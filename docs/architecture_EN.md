# Architecture

[中文](./architecture.md)

CPA Quota Keeper separates quota monitoring from model ignition. Keeper owns scheduling, state, and notifications. CLIProxyAPI (CPA) owns credentials and model execution.

## Responsibilities

| File or component | Responsibility |
| --- | --- |
| `scheduler.py` | Main loop, provider quota adapters, 5-hour scheduling, model selection, ignition confirmation, failure protection, Did Codex Reset polling |
| `watcher.py` | CPA Management API client, Claude/Antigravity quota parsing, state persistence, quota thresholds, Bark notifications |
| `keeper.toml` | Ignition hours, provider switches, model overrides, failure retry settings |
| `.env` | CPA management key, Bark, polling interval, thresholds, timezone, HTTP timeout |
| `data/state.json` | Notification baseline, scheduler state, Codex reset signal deduplication |
| `cpa_plugin/quota-keeper-bridge` | Sends an ignition request for one `auth_index` through CPA's own model executor |

## Quota monitoring path

Keeper reads available credentials from CPA `/auth-files`, then queries quota per provider. When an upstream quota endpoint is needed, Keeper calls CPA `/v0/management/api-call` with that credential's `auth_index`.

```text
Keeper
  -> CPA /v0/management/auth-files
  -> select credential auth_index
  -> CPA /v0/management/api-call
  -> provider quota endpoint
  -> normalize to group/window
  -> update state.json
  -> send Bark when needed
```

`/api-call` is used for quota and other non-model requests. Keeper can target one account without holding the provider OAuth token directly.

Current quota adapter entry points:

- Codex: `scheduler.fetch_codex_groups()`
- Claude: `watcher.fetch_claude_groups()`
- Antigravity: `watcher.fetch_antigravity_groups()`
- xAI: `scheduler.fetch_xai_groups()`

Every provider is normalized into the same group/window shape. Notification code only consumes normalized `remaining`, `reset`, window IDs, and labels.

## Ignition path

Automatic ignition uses CPA's normal model execution path. Keeper does not construct provider-native model URLs, OAuth requests, or user agents.

```text
scheduler.py
  -> select account and model
  -> POST /v0/management/quota-keeper/ignite
  -> quota-keeper-bridge
  -> host.auth.get_runtime(auth_index)
  -> resolve CPA runtime AuthID
  -> host.model.execute(AuthID, ForcedProvider, Model, Body)
  -> CPA Provider Executor
  -> provider
```

The bridge resolves `auth_index` to CPA's runtime `AuthID`, then calls `host.model.execute` with that `AuthID`. `AuthID` pins the request to one credential and `ForcedProvider` restricts the provider.

The plugin registers:

```text
POST /v0/management/quota-keeper/ignite
```

This is a CPA Management API route and uses CPA's normal management authentication.

Request fields:

| Field | Meaning |
| --- | --- |
| `auth_index` | Account index obtained from CPA's credential list |
| `model` | Model ID selected for this ignition |
| `entry_protocol` | Request protocol supplied by Keeper, such as `openai`, `openai-response`, or `claude` |
| `exit_protocol` | Response protocol; currently the same as `entry_protocol` |
| `body` | Minimal model request body |

Current protocol selection:

| Provider | `entry_protocol` |
| --- | --- |
| Codex | `openai-response` |
| Claude | `claude` |
| Antigravity | `openai` |
| xAI | `openai` |

After `host.model.execute` returns 2xx, the plugin returns execution metadata instead of model text. Keeper then reads quota again. The ignition is successful only when Keeper observes a valid fixed future 5-hour `reset`.

## 5-hour window detection

Some providers expose an unstarted window as a `reset` near "now + 5 hours" that moves forward on every poll. Keeper treats this as a rolling placeholder.

`observe_reset_behavior()` uses two observations:

1. `reset` is about five hours ahead of the current time;
2. between observations, the reset shift is close to the elapsed wall time.

When both are true, `rolling_reset=true` and the scheduler may ignite during the configured daytime window. Once a real window starts, `reset` becomes fixed and the next ignition is scheduled at `reset + grace_seconds`.

After an ignition request returns, `confirm_ignition()` checks quota after `5, 10, 15, 30, 15` seconds. If no future fixed `reset` appears, the ignition is treated as failed.

## Scheduling and failure protection

Default daytime settings come from `keeper.toml`:

- `start_hour = 7`
- `end_hour = 22`
- `end_grace_minutes = 30`
- `grace_seconds = 3`

Quota polling and Did Codex Reset polling both run once at process startup. Later runs use `next_aligned_epoch()` to target the next absolute interval boundary instead of accumulating waits from process start. With the default 300-second interval, both target `:00 / :05 / :10 / ... / :55` each hour. A request may finish a few seconds after the boundary, but the next target remains the next boundary.

A fixed window fires at `reset + grace_seconds`. A target outside the daytime range waits for the next day's `start_hour`.

Failure state is stored under `scheduler` in `state.json`:

- Hard failures: common 4xx responses, 429, missing/unsupported models, unavailable credentials, and a request that returns without a confirmed fixed 5-hour reset. The first hard failure pauses that account until the next day's `start_hour` and sends one Bark alert.
- Transient failures: retry after `failure_retry_seconds * failure_backoff_multiplier^(n-1)`. Defaults are about 5 minutes, then 15 minutes. Reaching `max_transient_failures = 3` pauses the account until the next day.

A valid future fixed `reset` or a confirmed ignition clears consecutive failure and circuit-breaker state.

## Model selection

The model list comes from CPA:

```text
GET /v0/management/auth-files/models?name=<credential>
```

Explicit `model` settings and Antigravity group `models` overrides take priority. Without an override:

- Codex: newest Luna.
- Claude: newest non-thinking Haiku.
- Antigravity / Gemini: newest non-image Flash, then other non-Pro Gemini models, with Pro as the final fallback.
- Antigravity / Claude / GPT: Haiku -> Sonnet -> Opus -> GPT-OSS -> other non-thinking models.
- xAI: exclude image/video models, then prefer `fast`, `mini`, and other Grok models.

Selection only chooses the model ID sent to CPA. Provider request formatting and upstream compatibility remain in CPA's executor.

## State file

`data/state.json` persists three main areas:

- `groups`: quota remaining, reset values, notification severity, and reset-reminder deduplication;
- `scheduler`: reset observations, recent success/failure, retry time, and circuit-breaker state per ignitable account;
- `codex_reset_updates`: seen Did Codex Reset records and polling state.

State readers use defaults for missing fields and include migration for legacy group keys. New state fields should keep the same backward-compatible pattern so users do not need to delete `state.json`.

## Design constraints

Keep these boundaries when changing the implementation:

- Quota requests continue through CPA `/api-call`; model ignition continues through CPA Provider Executor.
- Every ignition carries a concrete `auth_index`, and the bridge resolves and pins its exact `AuthID`.
- A successful `host.model.execute` call does not prove the quota window started; a fixed `reset` does.
- Provider-native model URLs, OAuth refresh, user agents, and protocol compatibility stay out of Keeper.
- Changes to the bridge request schema must update `scheduler.py`, the plugin, and related tests together.
