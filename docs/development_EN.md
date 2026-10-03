# Development

[中文](./development.md)

## Environment

The main service uses only the Python standard library. Production Compose uses `python:3.12-alpine`. The CPA ignition bridge is a Go dynamic plugin; its `go.mod` currently depends on `github.com/router-for-me/CLIProxyAPI/v8 v8.0.4`.

Common tools:

- Python 3.12
- Docker and Docker Compose
- CPA with dynamic plugin support
- Docker access to `golang:1.26-bookworm` when building the plugin

Build the plugin for the same platform and CPU architecture as CPA. The repository `build.sh` generates the `.so` for the current Docker host architecture.

## Layout

| Path | Contents |
| --- | --- |
| `scheduler.py` | Runtime entry point and ignition scheduler |
| `watcher.py` | CPA client, quota parsing, notifications, state persistence |
| `test_scheduler.py` | Unit tests for scheduling, model selection, bridge calls, notifications, and failure protection |
| `keeper.example.toml` | Ignition and provider configuration example |
| `.env.example` | Runtime environment variables |
| `compose.yaml` | Keeper container example |
| `cpa_plugin/quota-keeper-bridge/` | CPA bridge source and build script |
| `docs/architecture_EN.md` | Data flow, state, and design constraints |

## Unit tests

Tests use fake clients and should not send real model requests:

```bash
python3 -m unittest -v
```

Run at least these checks before committing:

```bash
git diff --check
python3 -m unittest -v
```

The test suite focuses on:

- rolling vs fixed 5-hour reset detection;
- daytime scheduling boundaries;
- provider model selection order;
- exact `auth_index` on ignition requests;
- ignition bridge protocol;
- retry backoff and circuit breaking;
- Bark thresholds, recovery baseline, and reset reminders;
- Did Codex Reset deduplication.

## Build the CPA ignition bridge

Run:

```bash
sh cpa_plugin/quota-keeper-bridge/build.sh
```

The script writes:

```text
cpa_plugin/quota-keeper-bridge/quota-keeper-bridge.so
```

Generated `.so` and cgo `.h` files are ignored by `.gitignore` and should not be committed.

Rebuild and reinstall the plugin after changing any of these:

- `cpa_plugin/quota-keeper-bridge/main.go`
- `go.mod` / `go.sum`
- CPA plugin ABI or the `host.model.execute` / `host.auth.get_runtime` contract

After installing the plugin into CPA's configured `plugins.dir`, restart CPA to reload the dynamic library.

## Local and container commands

Refresh quota once without ignition:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

Show upcoming ignition times:

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

Follow runtime logs:

```bash
docker compose logs -f quota-keeper
```

`--once` and `--show-schedule` still require a reachable CPA Management API and a valid management key because they read live quota.

## Change a provider adapter

Every quota adapter must return the same group/window shape. When changing a provider:

1. keep a stable `id`, `label`, `remaining`, and optional `reset` for each window;
2. make any auto-ignitable 5-hour window recognizable by `is_five_hour_window()`;
3. add unit tests for new parsing rules and edge cases;
4. update README when user-visible quota coverage, configuration, or defaults change;
5. update `docs/architecture_EN.md` when data flow, interfaces, or state structure change.

Do not add provider-native model HTTP details to Keeper. Ignition should go through `/quota-keeper/ignite` and CPA Provider Executor.

## Change model selection

Automatic model selection is centralized in `choose_model()`. Update the matching tests when changing a rule. Explicit configuration always wins, and the configured model must appear in CPA's model list for that credential.

Antigravity can expose multiple independent quota groups. Per-group overrides use:

```toml
[providers.antigravity.models]
"Gemini" = "..."
"Claude / GPT" = "..."
```

Do not document a model ID observed at one point in time as a permanent model. CPA supplies the list; documentation should describe selection rules.

## Change state

`data/state.json` survives container restarts. New fields should have defaults so old state continues to load. If a group key changes, add migration logic instead of requiring users to delete the state file.

Notification and scheduler state share the same JSON file. Keep writes through `watcher.save_state()`, which writes a temporary file and replaces the old file atomically.

## Live ignition tests

Calling `/v0/management/quota-keeper/ignite` sends a real model request, consumes quota, and may start a new 5-hour window. Normal development should use unit tests. Run a live ignition only when validating CPA/provider integration, after checking the account, model, and current window state.

An HTTP 2xx only proves that model execution returned. Read quota again and confirm that a rolling reset became a fixed future reset before treating the window as started.

## Documentation check

Before finishing a code change, follow `AGENTS.md`:

- user-visible behavior, installation, configuration, commands, and limits belong in README;
- architecture, interfaces, state, and development constraints belong in `docs/`;
- keep the Chinese and English README versions in sync;
- document the current state, not the change history.
