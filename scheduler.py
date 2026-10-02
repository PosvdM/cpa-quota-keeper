#!/usr/bin/env python3
import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tomllib

import watcher

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_USER_AGENT = "codex-tui/0.154.0 (Mac OS 26.5.2; arm64) iTerm.app/3.6.11 (codex-tui; 0.154.0)"
CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTIGRAVITY_GENERATE_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent"
XAI_BILLING_URLS = [
    "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
    "https://cli-chat-proxy.grok.com/v1/billing",
]
XAI_CLI_RESPONSES_URL = "https://cli-chat-proxy.grok.com/v1/responses"
XAI_API_CHAT_URL = "https://api.x.ai/v1/chat/completions"
XAI_CLI_VERSION = "0.2.120"

KEEPER_CONFIG_FILE = Path(os.getenv("KEEPER_CONFIG", "/app/keeper.toml"))

DEFAULT_PROVIDER_CONFIG = {
    "codex": {"monitor": True, "ignite": True, "model": ""},
    "claude": {"monitor": True, "ignite": True, "model": ""},
    "antigravity": {"monitor": True, "ignite": False, "model": ""},
    "xai": {"monitor": True, "ignite": False, "model": ""},
}


def _bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def load_keeper_config():
    data = {}
    try:
        with KEEPER_CONFIG_FILE.open("rb") as fh:
            parsed = tomllib.load(fh)
            if isinstance(parsed, dict):
                data = parsed
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise RuntimeError(f"配置文件读取失败 {KEEPER_CONFIG_FILE}: {exc}") from exc
    return data


KEEPER_CONFIG = load_keeper_config()
WINDOW_CONFIG = KEEPER_CONFIG.get("window_ignition") if isinstance(KEEPER_CONFIG.get("window_ignition"), dict) else {}
PROVIDERS_CONFIG = KEEPER_CONFIG.get("providers") if isinstance(KEEPER_CONFIG.get("providers"), dict) else {}
RESET_UPDATES_CONFIG = (
    KEEPER_CONFIG.get("codex_reset_updates")
    if isinstance(KEEPER_CONFIG.get("codex_reset_updates"), dict)
    else {}
)


def _window_int(key, env_key, default, minimum=None, maximum=None):
    raw = WINDOW_CONFIG.get(key)
    if raw is None:
        raw = os.getenv(env_key, str(default))
    value = int(raw)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def provider_config(provider):
    provider = str(provider or "").strip().lower()
    aliases = [provider]
    if provider == "xai":
        aliases = ["xai", "grok"]
    merged = dict(DEFAULT_PROVIDER_CONFIG.get(provider, {"monitor": True, "ignite": False, "model": ""}))
    for key in aliases:
        raw = PROVIDERS_CONFIG.get(key)
        if isinstance(raw, dict):
            merged.update(raw)
    if provider == "codex" and not merged.get("model"):
        merged["model"] = os.getenv("IGNITE_CODEX_MODEL", "").strip()
    if provider == "claude" and not merged.get("model"):
        merged["model"] = os.getenv("IGNITE_CLAUDE_MODEL", "").strip()
    merged["monitor"] = _bool_value(merged.get("monitor"), True)
    merged["ignite"] = _bool_value(merged.get("ignite"), False)
    return merged


IGNITE_ENABLED = _bool_value(
    WINDOW_CONFIG.get("enabled"),
    os.getenv("IGNITE_ENABLED", "true").lower() not in {"0", "false", "no", "off"},
)
IGNITE_START_HOUR = _window_int("start_hour", "IGNITE_START_HOUR", 7, 0, 23)
IGNITE_END_HOUR = _window_int("end_hour", "IGNITE_END_HOUR", 22, 0, 23)
IGNITE_END_GRACE_MINUTES = _window_int("end_grace_minutes", "IGNITE_END_GRACE_MINUTES", 30, 0)
IGNITE_GRACE_SECONDS = _window_int("grace_seconds", "IGNITE_GRACE_SECONDS", 3, 0)
IGNITE_FAILURE_RETRY_SECONDS = _window_int("failure_retry_seconds", "IGNITE_FAILURE_RETRY_SECONDS", 300, 60)
IGNITE_POST_SUCCESS_HOLD_SECONDS = _window_int("post_success_hold_seconds", "IGNITE_POST_SUCCESS_HOLD_SECONDS", 60, 15)

CODEX_RESET_UPDATES_ENABLED = _bool_value(
    RESET_UPDATES_CONFIG.get("enabled"),
    os.getenv("CODEX_RESET_UPDATES_ENABLED", "false").lower() not in {"0", "false", "no", "off"},
)
CODEX_RESET_UPDATES_POLL_SECONDS = max(
    300,
    int(RESET_UPDATES_CONFIG.get("poll_seconds") or os.getenv("CODEX_RESET_UPDATES_POLL_SECONDS", "300")),
)
CODEX_RESET_UPDATES_NOTIFY_CURRENT_PENDING = _bool_value(
    RESET_UPDATES_CONFIG.get("notify_current_pending"),
    True,
)
DID_CODEX_RESET_API_URL = os.getenv(
    "DID_CODEX_RESET_API_URL",
    "https://didcodexreset.com/openapi/v1/records?kind=all&page=1&pageSize=10",
).strip()

# Some providers expose an unstarted 5-hour window as a rolling placeholder:
# reset_at stays about 5 hours ahead of now and moves forward with each poll.
# A real started window has a fixed reset_at even if the displayed remaining
# percentage still rounds to 100%. Detect the behavior from two observations.
ROLLING_RESET_WINDOW_SECONDS = 5 * 3600
ROLLING_RESET_OFFSET_TOLERANCE_SECONDS = 120
ROLLING_RESET_MIN_OBSERVATION_SECONDS = 2
ROLLING_RESET_DRIFT_TOLERANCE_SECONDS = 3
ROLLING_RESET_MAX_DRIFT_TOLERANCE_SECONDS = 10
IGNITE_CONFIRM_DELAYS_SECONDS = (5, 10, 15, 30, 15)

TRIGGER_PROMPT = (
    "This is an automated quota-window trigger. "
    "Do not think, reason, deliberate, use tools, or perform any other task. "
    "Reply with exactly OK and nothing else."
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


def _xai_user_id(file):
    containers = [file]
    for key in ("metadata", "attributes", "oauth", "user"):
        value = file.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in ("sub", "subject", "user_id", "userId", "id"):
            value = container.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _period_hours(start, end):
    start_dt = watcher.parse_time(start) if isinstance(start, str) else None
    end_dt = watcher.parse_time(end) if isinstance(end, str) else None
    if not start_dt or not end_dt:
        return None
    seconds = (end_dt - start_dt).total_seconds()
    if seconds <= 0:
        return None
    return seconds / 3600.0


def _window_label_from_hours(hours, fallback):
    if hours is not None and abs(hours - 5) <= 0.25:
        return "5 小时"
    if hours is not None and abs(hours - 168) <= 2:
        return "7 Day"
    return fallback


def fetch_xai_groups(client, file):
    idx = watcher.auth_index_of(file)
    if not idx:
        raise RuntimeError("Grok 凭证缺少 auth_index")
    headers = {
        "Authorization": "Bearer $TOKEN$",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-client-version": XAI_CLI_VERSION,
        "accept": "*/*",
        "user-agent": f"grok-pager/{XAI_CLI_VERSION} grok-shell/{XAI_CLI_VERSION} (macos; aarch64)",
    }
    user_id = _xai_user_id(file)
    if user_id:
        headers["x-userid"] = user_id

    windows = []
    errors = []
    for url in XAI_BILLING_URLS:
        try:
            payload = watcher.parse_jsonish(client.api_call(idx, "GET", url, headers))
        except Exception as exc:
            errors.append(exc)
            continue
        config = payload.get("config")
        if not isinstance(config, dict):
            continue
        period = config.get("currentPeriod") or config.get("current_period")
        if not isinstance(period, dict):
            period = {}
        start = period.get("start") or config.get("billingPeriodStart") or config.get("billing_period_start")
        end = period.get("end") or config.get("billingPeriodEnd") or config.get("billing_period_end")
        hours = _period_hours(start, end)
        period_type = str(period.get("type") or "").strip().lower().replace("_", "-")
        if hours is None and period_type in {"5h", "five-hour", "five hour"}:
            hours = 5.0
        if hours is None and period_type in {"weekly", "week"}:
            hours = 168.0
        used = config.get("creditUsagePercent")
        if used is None:
            used = config.get("credit_usage_percent")
        try:
            used = float(used) if used is not None else None
        except (TypeError, ValueError):
            used = None
        if used is None:
            products = config.get("productUsage") or config.get("product_usage") or []
            values = []
            if isinstance(products, list):
                for item in products:
                    if not isinstance(item, dict):
                        continue
                    value = item.get("usagePercent")
                    if value is None:
                        value = item.get("usage_percent")
                    try:
                        values.append(float(value))
                    except (TypeError, ValueError):
                        pass
            if values:
                used = max(values)
        if used is None or not end:
            continue
        fallback = "7 Day" if "format=credits" in url else "Monthly"
        label = _window_label_from_hours(hours, fallback)
        wid = "five-hour" if label == "5 小时" else "seven-day" if label == "7 Day" else "billing"
        windows.append({
            "id": wid,
            "label": label,
            "remaining": watcher.clamp_percent(100.0 - used),
            "reset": end,
            "period_hours": hours,
        })

    if not windows:
        if errors:
            raise errors[0]
        raise RuntimeError("Grok 未返回可识别的额度窗口")
    dedup = {}
    for window in windows:
        key = (window["id"], window.get("reset"))
        dedup[key] = window
    return [{"key": "xai:main", "provider": "Grok", "label": "Grok", "windows": list(dedup.values())}]


def provider_title(provider):
    return {
        "codex": "ChatGPT",
        "claude": "Claude",
        "antigravity": "Antigravity",
        "xai": "Grok",
    }.get(provider, provider.title())


def credential_email(file):
    for key in ("email", "account"):
        value = str(file.get(key) or "").strip()
        if "@" in value:
            return value

    name = str(file.get("name") or "")
    match = re.search(r"([A-Za-z0-9._%+]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,})", name)
    return match.group(1) if match else None


def masked_account_id(file, ordinal):
    email = credential_email(file)
    if not email:
        return str(ordinal)
    local = email.split("@", 1)[0]
    if len(local) >= 2:
        return local[-2:]
    return local or str(ordinal)


def credential_label(file, ordinal, provider_count):
    base = provider_title(watcher.provider_of(file))
    if provider_count > 1:
        return f"{base}#{masked_account_id(file, ordinal)}"
    return base


def decorate_groups(groups, file, ordinal, provider_count):
    provider = watcher.provider_of(file)
    idx = watcher.auth_index_of(file)
    cred_label = credential_label(file, ordinal, provider_count)
    out = []
    for group in groups:
        copied = dict(group)
        copied["key"] = f"{provider}:{idx}:{group['key']}"
        if provider_count == 1:
            copied["legacy_key"] = group["key"]
        base_label = str(group.get("label") or provider_title(provider))
        if provider == "codex":
            base_label = provider_title(provider)
        copied["source_label"] = base_label
        copied["label"] = (
            f"{base_label}#{masked_account_id(file, ordinal)}"
            if provider_count > 1
            else base_label
        )
        copied["credential_id"] = f"{provider}:{idx}"
        out.append(copied)
    return out


def is_five_hour_window(window):
    if not isinstance(window, dict):
        return False
    try:
        hours = float(window.get("period_hours"))
    except (TypeError, ValueError):
        hours = None
    if hours is not None and abs(hours - 5.0) <= 0.25:
        return True
    return window.get("id") == "five-hour" or watcher.short_window_label(window.get("label")) == "5h"


def five_hour_window(group):
    for window in group.get("windows", []):
        if is_five_hour_window(window):
            return window
    return None


QUOTA_FETCHERS = {
    "codex": fetch_codex_groups,
    "claude": watcher.fetch_claude_groups,
    "antigravity": watcher.fetch_antigravity_groups,
    "xai": fetch_xai_groups,
}


def fetch_groups_for_provider(client, file):
    provider = watcher.provider_of(file)
    adapter = QUOTA_FETCHERS.get(provider)
    if adapter is None:
        raise RuntimeError(f"不支持的额度 provider: {provider}")
    return adapter(client, file)


def group_ignition_enabled(provider, group):
    cfg = provider_config(provider)
    if not cfg.get("ignite"):
        return False
    allowed = cfg.get("groups")
    if not isinstance(allowed, list) or not allowed:
        return True
    label = str(group.get("source_label") or group.get("label") or "").strip().lower()
    return label in {str(item).strip().lower() for item in allowed}


def collect(client):
    supported = set(QUOTA_FETCHERS)
    files = []
    for file in client.auth_files():
        provider = watcher.provider_of(file)
        if file.get("disabled") or provider not in supported:
            continue
        if not provider_config(provider).get("monitor"):
            continue
        files.append(file)

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
            raw_groups = fetch_groups_for_provider(client, file)
            decorated = decorate_groups(raw_groups, file, ordinal, counts[provider])
            groups.extend(decorated)
            for group in decorated:
                window = five_hour_window(group)
                if not window or not group_ignition_enabled(provider, group):
                    continue
                accounts.append(
                    {
                        "id": f"{credential_id(file)}:{group['key']}",
                        "provider": provider,
                        "label": group["label"],
                        "group_label": group.get("source_label") or group["label"],
                        "file": file,
                        "remaining": window.get("remaining"),
                        "reset": window.get("reset"),
                    }
                )
        except Exception as exc:
            errors.append(f"{credential_label(file, ordinal, counts[provider])}: {exc}")
    return groups, accounts, errors


def models_for(client, file):
    name = file.get("name") or watcher.auth_index_of(file)
    query = urllib.parse.urlencode({"name": name})
    payload = client.management(f"/auth-files/models?{query}")
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    return [m.get("id") for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)]


def configured_model(account):
    cfg = provider_config(account["provider"])
    group = str(account.get("group_label") or "").strip()
    models = cfg.get("models")
    if isinstance(models, dict) and group:
        for key, value in models.items():
            if str(key).strip().lower() == group.lower() and str(value).strip():
                return str(value).strip()
    value = cfg.get("model")
    return str(value).strip() if value is not None else ""


def _first_matching(available, predicates):
    for predicate in predicates:
        for model in available:
            if predicate(model.lower()):
                return model
    return None


def choose_model(client, account):
    provider = account["provider"]
    available = models_for(client, account["file"])
    if not available:
        raise RuntimeError("该凭证没有可用模型")

    override = configured_model(account)
    if override:
        if override not in available:
            raise RuntimeError(f"指定点火模型 {override} 不在该凭证模型列表中")
        return override

    if provider == "codex":
        preferred = ["gpt-6-luna", "gpt-5.6-luna"]
        for model in preferred:
            if model in available:
                return model
        selected = _first_matching(available, [lambda m: "luna" in m])
        if selected:
            return selected

    if provider == "claude":
        preferred = ["claude-haiku-4-5-20251001", "claude-haiku-4-5"]
        for model in preferred:
            if model in available:
                return model
        selected = _first_matching(available, [lambda m: "haiku" in m])
        if selected:
            return selected

    if provider == "antigravity":
        group = str(account.get("group_label") or "").lower()
        candidates = [m for m in available if "image" not in m.lower()]
        if "gemini" in group:
            scoped = [m for m in candidates if "gemini" in m.lower()]
            selected = _first_matching(
                scoped,
                [
                    lambda m: "flash-lite" in m,
                    lambda m: "flash" in m,
                    lambda m: "pro" not in m,
                ],
            )
            if selected:
                return selected
        if "claude" in group or "gpt" in group:
            scoped = [m for m in candidates if "claude" in m.lower() or "gpt" in m.lower()]
            selected = _first_matching(
                scoped,
                [
                    lambda m: "haiku" in m and "thinking" not in m,
                    lambda m: "sonnet" in m and "thinking" not in m,
                    lambda m: "opus" in m and "thinking" not in m,
                    lambda m: "gpt-oss" in m,
                    lambda m: "thinking" not in m,
                ],
            )
            if selected:
                return selected
        if candidates:
            return candidates[0]

    if provider == "xai":
        candidates = [m for m in available if all(x not in m.lower() for x in ("image", "video"))]
        selected = _first_matching(
            candidates,
            [
                lambda m: "fast" in m,
                lambda m: "mini" in m,
                lambda m: "grok" in m,
            ],
        )
        if selected:
            return selected

    return available[0]


def ignite_codex(client, account, model):
    file = account["file"]
    headers = codex_headers(file)
    headers.update({"Accept": "text/event-stream", "Originator": "codex-tui"})
    body = {
        "model": model,
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


def ignite_antigravity(client, account, model):
    file = account["file"]
    project_id = watcher.resolve_project_id(client, file)
    request_id = "agent-" + str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    body = {
        "model": model,
        "project": project_id,
        "requestId": request_id,
        "requestType": "agent",
        "userAgent": "antigravity",
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": TRIGGER_PROMPT}],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 4,
                "temperature": 0,
            },
            "sessionId": session_id,
        },
    }
    raw = client.api_call(
        watcher.auth_index_of(file),
        "POST",
        ANTIGRAVITY_GENERATE_URL,
        dict(watcher.ANTIGRAVITY_HEADERS),
        json.dumps(body, separators=(",", ":"), ensure_ascii=False),
    )
    if not raw:
        raise RuntimeError("Antigravity 点火响应为空")


def _nested_auth_value(file, key):
    for container in (file, file.get("attributes"), file.get("metadata")):
        if isinstance(container, dict) and key in container and container.get(key) is not None:
            return container.get(key)
    return None


def _xai_using_api(file):
    raw = _nested_auth_value(file, "using_api")
    if raw is not None:
        return _bool_value(raw, False)
    auth_kind = str(_nested_auth_value(file, "auth_kind") or "").strip().lower()
    if auth_kind:
        return auth_kind != "oauth"
    return False


def ignite_xai(client, account, model):
    file = account["file"]
    idx = watcher.auth_index_of(file)
    if _xai_using_api(file):
        headers = {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": TRIGGER_PROMPT}],
            "max_tokens": 4,
            "stream": False,
        }
        raw = client.api_call(
            idx,
            "POST",
            XAI_API_CHAT_URL,
            headers,
            json.dumps(body, separators=(",", ":"), ensure_ascii=False),
        )
    else:
        headers = {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-grok-client-version": XAI_CLI_VERSION,
            "User-Agent": f"xai-grok-workspace/{XAI_CLI_VERSION}",
            "x-grok-client-identifier": "grok-shell",
            "x-authenticateresponse": "authenticate-response",
        }
        body = {
            "model": model,
            "input": TRIGGER_PROMPT,
            "max_output_tokens": 4,
            "stream": False,
        }
        raw = client.api_call(
            idx,
            "POST",
            XAI_CLI_RESPONSES_URL,
            headers,
            json.dumps(body, separators=(",", ":"), ensure_ascii=False),
        )
    if not raw:
        raise RuntimeError("Grok 点火响应为空")


IGNITE_ADAPTERS = {
    "codex": ignite_codex,
    "claude": ignite_claude,
    "antigravity": ignite_antigravity,
    "xai": ignite_xai,
}


def ignite(client, account):
    model = choose_model(client, account)
    adapter = IGNITE_ADAPTERS.get(account["provider"])
    if adapter is None:
        raise RuntimeError(f"不支持的点火 provider: {account['provider']}")
    adapter(client, account, model)
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


def observe_reset_behavior(state, account, now_utc):
    item = scheduler_state(state, account["id"])
    previous_flag = item.get("rolling_reset")
    previous = item.get("reset_observation")
    if not isinstance(previous, dict):
        previous = {}

    reset_dt = watcher.parse_time(account.get("reset"))
    try:
        remaining = float(account.get("remaining"))
    except (TypeError, ValueError):
        remaining = -1.0

    now_epoch = now_utc.timestamp()
    rolling = False
    reset_epoch = reset_dt.timestamp() if reset_dt else None
    if reset_epoch is not None:
        offset = reset_epoch - now_epoch
        previous_seen = previous.get("seen_epoch")
        previous_reset = previous.get("reset_epoch")
        try:
            previous_seen = float(previous_seen)
            previous_reset = float(previous_reset)
        except (TypeError, ValueError):
            previous_seen = previous_reset = None

        if (
            abs(offset - ROLLING_RESET_WINDOW_SECONDS) <= ROLLING_RESET_OFFSET_TOLERANCE_SECONDS
            and previous_seen is not None
            and previous_reset is not None
        ):
            elapsed = now_epoch - previous_seen
            reset_shift = reset_epoch - previous_reset
            if elapsed >= ROLLING_RESET_MIN_OBSERVATION_SECONDS:
                tolerance = min(
                    ROLLING_RESET_MAX_DRIFT_TOLERANCE_SECONDS,
                    max(ROLLING_RESET_DRIFT_TOLERANCE_SECONDS, elapsed * 0.02),
                )
                rolling = abs(reset_shift - elapsed) <= tolerance

    item["rolling_reset"] = rolling
    item["reset_observation"] = {
        "seen_epoch": now_epoch,
        "reset_epoch": reset_epoch,
        "remaining": remaining,
    }
    if rolling and previous_flag is not True:
        watcher.log(f"检测到滑动 5h 重置占位：{account['label']}，允许按日间窗口点火")
    elif previous_flag is True and not rolling:
        watcher.log(f"检测到固定 5h 重置时间：{account['label']}，恢复按 reset_at 调度")
    return rolling


def observe_accounts(state, accounts, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    for account in accounts:
        observe_reset_behavior(state, account, now_utc)


def held_until(item, now_utc):
    last_success = float(item.get("last_success_epoch") or 0)
    if last_success and now_utc.timestamp() < last_success + IGNITE_POST_SUCCESS_HOLD_SECONDS:
        return datetime.fromtimestamp(last_success + IGNITE_POST_SUCCESS_HOLD_SECONDS, timezone.utc)
    retry_at = float(item.get("retry_at_epoch") or 0)
    if retry_at > now_utc.timestamp():
        return datetime.fromtimestamp(retry_at, timezone.utc)
    return None


def due_at(account, state, now_utc):
    local_now, start, end = local_bounds(now_utc)
    if local_now < start:
        return start.astimezone(timezone.utc)
    if local_now > end:
        return next_daily_start(now_utc)

    item = scheduler_state(state, account["id"])
    hold = held_until(item, now_utc)
    if item.get("rolling_reset") is True:
        return hold or now_utc

    reset_dt = watcher.parse_time(account.get("reset"))
    if reset_dt and reset_dt > now_utc:
        target = reset_dt + timedelta(seconds=IGNITE_GRACE_SECONDS)
        target_local = target.astimezone(watcher.LOCAL_TZ)
        if target_local.date() == local_now.date() and start <= target_local <= end:
            return target
        return next_daily_start(now_utc)

    return hold or now_utc


def schedule_summary(accounts, state):
    now = datetime.now(timezone.utc)
    parts = []
    for account in accounts:
        due = due_at(account, state, now)
        parts.append(f"{account['label']} → {due.astimezone(watcher.LOCAL_TZ).strftime('%m-%d %H:%M:%S')}")
    return "；".join(parts)


def _reset_type_label(value):
    return {
        "global": "全局重置",
        "banked": "重置卡",
        "global_and_banked": "全局重置 + 重置卡",
    }.get(str(value or "").strip().lower(), "Codex 重置")


def _scope_label(scope):
    if not isinstance(scope, dict):
        return ""
    plans = scope.get("plans")
    if not isinstance(plans, list) or not plans:
        return ""
    normalized = [str(item).strip().lower() for item in plans if str(item).strip()]
    if "all" in normalized:
        return "全部套餐"
    names = {
        "plus": "Plus",
        "pro": "Pro",
        "business": "Business",
        "team": "Team",
        "enterprise": "Enterprise",
    }
    return " · ".join(names.get(item, item) for item in normalized)


def _format_reset_event_time(value):
    dt = watcher.parse_time(value)
    if not dt:
        return ""
    return dt.astimezone(watcher.LOCAL_TZ).strftime("%m/%d %H:%M")


def build_codex_reset_notification(record):
    kind = str(record.get("kind") or "").strip().lower()
    reset_type = _reset_type_label(record.get("resetType"))
    confidence = record.get("confidence")
    try:
        confidence_text = f"{round(float(confidence) * 100)}%"
    except (TypeError, ValueError):
        confidence_text = ""

    if kind == "reset_scheduled":
        title = f"📅 Codex {reset_type}已排期"
        time_text = _format_reset_event_time(record.get("effectiveAt"))
        lines = []
        if time_text:
            lines.append(f"预计：{time_text}")
        if confidence_text:
            lines.append(f"置信度：{confidence_text}")
    else:
        suffix = "已到账" if record.get("resetType") == "banked" else "已完成"
        title = f"✅ Codex {reset_type}{suffix}"
        time_text = _format_reset_event_time(
            record.get("effectiveAt") or record.get("completedAt") or record.get("announcedAt")
        )
        lines = []
        if time_text:
            lines.append(f"时间：{time_text}")
        if confidence_text:
            lines.append(f"置信度：{confidence_text}")

    scope_text = _scope_label(record.get("scope"))
    if scope_text:
        lines.append(f"范围：{scope_text}")
    return title, "\n".join(lines) or "Did Codex Reset 发布了新的重置信号", "active"


def codex_reset_detail_url(record):
    announced = watcher.parse_time(record.get("announcedAt"))
    if not announced:
        return None
    millis = int(round(announced.timestamp() * 1000))
    return f"https://didcodexreset.com/zh/history/{millis}.html"


def fetch_codex_reset_records():
    payload = watcher.http_json(
        DID_CODEX_RESET_API_URL,
        headers={"User-Agent": "cpa-quota-keeper/1.0", "Accept": "application/json"},
    )
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Did Codex Reset API 返回异常")
    data = payload.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("Did Codex Reset API 缺少 records")
    return [item for item in items if isinstance(item, dict) and str(item.get("id") or "").strip()]


def codex_reset_record_key(record):
    rid = str(record.get("id") or "").strip()
    if rid and not rid.startswith("manual:"):
        return f"id:{rid}"

    scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}
    plans = scope.get("plans") if isinstance(scope.get("plans"), list) else []
    windows = scope.get("windows") if isinstance(scope.get("windows"), list) else []
    parts = [
        str(record.get("kind") or ""),
        str(record.get("resetType") or ""),
        str(record.get("announcedAt") or ""),
        str(record.get("effectiveAt") or ""),
        str(record.get("completedAt") or ""),
        str(record.get("scheduleState") or ""),
        ",".join(sorted(str(item) for item in plans)),
        ",".join(sorted(str(item) for item in windows)),
    ]
    return "manual:" + "|".join(parts)


def process_codex_reset_records(state, records):
    root = state.setdefault("codex_reset_updates", {})
    initialized = bool(root.get("initialized"))
    old_seen_ids = {str(item) for item in root.get("seen_ids", []) if str(item)}
    seen_keys = {str(item) for item in root.get("seen_keys", []) if str(item)}
    candidates = []

    # Migration from the original ID-only dedupe. The upstream API can rotate IDs
    # for manual records while leaving the event itself unchanged, so ID-only
    # dedupe caused the same historical reset-card events to be pushed repeatedly.
    if initialized and not root.get("stable_key_migrated"):
        for record in records:
            rid = str(record.get("id") or "")
            if rid in old_seen_ids or rid.startswith("manual:"):
                seen_keys.add(codex_reset_record_key(record))
        root["stable_key_migrated"] = True

    if not initialized:
        root["initialized"] = True
        root["stable_key_migrated"] = True
        pending_key = None
        if CODEX_RESET_UPDATES_NOTIFY_CURRENT_PENDING:
            for record in records:
                if record.get("kind") == "reset_scheduled" and record.get("scheduleState") == "pending":
                    pending_key = codex_reset_record_key(record)
                    candidates.append(record)
                    break
        for record in records:
            key = codex_reset_record_key(record)
            if key and key != pending_key:
                seen_keys.add(key)
    else:
        candidates = [record for record in records if codex_reset_record_key(record) not in seen_keys]
        candidates.reverse()

    sent = 0
    for record in candidates:
        key = codex_reset_record_key(record)
        title, body, level = build_codex_reset_notification(record)
        jump_url = codex_reset_detail_url(record)
        if watcher.send_bark(title, body, level, jump_url=jump_url):
            seen_keys.add(key)
            sent += 1

    ordered_keys = []
    for record in records:
        key = codex_reset_record_key(record)
        if key in seen_keys and key not in ordered_keys:
            ordered_keys.append(key)
    for key in root.get("seen_keys", []):
        key = str(key)
        if key in seen_keys and key not in ordered_keys:
            ordered_keys.append(key)
    root["seen_keys"] = ordered_keys[:100]

    # Keep current upstream IDs for diagnostics/backward compatibility, but no
    # longer use them as the source of truth for deduplication.
    current_seen_ids = []
    for record in records:
        rid = str(record.get("id") or "")
        if rid and codex_reset_record_key(record) in seen_keys and rid not in current_seen_ids:
            current_seen_ids.append(rid)
    root["seen_ids"] = current_seen_ids[:100]
    root["last_check_epoch"] = int(time.time())
    return sent


def poll_codex_reset_updates(state):
    records = fetch_codex_reset_records()
    sent = process_codex_reset_records(state, records)
    watcher.save_state(state)
    if sent:
        watcher.log(f"Did Codex Reset：已发送 {sent} 条 Bark 通知")
    return sent


def process_groups(state, groups):
    groups_state = state.setdefault("groups", {})
    for group in groups:
        legacy_key = group.get("legacy_key")
        new_key = group["key"]
        if not legacy_key or legacy_key == new_key or legacy_key not in groups_state:
            continue
        old = groups_state[legacy_key]
        if new_key not in groups_state:
            groups_state[new_key] = copy.deepcopy(old)
            watcher.log(f"迁移旧通知状态：{legacy_key} → {new_key}")
        else:
            current = groups_state[new_key]
            old_windows = old.get("windows", {}) if isinstance(old, dict) else {}
            current_windows = current.setdefault("windows", {}) if isinstance(current, dict) else {}
            for wid, old_window in old_windows.items():
                current_window = current_windows.setdefault(wid, {})
                if isinstance(old_window, dict) and isinstance(current_window, dict):
                    for key, value in old_window.items():
                        current_window.setdefault(key, value)
        groups_state.pop(legacy_key, None)

    if groups:
        watcher.log(watcher.summary(groups))
    for group in groups:
        watcher.process_group(state, group)
    watcher.save_state(state)


def poll(client, state):
    groups, accounts, errors = collect(client)
    if not groups:
        raise RuntimeError("；".join(errors) if errors else "没有读取到任何额度")
    observe_accounts(state, accounts)
    process_groups(state, groups)
    if errors:
        watcher.log("部分额度刷新失败：" + "；".join(errors))
    if IGNITE_ENABLED and accounts:
        watcher.log("点火计划：" + schedule_summary(accounts, state))
    return accounts


def refresh_account(client, account):
    raw_groups = fetch_groups_for_provider(client, account["file"])
    target_label = str(account.get("group_label") or account.get("label") or "").strip().lower()
    candidates = []
    for group in raw_groups:
        window = five_hour_window(group)
        if not window:
            continue
        candidates.append((group, window))
        label = str(group.get("label") or "").strip().lower()
        if target_label and label != target_label:
            continue
        refreshed = dict(account)
        refreshed["remaining"] = window.get("remaining")
        refreshed["reset"] = window.get("reset")
        return refreshed
    if len(candidates) == 1:
        _, window = candidates[0]
        refreshed = dict(account)
        refreshed["remaining"] = window.get("remaining")
        refreshed["reset"] = window.get("reset")
        return refreshed
    raise RuntimeError(f"无法重新定位 5h 额度组：{account['label']}")


def confirm_ignition(client, state, account):
    latest = dict(account)
    for delay in IGNITE_CONFIRM_DELAYS_SECONDS:
        time.sleep(delay)
        latest = refresh_account(client, account)
        now = datetime.now(timezone.utc)
        rolling = observe_reset_behavior(state, latest, now)
        reset_dt = watcher.parse_time(latest.get("reset"))
        watcher.save_state(state)
        if not rolling and reset_dt and reset_dt > now:
            return latest
        watcher.log(
            f"等待点火确认：{account['label']} · "
            f"reset={'滑动' if rolling else latest.get('reset') or '未知'}"
        )
    raise RuntimeError("点火请求已返回，但未确认到新的固定 5h reset")


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
            watcher.log(f"点火请求成功：{account['label']} · {model}，等待 reset 确认")
            confirmed = confirm_ignition(client, state, account)
            stamp = time.time()
            item["last_success_epoch"] = stamp
            item["last_model"] = model
            item["last_error"] = ""
            item["retry_at_epoch"] = 0
            account["remaining"] = confirmed.get("remaining")
            account["reset"] = confirmed.get("reset")
            reset_dt = watcher.parse_time(account.get("reset"))
            reset_text = reset_dt.astimezone(watcher.LOCAL_TZ).strftime("%m-%d %H:%M:%S") if reset_dt else "未知"
            watcher.log(f"窗口点火确认成功：{account['label']} · {model} · reset={reset_text}")
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
    parser = argparse.ArgumentParser(description="CPA Quota Keeper: quota monitor + Window Ignition scheduler")
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

    enabled_provider_names = [
        provider_title(provider)
        for provider in QUOTA_FETCHERS
        if provider_config(provider).get("ignite")
    ]
    provider_text = "、".join(enabled_provider_names) if enabled_provider_names else "无"
    watcher.log(
        f"启动：每 {watcher.POLL_INTERVAL}s 刷新额度；点火={'开启' if IGNITE_ENABLED else '关闭'}；"
        f"点火 Provider={provider_text}；每日 {IGNITE_START_HOUR:02d}:00 起跑，"
        f"{IGNITE_END_HOUR:02d}:00 后保留 {IGNITE_END_GRACE_MINUTES} 分钟漂移窗口"
    )

    accounts = []
    next_poll = 0.0
    next_reset_updates_poll = 0.0
    while True:
        now_epoch = time.time()
        if CODEX_RESET_UPDATES_ENABLED and now_epoch >= next_reset_updates_poll:
            try:
                poll_codex_reset_updates(state)
            except Exception as exc:
                watcher.log(f"Did Codex Reset 检查失败：{exc}")
            next_reset_updates_poll = time.time() + CODEX_RESET_UPDATES_POLL_SECONDS

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
        if CODEX_RESET_UPDATES_ENABLED:
            sleep_for = min(sleep_for, max(1.0, next_reset_updates_poll - time.time()))
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        watcher.log(f"致命错误：{exc}")
        sys.exit(1)
