# CPA Quota Keeper

[中文](./README.md)

CPA Quota Keeper adds quota monitoring and **Window Ignition** to [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA).

It does two jobs:

1. Reads subscription quota and sends [Bark](https://github.com/Finb/Bark) alerts for low quota, upcoming resets, and recovery.
2. For providers with Window Ignition enabled, sends a minimal request after a real 5-hour quota reset so the next window starts immediately.

Monitoring and ignition are separate. You can monitor a provider without automatically consuming its quota.

## Supported providers

| Provider | Monitoring | Window Ignition |
| --- | --- | --- |
| ChatGPT / Codex | 5-hour and 7-day windows | Configurable |
| Claude | 5-hour, 7-day, and Fable windows | Configurable |
| Antigravity | Gemini, Claude / GPT, and other quota groups | Configurable |
| Grok / xAI | Billing windows returned by xAI | Configurable when a real 5-hour window is reported |

Keeper does not guess a window length from the provider name. A provider adapter normalizes upstream quota into a window and reset time. Any normalized 5-hour window can use the same scheduler.

If a Grok / xAI account reports only weekly or monthly billing, Keeper can monitor those windows but does not invent a 5-hour window or send ignition requests for one.

Multiple accounts per provider are supported without maintaining an account list.

## Configuration

Create both local config files:

```bash
cp .env.example .env
cp keeper.example.toml keeper.toml
```

- `.env`: CPA connection, Bark, polling, and timezone.
- `keeper.toml`: Window Ignition and per-provider behavior.

Default provider settings:

```toml
[providers.codex]
monitor = true
ignite = true

[providers.claude]
monitor = true
ignite = true

[providers.antigravity]
monitor = true
ignite = false

[providers.grok]
monitor = true
ignite = false
```

`monitor = true` reads quota and sends alerts.

`ignite = true` adds real 5-hour windows from that provider to Window Ignition.

This makes it possible to keep monitoring Antigravity while leaving its ignition disabled.

### Window Ignition schedule

```toml
[window_ignition]
enabled = true
start_hour = 7
end_hour = 22
end_grace_minutes = 30
grace_seconds = 3
failure_retry_seconds = 300
post_success_hold_seconds = 60
```

07:00 is the daily anchor. Later requests follow each window's real `reset_at` instead of fixed 12:00, 17:00, and 22:00 times:

```text
07:00 ignite
  ↓
read real reset_at
  ↓
ignite again at reset_at + 3 seconds
  ↓
read the new reset_at
  ↓
repeat
```

If an account was already used earlier, Keeper follows its current window instead of forcing it back onto a fixed clock.

The default cutoff allows the last daytime window to drift until 22:30. Later resets wait until 07:00 the next day.

A failed ignition retries the same credential after 5 minutes. It does not fail over to another account.

### Antigravity

One Antigravity account can expose several independent 5-hour quota groups, such as Gemini and Claude / GPT.

Enable all detected 5-hour groups:

```toml
[providers.antigravity]
monitor = true
ignite = true
```

Limit ignition to one group:

```toml
[providers.antigravity]
monitor = true
ignite = true
groups = ["Gemini"]
```

Optional per-group model overrides:

```toml
[providers.antigravity.models]
"Gemini" = "gemini-3.5-flash-lite"
"Claude / GPT" = "gpt-oss-120b-medium"
```

Without an override, Keeper chooses a lighter model from the models actually available to that credential.

### Grok / xAI

Grok uses the same provider config:

```toml
[providers.grok]
monitor = true
ignite = true
model = ""
```

Keeper reads the xAI billing period. It only adds the account to Window Ignition when the upstream response represents a real 5-hour window.

If the account only reports weekly or monthly billing, setting `ignite = true` does not create extra requests.

## Ignition requests

Every provider uses the same rule: keep the request small and pin it to one credential.

- ChatGPT / Codex prefers Luna.
- Claude prefers Haiku.
- Antigravity Gemini prefers Flash Lite / Flash.
- Antigravity Claude / GPT prefers a lighter compatible model.
- Grok prefers a non-image, non-video lightweight model.

Override automatic selection with `model`:

```toml
[providers.claude]
monitor = true
ignite = true
model = "your-model-id"
```

Requests go through the CPA Management API with the exact `auth_index`. A failed request is never rerouted to another credential.

## Notifications

Default remaining-quota thresholds:

```text
50%
20%
10%
0%
```

Keeper also sends:

- a reminder within 1 hour of every reset,
- an additional reminder within 1 day of a 7-day reset,
- one recovery alert after quota recovers.

Example:

```text
⚠️ Claude · 7d 48%

5h：96% | 04h | 09/28 13:50
7d：48% | 03d | 10/01 14:00
```

When a provider has multiple accounts, Keeper creates a masked label from the first two and last two characters of the email local-part:

```text
alice.work@example.com → ChatGPT#al~rk
bob.team@example.net   → ChatGPT#bo~am
```

The label is only for notifications and logs. Routing still uses `auth_index`.

## Deployment

### 1. Create local config

```bash
cp .env.example .env
cp keeper.example.toml keeper.toml
```

Configure Bark:

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

Provide the CPA Management API key:

```env
CPA_MANAGEMENT_KEY=your_management_key
```

You can also keep using `MANAGEMENT_PASSWORD` from `/opt/cliproxyapi/watcher.env`.

The default Docker setup expects:

| Setting | Default |
| --- | --- |
| CPA container | `cliproxyapi` |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker network | `cliproxyapi_default` |
| CPA config file | `/opt/cliproxyapi/config.yaml` |

Edit `compose.yaml` if your setup differs.

### 2. Start

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## Commands

Refresh quota without ignition:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

Show the current ignition schedule:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

Run tests:

```bash
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## .env

```env
CPA_BASE_URL=http://cliproxyapi:8317/v0/management
CPA_CONFIG_FILE=/run/cliproxyapi/config.yaml
STATE_FILE=/data/state.json
CPA_MANAGEMENT_KEY=

BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA

POLL_INTERVAL=300
NOTICE_THRESHOLD=50
LOW_THRESHOLD=20
CRITICAL_THRESHOLD=10
NOTIFY_RECOVERY=true

TZ_OFFSET_HOURS=8
REQUEST_TIMEOUT=20
KEEPER_CONFIG=/app/keeper.toml
```

`POLL_INTERVAL` only controls quota refreshes and notifications. Window Ignition uses real reset times and has its own scheduler.

Legacy `IGNITE_*` environment variables remain available as a compatibility fallback, but new deployments should keep ignition settings in `keeper.toml`.

## Files

| File | Purpose |
| --- | --- |
| `watcher.py` | Reads quota, builds Bark alerts, and stores notification state |
| `scheduler.py` | Provider adapters, 5-hour scheduling, and Window Ignition |
| `keeper.example.toml` | Provider and ignition configuration template |
| `keeper.toml` | Local configuration; do not commit it |
| `test_scheduler.py` | Unit tests |
| `compose.yaml` | Docker Compose configuration |
| `.env.example` | Environment variable template |
| `data/state.json` | Generated runtime state |

## Security

Do not commit:

- `.env`
- `keeper.toml`
- Bark device keys
- CPA Management keys
- CPA auth files
- `data/state.json`

`.gitignore` excludes `.env`, `keeper.toml`, and `data/`.

Quota reads and ignition requests go through the CPA Management API. Ignition uses the selected credential's exact `auth_index` instead of normal load balancing.
