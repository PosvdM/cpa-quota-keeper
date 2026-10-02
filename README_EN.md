# CPA Quota Keeper

[中文](./README.md)

Monitor subscription quota in [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA), receive low-quota alerts through [Bark](https://github.com/Finb/Bark), and automatically start the next 5-hour quota window.

## Features

- **Quota monitoring**: supports multiple accounts per provider and sends alerts at 50%, 20%, 10%, and zero remaining quota.
- **Window ignition**: sends small requests during configured hours to start quota windows. This uses a small amount of quota and can be disabled separately.
- **Codex reset alerts**: optionally forwards reset signals from [Did Codex Reset](https://didcodexreset.com/), with links to event details.

| Provider | Monitoring | Default ignition |
| --- | --- | --- |
| ChatGPT / Codex | 5-hour and 7-day quota | On |
| Claude | 5-hour, 7-day, and Fable quota | 5-hour windows only |
| Antigravity | Gemini, Claude / GPT, and other quota groups | Off; Gemini only by default when enabled |
| Grok / xAI | Quota windows returned by the provider | Off |

## Quick start

You need a running CPA instance, a valid management key, and Docker Compose.

### 1. Download and create configuration files

```bash
git clone https://github.com/PosvdM/cpa-quota-keeper.git
cd cpa-quota-keeper
cp .env.example .env
cp keeper.example.toml keeper.toml
```

Set your CPA management key and Bark push URL in `.env`:

```env
CPA_MANAGEMENT_KEY=your_management_key
BARK_URL=https://api.day.app/your_device_key
```

### 2. Match Compose to your CPA deployment

[compose.yaml](./compose.yaml) uses the following defaults. Adjust them for your setup:

| Setting | Default |
| --- | --- |
| CPA management API | `http://cliproxyapi:8317/v0/management` |
| Existing Docker network | `cliproxyapi_default` |
| CPA config file on the host | `/opt/cliproxyapi/config.yaml` |
| Extra environment file | `/opt/cliproxyapi/watcher.env` |

If you do not have `watcher.env`, remove its entry from `env_file` and keep `.env`. If an existing file contains `MANAGEMENT_PASSWORD`, you can reuse it and leave `CPA_MANAGEMENT_KEY` empty.

The management API URL is set in Compose's `environment` section. Changing it only in `.env` will not override that value.

### 3. Start

```bash
docker compose up -d
docker compose logs -f quota-keeper
```

## Configuration and everyday use

| File | Settings |
| --- | --- |
| [`.env`](./.env.example) | CPA key, Bark URL, alert thresholds, polling interval, timezone |
| [`keeper.toml`](./keeper.example.toml) | Ignition switch and hours, per-provider monitoring and ignition, Antigravity groups, Codex reset alerts |

Quota refreshes every 5 minutes by default, using UTC+8. Ignition starts at 07:00 each day, then follows quota reset times until 22:30.

In `keeper.toml`:

- For monitoring only, set `enabled = false` under `[window_ignition]`.
- Use each provider's `monitor` and `ignite` settings to control monitoring and ignition separately.
- To enable Antigravity ignition, set `ignite = true` under `[providers.antigravity]`. Only Gemini is included by default.
- To subscribe to Codex reset signals, set `enabled = true` under `[codex_reset_updates]`.

Ignition models are selected automatically. You can also specify them in the configuration. See the example files above for all options.

Did Codex Reset is a third-party monitor; its signals are not official OpenAI confirmations. The example configuration disables quota recovery alerts and pre-reset reminders. You can enable them in `.env`.

Recreate the container after changing configuration:

```bash
docker compose up -d --force-recreate
```

View the next ignition times:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

Keep the runtime state in `data/` to preserve notification and ignition records. Do not commit keys, CPA authentication files, or runtime state. Git already ignores `.env`, `keeper.toml`, and `data/`.
