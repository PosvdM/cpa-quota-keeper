# CPA Quota Watcher

[中文](./README.md)

Monitor Claude and Antigravity quotas in CLIProxyAPI and send alerts through Bark.

## Features

- Refreshes quota data directly instead of reading cached values from the management page.
- Supports Claude 5-hour, 7-day, and Fable quotas.
- Supports Antigravity Gemini and Claude / GPT quota groups.
- Sends alerts when remaining quota crosses `50%`, `20%`, `10%`, or `0%`.
- Does not repeat alerts while a quota stays in the same threshold range.
- Sends one recovery alert when quota returns to a healthier range.
- Sends one reset reminder for every quota window within 1 hour of reset.
- Sends an extra reset reminder for 7-day windows within 1 day of reset.
- Keeps the 1-day and 1-hour reminders separate, so restarting the watcher does not resend the same reminder.
- Reset reminder bodies include every window in the same quota group.
- Recovery is shown in the title; the body keeps showing the current percentage.
- Stores state in `data/state.json`.

## Notification examples

Quota alert:

```text
⚠️ CPA · Claude · 7d 48%

5h：83% | 02时 | 09/28 05:59
7d：48% | 03天 | 10/01 13:59
```

Reset reminder:

```text
⏰ CPA · Gemini · 5h 重置提醒

5h：94% | 07分 | 09/28 04:16
7d：95% | 03天 | 10/01 13:59
```

Recovery:

```text
✅ CPA · Claude · 7d 已恢复

5h：83% | 02时 | 09/28 05:59
7d：100% | 03天 | 10/01 13:59
```

## Time format

Each line shows both a compact remaining time and the exact reset time:

```text
7d：48% | 03天 | 10/01 13:59
```

The compact value is rounded in the unit the remaining time currently falls into:

- Under 1 hour: `59分`, `60分`
- 1 hour to under 1 day: `02时`, `24时`
- 1 day or more: `03天`, `04天`

## Setup

### 1. Create the environment file

```bash
cp .env.example .env
```

Set your Bark URL:

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. Provide the CLIProxyAPI management key

You can put it in `.env`:

```env
CPA_MANAGEMENT_KEY=your_management_key
```

The included `compose.yaml` also reads:

```text
/opt/cliproxyapi/watcher.env
```

That file can contain:

```env
MANAGEMENT_PASSWORD=your_management_password
```

Use either method.

### 3. Check the Docker network

The included configuration assumes:

- the CLIProxyAPI container is named `cliproxyapi`
- the Management API is available at `http://cliproxyapi:8317/v0/management`
- the Docker network is named `cliproxyapi_default`

Change `compose.yaml` or `.env` if your setup is different.

### 4. Start the watcher

```bash
docker compose up -d
```

View logs:

```bash
docker logs -f cpa-quota-watcher
```

Run one polling cycle:

```bash
docker compose run --rm quota-watcher python /app/watcher.py --once
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
```

The watcher polls every 5 minutes by default.

Reset reminder rules:

```text
All windows: 0 < time to reset <= 1 hour
7-day windows: 1 hour < time to reset <= 1 day
```

A reminder is sent on the first poll after a window enters one of these ranges.

## Files

- `watcher.py`: quota polling, notifications, and state handling
- `compose.yaml`: Docker Compose configuration
- `.env.example`: environment variable example
- `.gitignore`: excludes local secrets and runtime state
- `README.md`: Chinese README

## Security

Do not commit:

- `.env`
- Bark device keys
- CLIProxyAPI management keys or passwords
- CLIProxyAPI auth files
- `data/state.json`

The included `.gitignore` excludes `.env` and `data/`.

## How it works

The watcher calls upstream quota endpoints through the CLIProxyAPI Management API. It does not need direct access to Claude or Antigravity account credential files.
