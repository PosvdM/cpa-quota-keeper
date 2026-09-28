# CPA Quota Keeper

[中文](./README.md)

CPA Quota Keeper adds quota monitoring and automatic window triggering to CLIProxyAPI.

It reads Codex, Claude, and Antigravity quota data, sends Bark alerts, and can start the next Codex or Claude 5-hour quota window with a minimal request.

## What it supports

- Codex 5-hour and 7-day quotas
- Claude 5-hour, 7-day, and Fable quotas
- Antigravity Gemini and Claude / GPT quota groups
- Multiple Codex and Claude accounts
- Bark alerts for low quota, resets, and recovery
- Automatic Codex / Claude 5-hour window triggering

Default alert thresholds:

```text
50% → alert
20% → alert
10% → alert
 0% → alert
```

Every quota window gets a reset reminder within 1 hour of reset. A 7-day window also gets a reminder within 1 day.

A recovery alert is sent when quota becomes healthy again.

## Window Ignition

**07:00** is the daily starting point.

After 07:00, Keeper does not use fixed 12:00, 17:00, and 22:00 trigger times. It follows the real `reset_at` reported for each account.

```text
07:00 trigger
  ↓
read reset_at
  ↓
trigger again at reset_at + 3 seconds
  ↓
read the new reset_at
  ↓
repeat
```

Each account therefore follows its own real quota window.

If an account was already used earlier, Keeper keeps its current window instead of forcing it back onto a fixed schedule.

The last daytime trigger may drift up to 30 minutes after 22:00 by default. Later resets are skipped until 07:00 the next day.

### Trigger request

Codex prefers an available Luna model. Claude prefers Haiku.

The request only asks the model to reply with:

```text
OK
```

Tools are disabled, and reasoning is disabled or minimized where supported.

Each trigger is pinned to the credential's exact `auth_index`. If one account fails, Keeper retries that account only.

The default retry delay is 5 minutes.

Antigravity is monitored but is not used for Window Ignition.

## Multiple accounts

If a provider has only one account, Keeper shows only the provider name:

```text
Gemini
Claude
ChatGPT
```

If a provider has multiple accounts, Keeper reads the account email from the credential and masks the email local-part with its first two and last two characters.

For example:

```text
alice.work@example.com → ChatGPT#al~rk
bob.team@example.net   → ChatGPT#bo~am
```

The label is only used in notifications and logs. Routing still uses the exact `auth_index`.

New accounts are detected automatically.

## Notification examples

Quota alert:

```text
⚠️ Claude · 7d 48%

5h：96% | 04h | 09/28 13:50
7d：48% | 03d | 10/01 14:00
```

Reset reminder:

```text
⏰ ChatGPT#bo~am · 5h reset reminder

5h：19% | 01h | 09/28 13:55
7d：81% | 06d | 10/04 21:27
```

Recovery:

```text
✅ Claude · 7d recovered

5h：96% | 04h | 09/28 13:50
7d：100% | 03d | 10/01 14:00
```

## Setup

Copy the example environment file:

```bash
cp .env.example .env
```

Set Bark:

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

Then provide the CLIProxyAPI Management API key.

You can put it in `.env`:

```env
CPA_MANAGEMENT_KEY=your_management_key
```

Or use:

```text
/opt/cliproxyapi/watcher.env
```

with:

```env
MANAGEMENT_PASSWORD=your_management_password
```

Use either method.

The default Docker setup expects:

```text
CLIProxyAPI container: cliproxyapi
Management API: http://cliproxyapi:8317/v0/management
Docker network: cliproxyapi_default
```

If your setup is different, edit `compose.yaml` and `.env`.

Start the service:

```bash
docker compose up -d
```

Follow logs:

```bash
docker logs -f cpa-quota-keeper
```

## Common commands

Refresh quota once without triggering a new window:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

Show the next trigger time for each account:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

Run tests:

```bash
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## Default settings

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

IGNITE_CODEX_MODEL=
IGNITE_CLAUDE_MODEL=
```

`POLL_INTERVAL` only controls quota refreshes and Bark alerts.

Window Ignition schedules the next trigger from `reset_at`, so it does not need frequent quota polling.

## Files

- `watcher.py`: reads quota data, builds notifications, and stores notification state
- `scheduler.py`: multi-account scheduling and Window Ignition
- `test_scheduler.py`: tests
- `compose.yaml`: Docker Compose configuration
- `.env.example`: configuration example
- `data/state.json`: runtime state

## Security

Do not commit:

- `.env`
- Bark device keys
- CLIProxyAPI management keys or passwords
- CLIProxyAPI auth files
- `data/state.json`

Keeper does not read account credential files directly.

Quota checks and trigger requests go through the CLIProxyAPI Management API. CPA uses the credential selected by `auth_index`.
