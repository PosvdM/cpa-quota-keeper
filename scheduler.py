#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import watcher

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_USER_AGENT = "codex-tui/0.154.0 (Mac OS 26.5.2; arm64) iTerm.app/3.6.11 (codex-tui; 0.154.0)"
CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

IGNITE_ENABLED = os.getenv("IGNITE_ENABLED", "true").lower() not in {"0", "false", "no", "off"}
IGNITE_START_HOUR = max(0, min(23, int(os.getenv("IGNITE_START_HOUR", "7"))))
IGNITE_END_HOUR = max(0, min(23, int(os.getenv("IGNITE_END_HOUR", "22"))))
IGNITE_END_GRACE_MINUTES = max(0, int(os.getenv("IGNITE_END_GRACE_MINUTES", "30")))
IGNITE_GRACE_SECONDS = max(0, int(os.getenv("IGNITE_GRACE_SECONDS", "3")))
IGNITE_FAILURE_RETRY_SECONDS = max(60, int(os.getenv("IGNITE_FAILURE_RETRY_SECONDS", "300")))
IGNITE_POST_SUCCESS_HOLD_SECONDS = max(15, int(os.getenv("IGNITE_POST_SUCCESS_HOLD_SECONDS", "60")))
IGNITE_CODEX_MODEL = os.getenv("IGNITE_CODEX_MODEL", "").strip()
IGNITE_CLAUDE_MODEL = os.getenv("IGNITE_CLAUDE_MODEL", "").strip()

TRIGGER_PROMPT = (
    "This is an automated quota-window trigger. "
    "Reply with exactly OK. No explanation. Do not use tools or perform any other task."
)


def credential_id(file):
    return f"{watcher.provider_of(file)}:{watcher.auth_index_of(file)}"


def scalar(obj, *names):
    if not isinstance(obj, dict):
        return None
    for name in names:
        value = obj.get(name)
        if value is not None:
            return value
    return None


def datetime_from_reset(value, now=None):
    now = now or datetime.now(timezone.utc)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
            return datetime_from_reset(number, now)
        except ValueError:
            return watcher.parse_time(text)
    return None


def reset_iso(window, now=None):
    now = now or datetime.now(timezone.utc)
    raw = scalar(window, "reset_at", "resetAt")
    dt = datetime_from_reset(raw, now)
    if not dt:
        offset = scalar(window, "reset_after_seconds", "resetAfterSeconds")
        try:
            dt = now + timedelta(seconds=float(offset))
        except (TypeError, ValueError):
            return None
    return dt.astimezone(timezone.utc).isoformat()


def codex_window(window, wid, label, now):
    if not isinstance(window, dict):
        return None
    used_raw = scalar(window, "used_percent", "usedPercent")
    try:
        used = float(used_raw)
    except (TypeError, ValueError):
        used = 100.0 if scalar(window, "limit_reached", "limitReached") else 0.0
    return {
        "id": wid,
        "label": label,
        "remaining": watcher.clamp_percent(100.0 - used),
        "reset": reset_iso(window, now),
    }


def classify_codex_windows(rate_limit):
    primary = scalar(rate_limit, "primary_window", "primaryWindow")
    secondary = scalar(rate_limit, "secondary_window", "secondaryWindow")
    windows = [w for w in (primary, secondary) if isinstance(w, dict)]
    five_hour = None
    weekly = None
    for window in windows:
        seconds = scalar(window, "limit_window_seconds", "limitWindowSeconds")
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            seconds = 0
        if seconds == 18000 and five_hour is None:
            five_hour = window
        elif seconds == 604800 and weekly is None:
            weekly = window
    if five_hour is None:
        five_hour = primary if isinstance(primary, dict) and primary is not weekly else None
    if weekly is None:
        weekly = secondary if isinstance(secondary, dict) and secondary is not five_hour else None
    return five_hour, weekly


def codex_headers(file):
    headers = {
        "Authorization": "Bearer $TOKEN$",
        "Content-Type": "application/json",
        "User-Agent": CODEX_USER_AGENT,
    }
    metadata = file.get("metadata") if isinstance(file.get("metadata"), dict) else {}
    account_id = (
        file.get("chatgpt_account_id")
        or file.get("chatgptAccountId")
        or metadata.get("chatgpt_account_id")
        or metadata.get("chatgptAccountId")
    )
    if account_id:
        headers["Chatgpt-Account-Id"] = str(account_id)
    return headers


def fetch_codex_groups(client, file):
    idx = watcher.auth_index_of(file)
    if not idx:
        raise RuntimeError("Codex 凭证缺少 auth_index")
    payload = watcher.parse_jsonish(
        client.api_call(idx, "GET", CODEX_USAGE_URL, codex_headers(file))
    )
    rate_limit = scalar(payload, "rate_limit", "rateLimit")
    if not isinstance(rate_limit, dict):
        raise RuntimeError("Codex 未返回 rate_limit")
    now = datetime.now(timezone.utc)
    five_hour, weekly = classify_codex_windows(rate_limit)
    windows = []
    parsed = codex_window(five_hour, "five-hour", "5 小时", now)
    if parsed:
        windows.append(parsed)
    parsed = codex_window(weekly, "seven-day", "7 Day", now)
    if parsed:
        windows.append(parsed)
    if not windows:
        raise RuntimeError("Codex 未返回可识别的额度窗口")
    return [{"key": "codex:main", "provider": "Codex", "label": "Codex", "windows": windows}]


def provider_title(provider):
    return {"codex": "Codex", "claude": "Claude", "antigravity": "Antigravity"}.get(
        provider, provider.title()
    )


def decorate_groups(groups, file, ordinal, provider_count):
    provider = watcher.provider_of(file)
    idx = watcher.auth_index_of(file)
    account_suffix = f" #{ordinal}" if provider_count > 1 else ""
    out = []
    for group in groups:
        copied = dict(group)
        copied["key"] = f"{provider}:{idx}:{group['key']}"
        base_label = str(group.get("label") or provider_title(provider))
        copied["label"] = f"{base_label}{account_suffix}"
        copied["credential_id"] = f"{provider}:{idx}"
        out.append(copied)
    return out


def five_hour_window(groups):
    for group in groups:
        for window in group.get("windows", []):
            if window.get("id") == "five-hour" or watcher.short_window_label(window.get("label")) == "5h":
                return window
    return None


def collect(client):
    files = [
        f
        for f in client.auth_files()
        if not f.get("disabled") and watcher.provider_of(f) in {"codex", "claude", "antigravity"}
    ]
    counts = {}
    for file in files:
        provider = watcher.provider_of(file)
        counts[provider] = counts.get(provider, 0) + 1
    ordinals = {}
    groups = []
    accounts = []
    errors = []

    for file in files:
        provider = watcher.provider_of(file)
        ordinals[provider] = ordinals.get(provider, 0) + 1
        ordinal = ordinals[provider]
        try:
            if provider == "codex":
                raw_groups = fetch_codex_groups(client, file)
            elif provider == "claude":
                raw_groups = watcher.fetch_claude_groups(client, file)
            else:
                raw_groups = watcher.fetch_antigravity_groups(client, file)
            decorated = decorate_groups(raw_groups, file, ordinal, counts[provider])
            groups.extend(decorated)
            if provider in {"codex", "claude"}:
                window = five_hour_window(decorated)
                if window:
                    accounts.append(
                        {
                            "id": credential_id(file),
                            "provider": provider,
                            "label": f"{provider_title(provider)}{f' #{ordinal}' if counts[provider] > 1 else ''}",
                            "file": file,
                            "remaining": window.get("remaining"),
                            "reset": window.get("reset"),
                        }
                    )
        except Exception as exc:
            errors.append(f"{provider_title(provider)}{f' #{ordinal}' if counts[provider] > 1 else ''}: {exc}")
    return groups, accounts, errors


def models_for(client, file):
    name = file.get("name") or watcher.auth_index_of(file)
    query = urllib.parse.urlencode({"name": name})
    payload = client.management(f"/auth-files/models?{query}")
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    return [m.get("id") for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)]


def choose_model(client, account):
    provider = account["provider"]
    available = models_for(client, account["file"])
    override = IGNITE_CODEX_MODEL if provider == "codex" else IGNITE_CLAUDE_MODEL
    if override:
        if override not in available:
            raise RuntimeError(f"指定点火模型 {override} 不在该凭证模型列表中")
        return override

    if provider == "codex":
        preferred = ["gpt-6-luna", "gpt-5.6-luna"]
        for model in preferred:
            if model in available:
                return model
        for model in available:
            if "luna" in model.lower():
                return model
    else:
        preferred = ["claude-haiku-4-5-20251001", "claude-haiku-4-5"]
        for model in preferred:
            if model in available:
                return model
        for model in available:
            if "haiku" in model.lower():
                return model
    if not available:
        raise RuntimeError("该凭证没有可用模型")
    return available[0]


def ignite_codex(client, account, model):
    file = account["file"]
    headers = codex_headers(file)
    headers.update({"Accept": "text/event-stream", "Originator": "codex-tui"})
    body = {
        "model": model,
        "instructions": "Reply with exactly OK. No explanation. Do not use tools.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": TRIGGER_PROMPT}]}],
        "reasoning": {"effort": "none"},
        "tools": [],
        "stream": True,
        "store": False,
    }
    raw = client.api_call(
        watcher.auth_index_of(file),
        "POST",
        CODEX_RESPONSES_URL,
        headers,
        json.dumps(body, separators=(",", ":"), ensure_ascii=False),
    )
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    if "response.completed" not in text:
        raise RuntimeError("Codex 点火响应未完成")
    if "OK" not in text:
        raise RuntimeError("Codex 点火响应没有返回 OK")


def ignite_claude(client, account, model):
    file = account["file"]
    headers = {
        "Authorization": "Bearer $TOKEN$",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
        "User-Agent": "claude-cli/2.1.280 (external, cli)",
    }
    body = {
        "model": model,
        "max_tokens": 4,
        "messages": [{"role": "user", "content": TRIGGER_PROMPT}],
    }
    raw = client.api_call(
        watcher.auth_index_of(file),
        "POST",
        CLAUDE_MESSAGES_URL,
        headers,
        json.dumps(body, separators=(",", ":"), ensure_ascii=False),
    )
    payload = watcher.parse_jsonish(raw)
    text = "".join(
        part.get("text", "")
        for part in payload.get("content", [])
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    if payload.get("type") != "message" or not text.upper().startswith("OK"):
        raise RuntimeError("Claude 点火响应没有返回 OK")


def ignite(client, account):
    model = choose_model(client, account)
    if account["provider"] == "codex":
        ignite_codex(client, account, model)
    elif account["provider"] == "claude":
        ignite_claude(client, account, model)
    else:
        raise RuntimeError("不支持的点火 provider")
    return model


def scheduler_state(state, account_id):
    root = state.setdefault("scheduler", {})
    return root.setdefault(account_id, {})


def local_bounds(now_utc):
    local_now = now_utc.astimezone(watcher.LOCAL_TZ)
    start = local_now.replace(hour=IGNITE_START_HOUR, minute=0, second=0, microsecond=0)
    end = local_now.replace(hour=IGNITE_END_HOUR, minute=0, second=0, microsecond=0)
    end += timedelta(minutes=IGNITE_END_GRACE_MINUTES)
    if end <= start:
        end += timedelta(days=1)
    return local_now, start, end


def next_daily_start(now_utc):
    local_now = now_utc.astimezone(watcher.LOCAL_TZ)
    start = local_now.replace(hour=IGNITE_START_HOUR, minute=0, second=0, microsecond=0)
    if local_now >= start:
        start += timedelta(days=1)
    return start.astimezone(timezone.utc)


def due_at(account, state, now_utc):
    local_now, start, end = local_bounds(now_utc)
    if local_now < start:
        return start.astimezone(timezone.utc)
    if local_now > end:
        return next_daily_start(now_utc)

    reset_dt = watcher.parse_time(account.get("reset"))
    if reset_dt and reset_dt > now_utc:
        target = reset_dt + timedelta(seconds=IGNITE_GRACE_SECONDS)
        target_local = target.astimezone(watcher.LOCAL_TZ)
        if target_local.date() == local_now.date() and start <= target_local <= end:
            return target
        return next_daily_start(now_utc)

    item = scheduler_state(state, account["id"])
    last_success = float(item.get("last_success_epoch") or 0)
    if last_success and now_utc.timestamp() < last_success + IGNITE_POST_SUCCESS_HOLD_SECONDS:
        return datetime.fromtimestamp(last_success + IGNITE_POST_SUCCESS_HOLD_SECONDS, timezone.utc)
    retry_at = float(item.get("retry_at_epoch") or 0)
    if retry_at > now_utc.timestamp():
        return datetime.fromtimestamp(retry_at, timezone.utc)
    return now_utc


def schedule_summary(accounts, state):
    now = datetime.now(timezone.utc)
    parts = []
    for account in accounts:
        due = due_at(account, state, now)
        parts.append(f"{account['label']} → {due.astimezone(watcher.LOCAL_TZ).strftime('%m-%d %H:%M:%S')}")
    return "；".join(parts)


def process_groups(state, groups):
    if groups:
        watcher.log(watcher.summary(groups))
    for group in groups:
        watcher.process_group(state, group)
    watcher.save_state(state)


def poll(client, state):
    groups, accounts, errors = collect(client)
    if not groups:
        raise RuntimeError("；".join(errors) if errors else "没有读取到任何额度")
    process_groups(state, groups)
    if errors:
        watcher.log("部分额度刷新失败：" + "；".join(errors))
    if IGNITE_ENABLED and accounts:
        watcher.log("点火计划：" + schedule_summary(accounts, state))
    return accounts


def perform_due(client, state, accounts):
    if not IGNITE_ENABLED:
        return False
    now = datetime.now(timezone.utc)
    due_accounts = []
    for account in accounts:
        target = due_at(account, state, now)
        if target <= now + timedelta(milliseconds=250):
            due_accounts.append(account)
    if not due_accounts:
        return False

    attempted = False
    for account in due_accounts:
        attempted = True
        item = scheduler_state(state, account["id"])
        try:
            model = ignite(client, account)
            stamp = time.time()
            item["last_success_epoch"] = stamp
            item["last_model"] = model
            item["last_error"] = ""
            item["retry_at_epoch"] = 0
            watcher.log(f"窗口点火成功：{account['label']} · {model}")
        except Exception as exc:
            item["last_error"] = str(exc)[:500]
            item["last_failure_epoch"] = time.time()
            item["retry_at_epoch"] = time.time() + IGNITE_FAILURE_RETRY_SECONDS
            watcher.log(f"窗口点火失败：{account['label']}：{exc}")
    watcher.save_state(state)
    return attempted


def next_wakeup(accounts, state, next_poll_epoch):
    now = datetime.now(timezone.utc)
    times = [datetime.fromtimestamp(next_poll_epoch, timezone.utc)]
    if IGNITE_ENABLED:
        for account in accounts:
            times.append(due_at(account, state, now))
    target = min(times)
    return max(1.0, min(3600.0, (target - now).total_seconds()))


def main():
    parser = argparse.ArgumentParser(description="CPA quota watcher + quota-window scheduler")
    parser.add_argument("--once", action="store_true", help="只刷新一次额度，不发送点火请求")
    parser.add_argument("--show-schedule", action="store_true", help="刷新额度并打印下一次点火时间")
    args = parser.parse_args()

    key = watcher.parse_management_key()
    client = watcher.CPAClient(key)
    state = watcher.load_state()

    if args.once or args.show_schedule:
        accounts = poll(client, state)
        if args.show_schedule and accounts:
            print(schedule_summary(accounts, state))
        return

    watcher.log(
        f"启动：每 {watcher.POLL_INTERVAL}s 刷新额度；点火={'开启' if IGNITE_ENABLED else '关闭'}；"
        f"每日 {IGNITE_START_HOUR:02d}:00 起跑，{IGNITE_END_HOUR:02d}:00 后保留 "
        f"{IGNITE_END_GRACE_MINUTES} 分钟漂移窗口"
    )

    accounts = []
    next_poll = 0.0
    while True:
        now_epoch = time.time()
        if now_epoch >= next_poll or not accounts:
            try:
                accounts = poll(client, state)
            except Exception as exc:
                watcher.log(f"额度刷新失败：{exc}")
            next_poll = time.time() + watcher.POLL_INTERVAL

        if accounts:
            attempted = perform_due(client, state, accounts)
            if attempted:
                # 触发成功后立即刷新真实 reset_at；失败则由 retry_at 控制下一次尝试。
                try:
                    accounts = poll(client, state)
                except Exception as exc:
                    watcher.log(f"点火后额度刷新失败：{exc}")
                next_poll = time.time() + watcher.POLL_INTERVAL

        sleep_for = next_wakeup(accounts, state, next_poll) if accounts else min(60, watcher.POLL_INTERVAL)
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        watcher.log(f"致命错误：{exc}")
        sys.exit(1)
