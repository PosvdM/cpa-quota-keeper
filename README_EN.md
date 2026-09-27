# CPA Quota Watcher

A small Dockerized watcher for [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) that actively refreshes quota data and sends threshold alerts to Bark.

## What it does

- Polls CLIProxyAPI's Management API instead of relying on the web UI cache.
- Reads Claude OAuth quota windows, including 5-hour, 7-day, and Fable quota when available.
- Reads Antigravity quota groups such as Gemini and Claude/GPT.
- Sends Bark alerts when remaining quota crosses 50%, 20%, 10%, or 0%.
- Deduplicates alerts within the same severity band.
- Sends a recovery notification when quota resets or returns to a healthier band.
- Sends one reset reminder for every quota window when its reset is within 1 hour, regardless of the remaining percentage.
- Sends an additional reminder for 7-day windows when the reset is within 1 day but still more than 1 hour away.
- Persists separate 1-day and 1-hour reminder markers so restarting the watcher does not resend the same stage in the same reset cycle.
- Persists state in `data/state.json`.

Example alert:

```text
⚠️ CPA · Claude · 7d 48%

5h：83% | 02时 | 09/28 05:59
7d：48% | 03天 | 10/01 13:59
```

Reset reminder:

```text
⏰ CPA · Gemini · 5h 重置提醒

5h：94% | 07分 | 09/28 04:16
```

The compact relative time is rounded within the real current unit:

- `< 1 hour` → minutes, e.g. `60分`
- `1 hour – < 1 day` → hours, e.g. `03时`
- `>= 1 day` → days, e.g. `04天`

## Files

- `watcher.py` — quota polling, state machine, formatting, and Bark delivery
- `compose.yaml` — Docker Compose service used by the current deployment
- `.env.example` — safe configuration template
- `.gitignore` — excludes secrets and runtime state

## Setup

1. Put this project next to your CLIProxyAPI deployment.
2. Copy `.env.example` to `.env`.
3. Set `BARK_URL`.
4. Provide the CLIProxyAPI management key.

The included `compose.yaml` matches the current deployment and loads an additional file:

```text
/opt/cliproxyapi/watcher.env
```

That file can contain:

```text
MANAGEMENT_PASSWORD=your_management_password
```

Alternatively, set `CPA_MANAGEMENT_KEY` in `.env` and remove the extra `env_file` entry from `compose.yaml`.

The watcher expects the external Docker network `cliproxyapi_default` and reaches CPA at:

```text
http://cliproxyapi:8317/v0/management
```

Start it with:

```bash
docker compose up -d
```

Check logs with:

```bash
docker logs -f cpa-quota-watcher
```

Run one polling cycle manually:

```bash
docker compose run --rm quota-watcher python /app/watcher.py --once
```

## Security

Do not commit any of the following:

- `.env`
- Bark device keys
- CLIProxyAPI management passwords
- CLIProxyAPI auth files
- `data/state.json`

The provided `.gitignore` excludes the local `.env` and runtime state directory.

## Notes

The watcher calls upstream quota endpoints through CLIProxyAPI's Management API, so it does not need direct access to Claude or Antigravity credential files.
