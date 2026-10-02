# CPA Quota Keeper

[中文](./README.md)

Monitor subscription quota in [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA) and receive [Bark](https://github.com/Finb/Bark) alerts for low quota, resets, and recovery. Optional **Window Ignition** sends a tiny request to start the next real 5-hour quota window. Keeper can also forward Codex reset signals from [Did Codex Reset](https://didcodexreset.com/) to Bark.

Multiple accounts per provider are supported without maintaining an account list. Monitoring, ignition, and Codex reset forwarding have separate switches.

| Provider | Monitoring | Ignition default |
| --- | --- | --- |
| ChatGPT / Codex | 5-hour and 7-day windows | On |
| Claude | 5-hour, 7-day, and Fable windows | On for 5-hour only; 7-day / Fable are monitor-only |
| Antigravity | Gemini, Claude / GPT, and other quota groups | Off; when enabled, Gemini only by default |
| Grok / xAI | Billing windows returned by xAI | Off |

Monitoring is enabled for all providers by default. Ignition tasks are created only for detected real 5-hour windows; no 5-hour window means no ignition request.

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

- [`.env.example`](./.env.example): CPA, Bark, alert thresholds, polling, and timezone. Defaults to a 300-second quota refresh and UTC+8; `POLL_INTERVAL` does not control the exact ignition time.
- [`keeper.example.toml`](./keeper.example.toml): ignition timing, provider settings, Antigravity groups, and Did Codex Reset forwarding. Edit your copy, `keeper.toml`; legacy `IGNITE_*` environment variables remain available as a compatibility fallback.

### Ignition rules

By default, ignition starts at 07:00 and normally follows each window's returned `reset_at + 3 seconds`. The last round may run until 22:30; later resets wait until 07:00 the next day. Failed requests retry the same account after 5 minutes.

Some providers expose an unstarted 5-hour window as a rolling placeholder whose `reset_at` always stays about five hours ahead of the current time. Keeper compares consecutive observations: if `reset_at` moves forward with wall-clock time, it treats the value as an unstarted placeholder instead of a real reset time.

A successful HTTP/model response is not immediately treated as a successful ignition. Keeper re-reads quota after roughly 5s → 10s → 15s → 30s → 15s and confirms ignition only after it sees a future, fixed `reset_at`. After confirmation, the next run is scheduled from the actual `reset_at` returned upstream; Keeper does not invent an extra fixed five-hour cooldown.

Ignition uses the CPA Management API with the account's exact `auth_index`, with no failover to another account. Automatic model selection prefers the newest version in the lightweight family, then falls back to older versions:

- ChatGPT / Codex: newest **Luna**.
- Claude: newest non-thinking **Haiku**.
- Antigravity / Gemini: prefer the newest non-image **Flash** and fall back through older Flash versions; if no Flash is available, try other non-Pro Gemini models first and use the newest Pro model last.
- Antigravity / Claude / GPT (not ignited by default): Haiku → Sonnet → Opus → GPT-OSS → other non-thinking text models.
- Grok / xAI: text models whose names contain `fast` or `mini` first.

When a newer Luna / Haiku / Flash model appears in the account model list, Keeper automatically prefers it by numeric version instead of requiring another hard-coded model ID. Explicit `model` or per-group `models` settings always override automatic selection.

All ignition providers use the same strict prompt:

```text
This is an automated quota-window trigger. Do not think, reason, deliberate, use tools, or perform any other task. Reply with exactly OK and nothing else.
```

Codex also sets `reasoning.effort = none`, an empty tool list, and `store = false`. Claude, Antigravity, and xAI ignition responses are capped to a tiny output.

### Providers and groups

Use `monitor` and `ignite` to control monitoring and ignition separately. Set `model` to override automatic model selection:

```toml
[providers.claude]
monitor = true
ignite = true
model = ""
```

Claude 7-day and Fable 5 windows are monitor-only and never participate in automatic ignition.

The Antigravity example config allows only the Gemini group to enter automatic ignition by default. The smaller `Claude / GPT` bucket is still monitored but is not consumed by ignition:

```toml
[providers.antigravity]
monitor = true
ignite = false
model = ""
groups = ["Gemini"]
```

To enable Antigravity ignition, set `ignite = true`; only groups listed in `groups` will be ignited. Models can also be overridden per group:

```toml
[providers.antigravity.models]
"Gemini" = "your-model-id"
```

## Notifications

- Alerts when remaining quota crosses **50%, 20%, 10%, and 0%**.
- `NOTIFY_RESET_REMINDERS` controls reset reminders.
- `NOTIFY_RECOVERY` controls recovery alerts.
- The example config disables the last two to avoid redundant notifications when Window Ignition is enabled. Low-quota alerts are unaffected.
- Notification countdowns use compact units: `d` for days, `h` for hours, and `m` for minutes, for example `06d`, `05h`, and `30m`.
- Multi-account notifications and logs use masked suffixes, such as `alice.work@example.com` → `ChatGPT#al~rk`. Routing still uses `auth_index`.

### Did Codex Reset → Bark

Keeper can optionally poll the public Did Codex Reset API and forward newly published global resets, reset cards, and related Codex signals to Bark:

```toml
[codex_reset_updates]
enabled = true
poll_seconds = 300
notify_current_pending = true
```

- The minimum polling interval is 300 seconds.
- On first enable, Keeper only sends the currently pending scheduled reset instead of replaying old history.
- Later records are deduplicated with stable event keys, including manual upstream records whose IDs may rotate.
- Records with `announcedAt` get a clickable Bark URL to the corresponding Did Codex Reset detail page.
- The notification body includes time, confidence, and plan scope, without a source-account line.
- Did Codex Reset is a third-party monitor; its signals are not an OpenAI guarantee.

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
