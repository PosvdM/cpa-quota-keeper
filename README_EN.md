# CPA Quota Keeper

[中文](./README.md)

CPA Quota Keeper monitors subscription quota in [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA) and sends [Bark](https://github.com/Finb/Bark) notifications.

It can also:

- **Ignite quota windows** by sending a very small request when a 5-hour window needs to start.
- **Forward Codex reset signals** from [Did Codex Reset](https://didcodexreset.com/) to Bark.

A provider can have multiple accounts. Monitoring, ignition, and Codex reset forwarding can be enabled separately.

| Provider | Monitoring | Ignition default |
| --- | --- | --- |
| ChatGPT / Codex | 5-hour and 7-day windows | On |
| Claude | 5-hour, 7-day, and Fable windows | On for 5-hour windows only |
| Antigravity | Gemini, Claude / GPT, and other quota groups | Off; when enabled, Gemini only by default |
| Grok / xAI | Billing windows returned by xAI | Off |

If the upstream provider does not expose a 5-hour window, Keeper does not send an ignition request.

## Quick start

You need:

- A running CPA instance
- Docker Compose

```bash
git clone https://github.com/PosvdM/cpa-quota-keeper.git
cd cpa-quota-keeper
cp .env.example .env
cp keeper.example.toml keeper.toml
```

Set these values in `.env`:

```env
CPA_MANAGEMENT_KEY=your_management_key
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

The default [compose.yaml](./compose.yaml) uses these paths:

| Setting | Default |
| --- | --- |
| CPA container | `cliproxyapi` |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker network | `cliproxyapi_default` |
| CPA config file | `/opt/cliproxyapi/config.yaml` |
| Extra environment file | `/opt/cliproxyapi/watcher.env` |

Edit `compose.yaml` if your setup is different.

If `watcher.env` already contains `MANAGEMENT_PASSWORD`, Keeper can reuse it. Otherwise, remove that `env_file` entry and set `CPA_MANAGEMENT_KEY`.

Start the service:

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## Configuration

Main configuration files:

- [`.env.example`](./.env.example): CPA, Bark, alert thresholds, polling, and timezone.
- [`keeper.example.toml`](./keeper.example.toml): ignition timing, providers, Antigravity groups, and Did Codex Reset.

Quota refresh defaults to every 300 seconds. The default timezone is UTC+8. Legacy `IGNITE_*` environment variables still work as fallback settings.

## Ignition

### Scheduling

Ignition starts at 07:00 by default.

After a window has started, the next ignition time is:

```text
reset_at + 3 seconds
```

The last run of the day may happen as late as 22:30. Later resets wait until 07:00 the next day.

A failed ignition retries the same account after 5 minutes.

### Detecting an unstarted 5-hour window

Some providers return a placeholder `reset_at` before a 5-hour window starts. The value stays about five hours ahead of the current time and keeps moving forward.

Keeper compares consecutive observations:

- If `reset_at` moves with wall-clock time, Keeper treats it as a placeholder.
- If `reset_at` stops moving, Keeper treats the window as started.

This prevents a rolling placeholder from being used as a real reset time.

### Confirming ignition

A successful model request does not prove that the 5-hour window started.

After ignition, Keeper checks quota again after about:

```text
5s → 10s → 15s → 30s → 15s
```

Ignition is confirmed only after Keeper sees a fixed future `reset_at`.

After confirmation, the next ignition uses the `reset_at` returned by the provider. Keeper does not add its own fixed five-hour cooldown.

### Account routing

Ignition uses the account's exact CPA Management API `auth_index`.

If one account fails, Keeper does not switch to another account.

### Model priority

An explicit `model` or per-group `models` setting always wins.

Without an override:

- **ChatGPT / Codex**: newest Luna, then older Luna versions.
- **Claude**: newest non-thinking Haiku, then older Haiku versions.
- **Antigravity / Gemini**: newest non-image Flash, then older Flash versions; if no Flash is available, other non-Pro Gemini models; Pro models are the final fallback, newest first.
- **Antigravity / Claude / GPT**: Haiku → Sonnet → Opus → GPT-OSS → other non-thinking text models. This group is not ignited by default.
- **Grok / xAI**: prefer text models whose names contain `fast` or `mini`.

When a newer Luna, Haiku, or Flash appears in the model list, Keeper prefers it automatically by version number.

### Ignition prompt

All providers use the same prompt:

```text
This is an automated quota-window trigger. Do not think, reason, deliberate, use tools, or perform any other task. Reply with exactly OK and nothing else.
```

Codex also sets:

- `reasoning.effort = none`
- `tools = []`
- `store = false`

Other providers also use a very small output limit.

## Providers and groups

`monitor` controls monitoring. `ignite` controls ignition.

Example:

```toml
[providers.claude]
monitor = true
ignite = true
model = ""
```

Claude 7-day and Fable 5 windows are monitor-only.

Antigravity allows only Gemini to enter ignition by default:

```toml
[providers.antigravity]
monitor = true
ignite = false
model = ""
groups = ["Gemini"]
```

To enable Antigravity ignition, set `ignite = true`. Only groups listed in `groups` are ignited.

You can also override the model for a group:

```toml
[providers.antigravity.models]
"Gemini" = "your-model-id"
```

## Bark notifications

Keeper sends an alert when remaining quota crosses:

```text
50% → 20% → 10% → 0%
```

Other notification settings:

- `NOTIFY_RESET_REMINDERS`: reset reminders
- `NOTIFY_RECOVERY`: recovery alerts

The example config disables both to avoid duplicate notifications when ignition is enabled. Low-quota alerts still work.

Time remaining uses short units:

- `d`: days
- `h`: hours
- `m`: minutes

Examples: `06d`, `05h`, `30m`.

Multi-account notifications use a masked suffix, for example:

```text
alice.work@example.com → ChatGPT#al~rk
```

Requests still use `auth_index` for routing.

## Did Codex Reset → Bark

Keeper can poll the public Did Codex Reset API and forward new records to Bark:

```toml
[codex_reset_updates]
enabled = true
poll_seconds = 300
notify_current_pending = true
```

Rules:

- Minimum polling interval: 300 seconds.
- On first enable, Keeper sends only the currently pending schedule. It does not replay old history.
- Later records are deduplicated with stable event keys.
- Manual upstream records do not repeat even if their IDs change.
- Records with `announcedAt` get a link to the Did Codex Reset detail page.
- Tapping the Bark notification opens that page.
- The notification body includes time, confidence, and plan scope. It does not include the source account.

Did Codex Reset is a third-party monitor. Its signals are not an OpenAI confirmation.

## Commands

```bash
# Refresh quota without ignition
docker compose run --rm quota-keeper python /app/scheduler.py --once

# Show the ignition schedule
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# Run tests
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

Do not commit keys, CPA auth files, or runtime state.

`.gitignore` excludes:

```text
.env
keeper.toml
data/
```
