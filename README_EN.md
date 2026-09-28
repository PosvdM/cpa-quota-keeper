# CPA Quota Keeper

[中文](./README.md)

CPA Quota Keeper monitors quotas and triggers usage windows for [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA). It does two things:

1. Reads the remaining quota of Codex, Claude, and Antigravity accounts on a schedule, and sends [Bark](https://github.com/Finb/Bark) notifications when a quota drops, is about to reset, or has recovered.
2. After a Codex / Claude 5-hour quota resets, sends a minimal request to that account so the next 5-hour window starts right away.

## Supported quotas

| Provider | Quota windows | Window Ignition |
| --- | --- | --- |
| Codex | 5 hours, 7 days | Yes |
| Claude | 5 hours, 7 days, Fable | Yes |
| Antigravity | Gemini, Claude / GPT quota groups | No, monitoring only |

Each provider can have multiple accounts. New accounts need no configuration changes.

## Notifications

Bark notifications are sent when:

- Remaining quota drops to 50%, 20%, 10%, or 0% (thresholds are configurable)
- Any quota window is 1 hour from reset
- A 7-day window is 1 day from reset
- A quota returns to normal after a reset

Notifications are in Chinese. Examples:

```text
⚠️ Claude · 7d 48%

5h：96% | 04时 | 09/28 13:50
7d：48% | 03天 | 10/01 14:00
```

```text
⏰ ChatGPT#bo~am · 5h 重置提醒

5h：19% | 01时 | 09/28 13:55
7d：81% | 06天 | 10/04 21:27
```

```text
✅ Claude · 7d 已恢复

5h：96% | 04时 | 09/28 13:50
7d：100% | 03天 | 10/01 14:00
```

Each line shows the window, remaining quota, time until reset (`时` = hours, `天` = days), and reset time. `重置提醒` means reset reminder; `已恢复` means recovered.

## Window Ignition

A 5-hour window starts counting at the first request. If you start using an account long after its quota resets, that time is lost. Keeper sends a request right after each reset so the windows run back to back.

Schedule:

```text
07:00 first trigger
  ↓
read the account's reset_at
  ↓
trigger again at reset_at + 3 seconds
  ↓
read the new reset_at, repeat
```

- Each account follows its own `reset_at`. If an account is already in use, Keeper keeps its current window and does not force it back to 07:00.
- The last trigger of the day happens no later than 22:30 (`IGNITE_END_HOUR` + `IGNITE_END_GRACE_MINUTES`). Later resets are not triggered; the account waits until 07:00 the next day.
- The trigger request only asks the model to reply `OK` and includes no tools. Codex requests disable reasoning; Claude requests allow at most 4 output tokens.
- Codex uses Luna and Claude uses Haiku by default. Set `IGNITE_CODEX_MODEL` / `IGNITE_CLAUDE_MODEL` to choose other models.
- Each request is bound to one account through `auth_index`. On failure, Keeper retries the same account after 5 minutes and never switches to another account.

## Deployment

### Requirements

- CPA running in Docker with the Management API enabled
- A Bark device key

Default assumptions:

| Item | Default |
| --- | --- |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker network | `cliproxyapi_default` |
| CPA config file | `/opt/cliproxyapi/config.yaml` |

If your setup differs, edit `compose.yaml`.

### 1. Create the config

```bash
cp .env.example .env
```

Set the Bark URL:

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. Provide the management key

Keeper looks for the key in this order and uses the first one found:

1. `CPA_MANAGEMENT_KEY` in `.env`
2. `MANAGEMENT_PASSWORD` in `/opt/cliproxyapi/watcher.env`
3. The plain-text value of `remote-management.secret-key` in CPA's `config.yaml`

If CPA has already replaced `secret-key` in `config.yaml` with a bcrypt hash, use one of the first two options.

By default, `compose.yaml` loads `/opt/cliproxyapi/watcher.env` and mounts `/opt/cliproxyapi/config.yaml`. `docker compose` fails if either file is missing, so remove the matching lines from `compose.yaml` if you don't use them.

### 3. Start

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## Commands

```bash
# Refresh quotas and send notifications once, without triggering
docker compose run --rm quota-keeper python /app/scheduler.py --once

# Show the next trigger time for each account
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# Run tests
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## Configuration

All settings go in `.env`.

### Notifications

| Variable | Default | Description |
| --- | --- | --- |
| `BARK_URL` | empty | Bark push URL; no notifications are sent when empty |
| `BARK_GROUP` | `CPA` | Bark notification group |
| `BARK_ICON` | CPA logo | Notification icon |
| `POLL_INTERVAL` | `300` | Quota refresh interval in seconds, minimum 60 |
| `NOTICE_THRESHOLD` | `50` | First alert threshold (%) |
| `LOW_THRESHOLD` | `20` | Second alert threshold (%) |
| `CRITICAL_THRESHOLD` | `10` | Third alert threshold (%) |
| `NOTIFY_RECOVERY` | `true` | Notify when a quota recovers |
| `TZ_OFFSET_HOURS` | `8` | Time zone (UTC offset) for displayed times and trigger hours |
| `REQUEST_TIMEOUT` | `20` | HTTP request timeout in seconds |

`POLL_INTERVAL` only affects quota refresh and notifications. Trigger times come from `reset_at` and do not depend on the polling rate.

### Window Ignition

| Variable | Default | Description |
| --- | --- | --- |
| `IGNITE_ENABLED` | `true` | Enable Window Ignition |
| `IGNITE_START_HOUR` | `7` | Hour of the first trigger each day |
| `IGNITE_END_HOUR` | `22` | Hour after which triggering stops |
| `IGNITE_END_GRACE_MINUTES` | `30` | Extra minutes allowed after `IGNITE_END_HOUR` |
| `IGNITE_GRACE_SECONDS` | `3` | Seconds to wait after `reset_at` before triggering |
| `IGNITE_FAILURE_RETRY_SECONDS` | `300` | Retry delay after a failure in seconds, minimum 60 |
| `IGNITE_POST_SUCCESS_HOLD_SECONDS` | `60` | Seconds after a success during which no new trigger is sent, minimum 15 |
| `IGNITE_CODEX_MODEL` | empty | Codex trigger model; Luna is chosen when empty |
| `IGNITE_CLAUDE_MODEL` | empty | Claude trigger model; Haiku is chosen when empty |

## Account names

When a provider has one account, notifications show only the provider name, such as `Claude`, `ChatGPT`, or `Gemini`.

With multiple accounts, Keeper adds a suffix made of the first 2 and last 2 characters of the email username:

```text
alice.work@example.com → ChatGPT#al~rk
bob.team@example.net   → ChatGPT#bo~am
```

These names are only used in notifications and logs. Scheduling and requests still identify accounts by `auth_index`.

## Files

| File | Purpose |
| --- | --- |
| `watcher.py` | Reads quotas, builds and sends notifications |
| `scheduler.py` | Main program: multi-account scheduling and Window Ignition |
| `test_scheduler.py` | Tests |
| `compose.yaml` | Docker Compose config |
| `.env.example` | Example config |
| `data/state.json` | Runtime state (created automatically) |

## Security

- Keeper does not read credential files from disk. Quota queries and triggers go through the CPA Management API, and CPA uses the credential for the given `auth_index` on the server side. If an Antigravity credential lacks `project_id`, Keeper downloads that credential file through the Management API to read it.
- The container runs with a read-only file system and all Linux capabilities dropped.
- Do not commit `.env`, your Bark device key, the management key, CPA credential files, or `data/state.json`. `.gitignore` already excludes `.env` and `data/`.
