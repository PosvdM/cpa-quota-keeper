# CPA Quota Keeper

[中文](./README.md)

CPA Quota Keeper monitors quotas and triggers usage windows for [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA):

1. Queries remaining quotas for Codex, Claude, and Antigravity accounts, sending [Bark](https://github.com/Finb/Bark) notifications on quota drops, upcoming resets, and recoveries.
2. Sends a minimal request to Codex and Claude accounts immediately after a 5-hour quota reset, starting the next window without delay.

## Supported Quotas

| Provider | Quota Windows | Window Ignition |
| --- | --- | --- |
| Codex | 5 hours, 7 days | Yes |
| Claude | 5 hours, 7 days, Fable | Yes |
| Antigravity | Gemini, Claude / GPT quota groups | No (monitoring only) |

Each provider supports multiple accounts. Adding accounts requires no configuration changes.

## Notifications

Bark notifications trigger when:

- Remaining quota drops to 50%, 20%, 10%, or 0% (thresholds are configurable).
- Any quota window is 1 hour from reset.
- A 7-day window is 1 day from reset.
- A quota recovers after a reset.

Notification examples:

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

Each line shows the window, remaining quota, time until reset (`时` = hours, `天` = days), and reset time. In alert titles, `重置提醒` indicates a reset reminder, and `已恢复` indicates recovery.

## Window Ignition

The 5-hour window starts counting at the first request. Idle time after a reset delays the start of the next window. Keeper sends a lightweight request immediately after each reset to keep windows rolling continuously.

Schedule flow:

```text
07:00 First ignition
  ↓
Read account reset_at
  ↓
Ignite again at reset_at + 3 seconds
  ↓
Read new reset_at, repeat
```

- Each account follows its own `reset_at` independently. For active accounts, Keeper tracks the existing window instead of forcing alignment to 07:00.
- Daily ignitions end at 22:30 (`IGNITE_END_HOUR` + `IGNITE_END_GRACE_MINUTES`). Resets after this cut-off do not trigger and wait until 07:00 the next day.
- Ignition requests require only an `OK` reply from the model without tools. Codex requests disable reasoning; Claude requests limit output to 4 tokens.
- Models default to Luna for Codex and Haiku for Claude. Override them using `IGNITE_CODEX_MODEL` and `IGNITE_CLAUDE_MODEL`.
- Requests bind to accounts via `auth_index`. Failed requests retry the same account after 5 minutes without failing over to another account.

## Deployment

### Prerequisites

- CPA running in Docker with the Management API enabled
- A Bark device key

Default assumptions:

| Setting | Default |
| --- | --- |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker network | `cliproxyapi_default` |
| CPA config file | `/opt/cliproxyapi/config.yaml` |

If your setup differs, edit `compose.yaml`.

### 1. Create Configuration

```bash
cp .env.example .env
```

Set the Bark endpoint:

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. Provide the Management Key

Keeper looks for the management key in the following order and uses the first match:

1. `CPA_MANAGEMENT_KEY` in `.env`
2. `MANAGEMENT_PASSWORD` in `/opt/cliproxyapi/watcher.env`
3. The plain-text `remote-management.secret-key` in CPA's `config.yaml`

If CPA has replaced `secret-key` in `config.yaml` with a bcrypt hash, provide the plain-text key via option 1 or 2.

`compose.yaml` defaults to loading `/opt/cliproxyapi/watcher.env` and mounting `/opt/cliproxyapi/config.yaml`. If either file does not exist, `docker compose` will fail; remove unused file references from `compose.yaml`.

### 3. Start

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## Commands

```bash
# Refresh quotas and send notifications once (without ignition)
docker compose run --rm quota-keeper python /app/scheduler.py --once

# Show the next ignition time for each account
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# Run tests
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## Configuration

Set all variables in `.env`.

### Notifications

| Variable | Default | Description |
| --- | --- | --- |
| `BARK_URL` | empty | Bark push URL; notifications are disabled when empty |
| `BARK_GROUP` | `CPA` | Bark notification group |
| `BARK_ICON` | CPA logo | Notification icon |
| `POLL_INTERVAL` | `300` | Quota check interval in seconds (minimum: 60) |
| `NOTICE_THRESHOLD` | `50` | First alert threshold (%) |
| `LOW_THRESHOLD` | `20` | Second alert threshold (%) |
| `CRITICAL_THRESHOLD` | `10` | Third alert threshold (%) |
| `NOTIFY_RECOVERY` | `true` | Send notification when a quota recovers |
| `TZ_OFFSET_HOURS` | `8` | UTC offset for displayed times and ignition schedule |
| `REQUEST_TIMEOUT` | `20` | HTTP request timeout in seconds |

`POLL_INTERVAL` affects only quota polling and notifications. Ignition timing depends on `reset_at`, not polling frequency.

### Window Ignition

| Variable | Default | Description |
| --- | --- | --- |
| `IGNITE_ENABLED` | `true` | Enable Window Ignition |
| `IGNITE_START_HOUR` | `7` | Daily ignition start hour |
| `IGNITE_END_HOUR` | `22` | Daily ignition end hour |
| `IGNITE_END_GRACE_MINUTES` | `30` | Grace window in minutes after `IGNITE_END_HOUR` |
| `IGNITE_GRACE_SECONDS` | `3` | Delay in seconds after `reset_at` before triggering |
| `IGNITE_FAILURE_RETRY_SECONDS` | `300` | Retry delay after failure in seconds (minimum: 60) |
| `IGNITE_POST_SUCCESS_HOLD_SECONDS` | `60` | Cooldown period after success in seconds (minimum: 15) |
| `IGNITE_CODEX_MODEL` | empty | Codex ignition model (defaults to Luna) |
| `IGNITE_CLAUDE_MODEL` | empty | Claude ignition model (defaults to Haiku) |

## Account Naming

When a provider has one account, notifications show only the provider name (`Claude`, `ChatGPT`, `Gemini`).

With multiple accounts under one provider, Keeper appends a suffix using the first two and last two characters of the email username:

```text
alice.work@example.com → ChatGPT#al~rk
bob.team@example.net   → ChatGPT#bo~am
```

This suffix appears only in notifications and logs. Account scheduling and API requests remain keyed by `auth_index`.

## Files

| File | Purpose |
| --- | --- |
| `watcher.py` | Queries quotas, generates and sends notifications |
| `scheduler.py` | Main entrypoint: multi-account scheduling and Window Ignition |
| `test_scheduler.py` | Unit tests |
| `compose.yaml` | Docker Compose configuration |
| `.env.example` | Environment variable template |
| `data/state.json` | Generated runtime state |

## Security

- Keeper does not read credential files from disk directly. Quota queries and ignition calls run through the CPA Management API, using credentials managed on the CPA server by `auth_index`. If an Antigravity credential lacks a `project_id`, Keeper retrieves the file through the Management API to inspect it.
- The container runs with a read-only root filesystem and drops all Linux capabilities.
- Never commit `.env`, Bark device keys, management secrets, CPA credential files, or `data/state.json`. `.gitignore` excludes `.env` and `data/`.
