# CPA Quota Keeper

[中文](./README.md)

Monitor subscription quota in [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA) and receive [Bark](https://github.com/Finb/Bark) alerts for low quota, upcoming resets, and recovery. Optional **Window Ignition** sends a minimal request after a real 5-hour quota reset to start the next window.

Multiple accounts per provider are supported without maintaining an account list. Monitoring and ignition have separate switches.

| Provider | Monitoring | Ignition default |
| --- | --- | --- |
| ChatGPT / Codex | 5-hour and 7-day windows | On |
| Claude | 5-hour, 7-day, and Fable windows | On |
| Antigravity | Gemini, Claude / GPT, and other quota groups | Off |
| Grok / xAI | Billing windows returned by xAI | Off |

Monitoring is enabled for all providers by default. Ignition requires a real 5-hour window reported upstream; Grok accounts with only weekly or monthly quota receive no ignition requests, even when ignition is enabled.

## Quick start

Requires a running CPA instance and Docker Compose.

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

The default [compose.yaml](./compose.yaml) uses the following settings. Edit it if your setup differs:

| Setting | Default |
| --- | --- |
| CPA container | `cliproxyapi` |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker network | `cliproxyapi_default` |
| CPA config file | `/opt/cliproxyapi/config.yaml` |
| Extra environment file | `/opt/cliproxyapi/watcher.env` |

You can reuse `MANAGEMENT_PASSWORD` from `watcher.env`. If you do not use that file, remove its `env_file` entry from `compose.yaml` and set `CPA_MANAGEMENT_KEY`.

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## Configuration

- [`.env.example`](./.env.example): CPA, Bark, alert thresholds, polling, and timezone. Defaults to a 300-second quota refresh and UTC+8; `POLL_INTERVAL` does not control ignition scheduling.
- [`keeper.example.toml`](./keeper.example.toml): ignition timing and provider settings. Edit your copy, `keeper.toml`; legacy `IGNITE_*` environment variables remain available as a compatibility fallback.

### Ignition rules

By default, ignition starts at 07:00 and then follows each window's `reset_at + 3 seconds`. Existing windows keep their reset times. The last round may run until 22:30; later resets wait until 07:00 the next day. Failed requests retry the same account after 5 minutes.

Ignition uses the CPA Management API with the account's exact `auth_index`, with no failover to another account. Keeper selects a lightweight model available to that account: Luna for Codex, Haiku for Claude, Flash Lite / Flash for Antigravity Gemini, and lightweight text models for other groups and Grok.

Use `monitor` and `ignite` to control monitoring and ignition separately. Set `model` to override automatic selection. For example, edit the existing Claude section:

```toml
[providers.claude]
monitor = true
ignite = true
model = "your-model-id"
```

When enabled, Antigravity ignition covers all detected 5-hour quota groups. Use `groups` to limit it and optionally set models per group:

```toml
[providers.antigravity]
monitor = true
ignite = true
groups = ["Gemini"]

[providers.antigravity.models]
"Gemini" = "gemini-3.5-flash-lite"
```

## Notifications

- Alerts when remaining quota crosses **50%, 20%, 10%, and 0%**.
- `NOTIFY_RESET_REMINDERS` controls reset reminders.
- `NOTIFY_RECOVERY` controls recovery alerts.
- The example config disables the last two to avoid redundant notifications when Window Ignition is enabled. Low-quota alerts are unaffected.
- Notification countdowns use compact units: `d` for days, `h` for hours, and `m` for minutes, for example `06d`, `05h`, and `30m`.

Multi-account notifications and logs use masked suffixes, such as `alice.work@example.com` → `ChatGPT#al~rk`. Routing still uses `auth_index`.

## Commands

```bash
# Refresh quota without ignition
docker compose run --rm quota-keeper python /app/scheduler.py --once

# Show the ignition schedule
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# Run tests
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

Do not commit keys, CPA auth files, or runtime state. `.gitignore` excludes `.env`, `keeper.toml`, and `data/`.
