#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

CPA_BASE_URL = os.getenv("CPA_BASE_URL", "http://cliproxyapi:8317/v0/management").rstrip("/")
CPA_CONFIG_FILE = os.getenv("CPA_CONFIG_FILE", "/run/cliproxyapi/config.yaml")
STATE_FILE = Path(os.getenv("STATE_FILE", "/data/state.json"))
BARK_URL = os.getenv("BARK_URL", "").strip().rstrip("/")
BARK_GROUP = os.getenv("BARK_GROUP", "CPA")
BARK_ICON = os.getenv(
    "BARK_ICON",
    "https://raw.githubusercontent.com/router-for-me/Cli-Proxy-API-Management-Center/main/logo.jpg",
).strip()
POLL_INTERVAL = max(60, int(os.getenv("POLL_INTERVAL", "300")))
NOTICE_THRESHOLD = float(os.getenv("NOTICE_THRESHOLD", "50"))
LOW_THRESHOLD = float(os.getenv("LOW_THRESHOLD", "20"))
CRITICAL_THRESHOLD = float(os.getenv("CRITICAL_THRESHOLD", "10"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "20"))
NOTIFY_RECOVERY = os.getenv("NOTIFY_RECOVERY", "true").lower() not in {"0", "false", "no", "off"}
NOTIFY_RESET_REMINDERS = os.getenv("NOTIFY_RESET_REMINDERS", "true").lower() not in {"0", "false", "no", "off"}
TZ_OFFSET_HOURS = float(os.getenv("TZ_OFFSET_HOURS", "8"))
LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))
RESET_ID_TOLERANCE_SECONDS = 10

ANTIGRAVITY_URLS = [
    "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
]
ANTIGRAVITY_HEADERS = {
    "Authorization": "Bearer $TOKEN$",
    "Content-Type": "application/json",
    "User-Agent": "antigravity/cli/1.0.13 (aidev_client; os_type=darwin; arch=arm64)",
}
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_HEADERS = {
    "Authorization": "Bearer $TOKEN$",
    "Content-Type": "application/json",
    "anthropic-beta": "oauth-2025-04-20",
}
CLAUDE_WINDOWS = [
    ("five_hour", "five-hour", "5 小时"),
    ("seven_day", "seven-day", "7 Day"),
    ("seven_day_oauth_apps", "seven-day-oauth-apps", "7 Day OAuth Apps"),
    ("seven_day_opus", "seven-day-opus", "7 Day Opus"),
    ("seven_day_sonnet", "seven-day-sonnet", "7 Day Sonnet"),
    ("seven_day_cowork", "seven-day-cowork", "7 Day Cowork"),
    ("iguana_necktie", "seven-day-fable", "7 Day Fable 5"),
]

SEVERITY_RANK = {"normal": 0, "notice": 1, "low": 2, "critical": 3, "exhausted": 4}


def log(msg):
    stamp = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def normalize_scalar(value):
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def parse_management_key():
    env_key = (os.getenv("CPA_MANAGEMENT_KEY", "").strip() or os.getenv("MANAGEMENT_PASSWORD", "").strip())
    if env_key:
        return env_key

    try:
        lines = Path(CPA_CONFIG_FILE).read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        raise RuntimeError(f"无法读取 CPA 配置文件 {CPA_CONFIG_FILE}: {exc}") from exc

    in_remote = False
    base_indent = 0
    for line in lines:
        if re.match(r"^remote-management\s*:", line):
            in_remote = True
            base_indent = len(line) - len(line.lstrip())
            continue
        if not in_remote:
            continue

        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped and not stripped.startswith("#") and indent <= base_indent:
            break

        match = re.match(r"^\s*secret-key\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value:
            break
        if value.startswith("$2"):
            raise RuntimeError("CPA config 中的 management secret-key 已是 bcrypt 哈希；请通过 CPA_MANAGEMENT_KEY 环境变量提供明文管理密钥。")
        return value

    raise RuntimeError("未找到可用的 CPA management secret-key")


def http_json(url, method="GET", headers=None, data=None, timeout=None):
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"请求失败: {exc.reason}") from exc


class CPAClient:
    def __init__(self, key):
        self.key = key

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.key}"}

    def management(self, path, method="GET", data=None):
        return http_json(f"{CPA_BASE_URL}{path}", method=method, headers=self.headers, data=data)

    def auth_files(self):
        payload = self.management("/auth-files")
        files = payload.get("files")
        return files if isinstance(files, list) else []

    def download_auth_file(self, name):
        url = f"{CPA_BASE_URL}/auth-files/download?{urllib.parse.urlencode({'name': name})}"
        req = urllib.request.Request(url, headers={**self.headers, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return None

    def api_call(self, auth_index, method, url, headers, data=None):
        payload = {
            "auth_index": auth_index,
            "method": method,
            "url": url,
            "header": headers,
        }
        if data is not None:
            payload["data"] = data
        result = self.management("/api-call", method="POST", data=payload)
        status = int(result.get("status_code") or result.get("statusCode") or 0)
        body = result.get("body")
        if body is None:
            body = result.get("bodyText")
        if status < 200 or status >= 300:
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            raise RuntimeError(f"上游 HTTP {status}: {text[:300]}")
        return body


def parse_jsonish(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("上游返回不是 JSON object")


def auth_index_of(file):
    value = file.get("auth_index") or file.get("authIndex")
    return str(value).strip() if value is not None and str(value).strip() else ""


def provider_of(file):
    raw = normalize_scalar(file.get("provider") or file.get("type")) or ""
    name = (normalize_scalar(file.get("name")) or "").lower()
    provider = raw.lower()
    if provider:
        return provider
    if name.startswith("claude-"):
        return "claude"
    if name.startswith("antigravity-"):
        return "antigravity"
    return ""


def clamp_percent(value):
    return max(0.0, min(100.0, float(value)))


def parse_time(value):
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def same_reset_cycle(left, right):
    if not left or not right:
        return False
    if left == right:
        return True
    left_dt = parse_time(left)
    right_dt = parse_time(right)
    if not left_dt or not right_dt:
        return False
    return abs((left_dt - right_dt).total_seconds()) <= RESET_ID_TOLERANCE_SECONDS


def format_duration(seconds):
    if seconds <= 0:
        return "可刷新"
    if seconds >= 86400:
        return f"{max(1, int(seconds / 86400 + 0.5))}天后"
    if seconds >= 3600:
        return f"{max(1, int(seconds / 3600 + 0.5))}小时后"
    return f"{max(1, int(seconds / 60 + 0.5))}分钟后"


def format_compact_duration(seconds):
    if seconds <= 0:
        return "可刷新"
    if seconds >= 86400:
        return f"{max(1, int(seconds / 86400 + 0.5)):02d}d"
    if seconds >= 3600:
        return f"{max(1, int(seconds / 3600 + 0.5)):02d}h"
    return f"{max(1, int(seconds / 60 + 0.5)):02d}分"


def format_reset(value):
    dt = parse_time(value)
    if not dt:
        return ""
    local = dt.astimezone(LOCAL_TZ)
    duration = format_duration((dt - datetime.now(timezone.utc)).total_seconds())
    return f"{duration} · {local.strftime('%m-%d %H:%M')}"


def severity(remaining):
    if remaining <= 0.01:
        return "exhausted"
    if remaining <= CRITICAL_THRESHOLD:
        return "critical"
    if remaining <= LOW_THRESHOLD:
        return "low"
    if remaining <= NOTICE_THRESHOLD:
        return "notice"
    return "normal"


def severity_label(value):
    return {
        "normal": "正常",
        "notice": f"≤{NOTICE_THRESHOLD:g}%",
        "low": f"≤{LOW_THRESHOLD:g}%",
        "critical": f"≤{CRITICAL_THRESHOLD:g}%",
        "exhausted": "已耗尽",
    }[value]


def find_fable_limit(payload):
    limits = payload.get("limits")
    if not isinstance(limits, list):
        return None
    candidates = []
    for item in limits:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        model_name = str(model.get("display_name") or "").strip().lower()
        if kind == "weekly_scoped" and model_name in {"fable", "fable 5"} and item.get("percent") is not None:
            candidates.append(item)
    if not candidates:
        return None
    return next((x for x in candidates if x.get("is_active") is True), candidates[0])


def fetch_claude_groups(client, file):
    idx = auth_index_of(file)
    if not idx:
        raise RuntimeError("Claude 凭证缺少 auth_index")
    payload = parse_jsonish(client.api_call(idx, "GET", CLAUDE_USAGE_URL, CLAUDE_HEADERS))

    fable_limit = find_fable_limit(payload)
    windows = []
    for key, wid, label in CLAUDE_WINDOWS:
        if key == "iguana_necktie" and fable_limit:
            continue
        item = payload.get(key)
        if not isinstance(item, dict) or item.get("utilization") is None:
            continue
        try:
            used = float(item["utilization"])
        except (TypeError, ValueError):
            continue
        windows.append({
            "id": wid,
            "label": label,
            "remaining": clamp_percent(100.0 - used),
            "reset": item.get("resets_at"),
        })

    if fable_limit:
        try:
            used = float(fable_limit["percent"])
            windows.append({
                "id": "seven-day-fable",
                "label": "7 Day Fable 5",
                "remaining": clamp_percent(100.0 - used),
                "reset": fable_limit.get("resets_at"),
            })
        except (TypeError, ValueError):
            pass

    if not windows:
        raise RuntimeError("Claude 未返回可识别的额度窗口")

    groups = []
    main = [w for w in windows if w["id"] in {"five-hour", "seven-day"}]
    if main:
        groups.append({"key": "claude:main", "provider": "Claude", "label": "Claude", "windows": main})
    for w in windows:
        if w["id"] in {"five-hour", "seven-day"}:
            continue
        groups.append({
            "key": f"claude:{w['id']}",
            "provider": "Claude",
            "label": "Fable" if w["id"] == "seven-day-fable" else "Claude",
            "windows": [w],
        })
    return groups


def nested_project_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("project_id", "projectId"):
        val = normalize_scalar(obj.get(key))
        if val:
            return val
    for child_key in ("metadata", "attributes", "installed", "web"):
        child = obj.get(child_key)
        if isinstance(child, dict):
            for key in ("project_id", "projectId", "gemini_virtual_project"):
                val = normalize_scalar(child.get(key))
                if val:
                    return val
    return ""


def resolve_project_id(client, file):
    value = nested_project_id(file)
    if value:
        return value
    name = normalize_scalar(file.get("name"))
    if name:
        downloaded = client.download_auth_file(name)
        value = nested_project_id(downloaded)
        if value:
            return value
    raise RuntimeError("Antigravity 凭证缺少 project_id")


def antigravity_group_label(raw):
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    low = text.lower()
    if low == "gemini models":
        return "Gemini"
    if low == "claude and gpt models":
        return "Claude / GPT"
    return text or "Quota Group"


def antigravity_bucket_label(bucket):
    window = str(bucket.get("window") or "").strip().lower()
    raw = str(bucket.get("displayName") or bucket.get("display_name") or "").strip()
    low = raw.lower()
    if window in {"5h", "five-hour", "five_hour"} or low in {"5 hour limit", "5-hour limit", "five hour limit"}:
        return "5 小时"
    if window in {"weekly", "week"} or low == "weekly limit":
        return "7 Day"
    return raw or window or "额度"


def parse_fraction(value):
    if isinstance(value, str):
        s = value.strip()
        if s.endswith("%"):
            return float(s[:-1].strip()) / 100.0
        value = float(s)
    value = float(value)
    if value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def fetch_antigravity_groups(client, file):
    idx = auth_index_of(file)
    if not idx:
        raise RuntimeError("Antigravity 凭证缺少 auth_index")
    project_id = resolve_project_id(client, file)
    request_body = json.dumps({"project": project_id}, separators=(",", ":"))
    last_error = None
    payload = None
    for url in ANTIGRAVITY_URLS:
        try:
            payload = parse_jsonish(client.api_call(idx, "POST", url, ANTIGRAVITY_HEADERS, request_body))
            if isinstance(payload.get("body"), (dict, str)) and not isinstance(payload.get("groups"), list):
                payload = parse_jsonish(payload["body"])
            if isinstance(payload.get("groups"), list):
                break
            last_error = RuntimeError("Antigravity 返回中没有 groups")
        except Exception as exc:
            last_error = exc
            payload = None
    if not payload or not isinstance(payload.get("groups"), list):
        raise last_error or RuntimeError("Antigravity 未返回额度组")

    groups = []
    for gi, group in enumerate(payload["groups"]):
        if not isinstance(group, dict):
            continue
        raw_label = group.get("displayName") or group.get("display_name")
        label = antigravity_group_label(raw_label)
        windows = []
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bi, bucket in enumerate(buckets):
            if not isinstance(bucket, dict):
                continue
            raw_fraction = bucket.get("remainingFraction")
            if raw_fraction is None:
                raw_fraction = bucket.get("remaining_fraction")
            if raw_fraction is None:
                continue
            try:
                remaining = parse_fraction(raw_fraction) * 100.0
            except (TypeError, ValueError):
                continue
            window_raw = str(bucket.get("window") or "").strip().lower()
            bid = normalize_scalar(bucket.get("bucketId") or bucket.get("bucket_id"))
            if not bid:
                bid = f"{window_raw or 'bucket'}-{bi+1}"
            windows.append({
                "id": bid,
                "label": antigravity_bucket_label(bucket),
                "remaining": clamp_percent(remaining),
                "reset": bucket.get("resetTime") or bucket.get("reset_time"),
                "order": 0 if window_raw in {"5h", "five-hour", "five_hour"} else 1 if window_raw in {"weekly", "week"} else 9,
            })
        if windows:
            windows.sort(key=lambda w: (w.pop("order"), w["label"]))
            safe_key = re.sub(r"[^a-z0-9]+", "-", str(raw_label or label).lower()).strip("-") or f"group-{gi+1}"
            groups.append({
                "key": f"antigravity:{safe_key}",
                "provider": "Antigravity",
                "label": label,
                "windows": windows,
            })
    if not groups:
        raise RuntimeError("Antigravity 未返回可识别的额度窗口")
    return groups


def load_state():
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"version": 1, "groups": {}}
    except FileNotFoundError:
        return {"version": 1, "groups": {}}
    except Exception as exc:
        log(f"状态文件读取失败，将重建：{exc}")
        return {"version": 1, "groups": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def short_window_label(label):
    return {
        "5 小时": "5h",
        "7 Day": "7d",
        "7 Day Fable 5": "7d",
    }.get(label, label)


def format_window_lines(window, recovered=False, index=0):
    label = short_window_label(window["label"])
    if recovered:
        value = "已恢复"
    else:
        value = f"{round(window['remaining'])}%"
    dt = parse_time(window.get("reset"))
    if not dt:
        return [f"{label}：{value}"]
    local = dt.astimezone(LOCAL_TZ)
    reset = format_compact_duration((dt - datetime.now(timezone.utc)).total_seconds())
    return [f"{label}：{value} | {reset} | {local.strftime('%m/%d %H:%M')}"]


def build_notification(group, changes):
    worsening = [c for c in changes if c["direction"] == "down"]
    recovering = [c for c in changes if c["direction"] == "up"]
    if worsening:
        worst = max(worsening, key=lambda c: SEVERITY_RANK[c["to"]])
        labels = " / ".join(
            f"{short_window_label(c['label'])} {round(c['remaining'])}%"
            for c in worsening
        )
        prefix = "🔴" if worst["to"] in {"critical", "exhausted"} else "⚠️"
        title = f"{prefix} {group['label']} · {labels}"
        level = "timeSensitive" if worst["to"] in {"critical", "exhausted"} else "active"
    else:
        labels = " / ".join(short_window_label(c["label"]) for c in recovering)
        title = f"✅ {group['label']} · {labels} 已恢复"
        level = "active"

    lines = []
    for index, window in enumerate(group["windows"]):
        lines.extend(format_window_lines(window, index=index))
    return title, "\n".join(lines), level


def build_reset_notification(group, reminders):
    labels = " / ".join(short_window_label(item["label"]) for item in reminders)
    title = f"⏰ {group['label']} · {labels} 重置提醒"
    body = "\n".join(
        format_window_lines(window, index=index)[0]
        for index, window in enumerate(group["windows"])
    )
    return title, body, "active"


def is_seven_day_window(window):
    label = str(window.get("label") or "")
    return short_window_label(label) == "7d" or label.startswith("7 Day")


def detect_reset_recovery(old, window, now, current_severity):
    pending = old.get("pending_reset_recovery_for")
    if pending and current_severity == "normal":
        if not same_reset_cycle(old.get("reset_recovery_for"), pending):
            return pending

    if current_severity != "normal":
        return None

    cycle = old.get("reset_notice_1h_for") or old.get("reset_notice_for")
    if not cycle or same_reset_cycle(old.get("reset_recovery_for"), cycle):
        return None

    target = parse_time(cycle)
    if not target or now < target:
        return None

    old_reset = old.get("reset")
    current_reset = window.get("reset")
    if old_reset and same_reset_cycle(old_reset, cycle):
        if not current_reset or not same_reset_cycle(current_reset, cycle):
            return cycle
    return None


def send_bark(title, body, level):
    if not BARK_URL:
        log(f"Bark 未配置，跳过推送：{title}")
        return False

    path = "/".join([urllib.parse.quote(title, safe=""), urllib.parse.quote(body, safe="")])
    params = {"group": BARK_GROUP, "level": level}
    if BARK_ICON:
        params["icon"] = BARK_ICON
    url = f"{BARK_URL}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "cpa-quota-keeper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Bark HTTP {resp.status}: {raw[:200]}")
            try:
                payload = json.loads(raw) if raw.strip() else {}
                code = payload.get("code")
                if code not in (None, 200):
                    raise RuntimeError(f"Bark 返回 code={code}: {raw[:200]}")
            except json.JSONDecodeError:
                pass
        log(f"Bark 推送成功：{title}")
        return True
    except Exception as exc:
        log(f"Bark 推送失败：{exc}")
        return False


def process_group(state, group):
    groups_state = state.setdefault("groups", {})
    prev_group = groups_state.setdefault(group["key"], {"windows": {}})
    prev_windows = prev_group.setdefault("windows", {})
    changes = []
    reset_reminders = []
    now = datetime.now(timezone.utc)

    for window in group["windows"]:
        wid = window["id"]
        current = severity(window["remaining"])
        old = prev_windows.get(wid, {})
        notified = old.get("notified_severity", "normal")
        notified_rank = SEVERITY_RANK.get(notified, 0)
        current_rank = SEVERITY_RANK[current]
        reset_recovery_for = detect_reset_recovery(old, window, now, current)

        direction = None
        if current_rank > notified_rank:
            direction = "down"
        elif NOTIFY_RECOVERY and notified_rank > 0 and current_rank < notified_rank:
            direction = "up"
        elif NOTIFY_RECOVERY and reset_recovery_for:
            direction = "up"

        if direction:
            change = {
                "id": wid,
                "label": window["label"],
                "from": notified,
                "to": current,
                "direction": direction,
                "remaining": window["remaining"],
            }
            if direction == "up" and reset_recovery_for:
                change["reset_recovery_for"] = reset_recovery_for
            changes.append(change)

        reset_value = window.get("reset")
        reset_dt = parse_time(reset_value)
        if NOTIFY_RESET_REMINDERS and reset_dt and reset_value:
            seconds_until_reset = (reset_dt - now).total_seconds()
            one_hour_notified = (
                same_reset_cycle(old.get("reset_notice_1h_for"), reset_value)
                or same_reset_cycle(old.get("reset_notice_for"), reset_value)
            )
            if 0 < seconds_until_reset <= 3600 and not one_hour_notified:
                reset_reminders.append({
                    "id": wid,
                    "label": window["label"],
                    "remaining": window["remaining"],
                    "reset": reset_value,
                    "stage": "1h",
                })
            elif (
                is_seven_day_window(window)
                and 3600 < seconds_until_reset <= 86400
                and not same_reset_cycle(old.get("reset_notice_1d_for"), reset_value)
            ):
                reset_reminders.append({
                    "id": wid,
                    "label": window["label"],
                    "remaining": window["remaining"],
                    "reset": reset_value,
                    "stage": "1d",
                })

        prev_windows[wid] = {
            **old,
            "severity": current,
            "remaining": window["remaining"],
            "reset": window.get("reset"),
            "last_seen": int(time.time()),
        }
        if reset_recovery_for:
            prev_windows[wid]["pending_reset_recovery_for"] = reset_recovery_for

    if changes:
        title, body, level = build_notification(group, changes)
        if send_bark(title, body, level):
            has_worsening = any(change["direction"] == "down" for change in changes)
            for change in changes:
                prev_windows[change["id"]]["notified_severity"] = change["to"]
                if change.get("reset_recovery_for") and not has_worsening:
                    prev_windows[change["id"]]["reset_recovery_for"] = change["reset_recovery_for"]
                    prev_windows[change["id"]].pop("pending_reset_recovery_for", None)

    if reset_reminders:
        title, body, level = build_reset_notification(group, reset_reminders)
        if send_bark(title, body, level):
            for reminder in reset_reminders:
                if reminder["stage"] == "1h":
                    prev_windows[reminder["id"]]["reset_notice_1h_for"] = reminder["reset"]
                    prev_windows[reminder["id"]]["reset_notice_for"] = reminder["reset"]
                else:
                    prev_windows[reminder["id"]]["reset_notice_1d_for"] = reminder["reset"]

    prev_group["label"] = group["label"]
    prev_group["last_seen"] = int(time.time())


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def summary(groups):
    parts = []
    for group in groups:
        vals = ", ".join(f"{w['label']} {round(w['remaining'])}%" for w in group["windows"])
        parts.append(f"{group['label']}: {vals}")
    return " | ".join(parts)


def poll_once(client, state):
    files = client.auth_files()
    targets = [f for f in files if not f.get("disabled") and provider_of(f) in {"claude", "antigravity"}]
    if not targets:
        raise RuntimeError("没有找到可用的 Claude / Antigravity 凭证")

    all_groups = []
    errors = []
    for file in targets:
        provider = provider_of(file)
        try:
            if provider == "claude":
                all_groups.extend(fetch_claude_groups(client, file))
            elif provider == "antigravity":
                all_groups.extend(fetch_antigravity_groups(client, file))
        except Exception as exc:
            errors.append(f"{provider}: {exc}")

    if not all_groups:
        raise RuntimeError("；".join(errors) if errors else "没有读取到任何额度")

    log(summary(all_groups))
    for group in all_groups:
        process_group(state, group)
    save_state(state)

    if errors:
        log("部分额度刷新失败：" + "；".join(errors))
    return all_groups


def main():
    parser = argparse.ArgumentParser(description="CLIProxyAPI quota watcher -> Bark")
    parser.add_argument("--once", action="store_true", help="只轮询一次后退出")
    args = parser.parse_args()

    key = parse_management_key()
    client = CPAClient(key)
    state = load_state()

    if args.once:
        poll_once(client, state)
        return

    log(f"启动：每 {POLL_INTERVAL}s 刷新一次；阈值 {NOTICE_THRESHOLD:g}% / {LOW_THRESHOLD:g}% / {CRITICAL_THRESHOLD:g}% / 0%；Bark={'已配置' if BARK_URL else '未配置'}")
    while True:
        started = time.monotonic()
        try:
            poll_once(client, state)
        except Exception as exc:
            log(f"轮询失败：{exc}")
        elapsed = time.monotonic() - started
        time.sleep(max(1, POLL_INTERVAL - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log(f"致命错误：{exc}")
        sys.exit(1)
