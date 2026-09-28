# CPA Quota Keeper

[中文](./README.md)

Monitor Codex, Claude, and Antigravity quotas in CLIProxyAPI, send Bark notifications, and optionally keep Codex / Claude 5-hour quota windows active.

## Features

- Reads live upstream quota data instead of relying on management-page cache.
- Supports multiple Codex and Claude credentials with separate state and reset times.
- Supports Codex 5-hour and 7-day quotas.
- Supports Claude 5-hour, 7-day, and Fable quotas.
- Supports Antigravity Gemini and Claude / GPT quota groups.
- Sends Bark alerts when remaining quota crosses `50%`, `20%`, `10%`, or `0%`.
- Sends one recovery alert when quota recovers.
- Sends reset reminders within 1 hour for every window and within 1 day for 7-day windows.
- Automatically starts the next Codex / Claude 5-hour window.
- Auto-selects a low-usage model: Luna for Codex and Haiku for Claude when available.
- Pins every trigger to one exact `auth_index`; a failed trigger never falls back to another account.
- Stores runtime state in `data/state.json`.

## Window Ignition

By default, **07:00** is the daily anchor.

After 07:00, the scheduler does not hard-code 12:00, 17:00, or 22:00. It follows the real `reset_at` reported for each credential:

```text
07:00 first trigger
  ↓
read real reset_at
  ↓
trigger at reset_at + 3 seconds
  ↓
read the new reset_at
  ↓
repeat
```

Different accounts can therefore drift independently:

```text
Claude   → 12:00:04 → 17:00:07 → 22:00:10
ChatGPT#main → 12:03:21 → 17:03:24 → 22:03:27
ChatGPT#team → 12:18:05 → 17:18:08 → 22:18:11
```

If an account was already used earlier that day, its existing real window is preserved instead of being forced back onto a fixed clock.

The default schedule allows 30 minutes of drift after 22:00. A reset that falls overnight is skipped, and that credential waits until 07:00 the next day.

### No cross-account fallback

Window triggers do not go through normal CPA `/v1` load balancing. The scheduler calls the Management API `api-call` endpoint with the exact credential `auth_index`.

If one ChatGPT credential fails, only that credential is marked failed and retried later. The request is never rerouted to another account.

The default failure retry interval is 5 minutes.

## Trigger requests

Codex prefers an available Luna model. Claude prefers Haiku. You can override either model:

```env
IGNITE_CODEX_MODEL=
IGNITE_CLAUDE_MODEL=
```

The trigger disables tools and unnecessary reasoning and asks for exactly:

```text
OK
```

Claude also uses a very small output limit. The ChatGPT Codex backend does not accept `max_output_tokens`, so Codex output is constrained through the explicit instruction instead.

Antigravity is quota-monitoring only and is not used for window triggers.

## Setup

### 1. Create the environment file

```bash
cp .env.example .env
```

Configure Bark:

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. Provide the CLIProxyAPI management key

Put it in `.env`:

```env
CPA_MANAGEMENT_KEY=your_management_key
```

The included `compose.yaml` also reads:

```text
/opt/cliproxyapi/watcher.env
```

That file may contain:

```env
MANAGEMENT_PASSWORD=your_management_password
```

Either method is enough.

### 3. Check the Docker network

The default configuration assumes:

- the CLIProxyAPI container is named `cliproxyapi`
- the Management API is `http://cliproxyapi:8317/v0/management`
- the Docker network is `cliproxyapi_default`

Change `compose.yaml` or `.env` if your setup differs.

### 4. Start

```bash
docker compose up -d
```

Follow logs:

```bash
docker logs -f cpa-quota-keeper
```

Refresh quotas once without sending trigger requests:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

Show the next trigger for every account:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

Run scheduler tests:

```bash
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## Defaults

```env
POLL_INTERVAL=300
NOTICE_THRESHOLD=50
LOW_THRESHOLD=20
CRITICAL_THRESHOLD=10
NOTIFY_RECOVERY=true
TZ_OFFSET_HOURS=8
REQUEST_TIMEOUT=20

IGNITE_ENABLED=true
IGNITE_START_HOUR=7
IGNITE_END_HOUR=22
IGNITE_END_GRACE_MINUTES=30
IGNITE_GRACE_SECONDS=3
IGNITE_FAILURE_RETRY_SECONDS=300
IGNITE_POST_SUCCESS_HOLD_SECONDS=60
```

`POLL_INTERVAL` only controls quota refreshes and Bark notifications. Trigger scheduling uses the real `reset_at` with a local timer, so there is no 10-second quota polling loop.

## Files

- `watcher.py`: upstream quota helpers, Bark notifications, and existing state logic
- `scheduler.py`: Codex / Claude multi-account quota collection and 5-hour window scheduling
- `test_scheduler.py`: scheduling-boundary and exact-credential tests
- `compose.yaml`: Docker Compose configuration
- `.env.example`: environment variable example
- `README.md`: Chinese README

## Security

Do not commit:

- `.env`
- Bark device keys
- CLIProxyAPI management keys or passwords
- CLIProxyAPI auth files
- `data/state.json`

The keeper does not directly read account credential files. Quota reads and trigger calls go through the CLIProxyAPI Management API, where CPA replaces `$TOKEN$` for the selected `auth_index` server-side.

## Multiple accounts

A provider with one account (for example, one Gemini or one Claude account) is shown without an account suffix. When a provider has multiple credentials, you can assign stable display aliases with `ACCOUNT_LABELS_JSON`:

```env
ACCOUNT_LABELS_JSON={"auth_index_1":"ChatGPT#main","auth_index_2":"ChatGPT#team"}
```

Aliases are display-only. Routing and Window Ignition still bind to the exact `auth_index`.


Account count is not hard-coded. New Codex or Claude OAuth credentials are discovered automatically and scheduled independently.

For example, adding a third Codex credential produces three separate schedules:

```text
Codex #1
Codex #2
Codex #3
```

Each keeps its own quota state, `reset_at`, and next trigger time.
