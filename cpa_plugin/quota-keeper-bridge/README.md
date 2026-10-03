# Quota Keeper CPA bridge

This small CPA plugin lets Quota Keeper run ignition requests through CPA's normal model executor while locking the request to one exact `auth_index`.

The plugin resolves `auth_index` to CPA's runtime credential ID, pins that credential, and calls the built-in provider executor. Quota Keeper therefore does not need to reproduce provider-specific model URLs, user agents, OAuth refresh logic, or wire formats.

Build on the same CPU architecture as CPA:

```bash
sh cpa_plugin/quota-keeper-bridge/build.sh
```

Copy `quota-keeper-bridge.so` into CPA's plugin directory, then enable it in CPA:

```yaml
plugins:
  enabled: true
  dir: "plugins"
  configs:
    quota-keeper-bridge:
      enabled: true
      priority: 1
```

Restart CPA after changing the plugin or its configuration.

The plugin registers this authenticated Management API route:

```text
POST /v0/management/quota-keeper/ignite
```

It is intended for Quota Keeper only. The route still requires the normal CPA management key.

Implementation details and the Keeper-side data flow are documented in [Architecture](../../docs/architecture_EN.md). Build and change conventions are in [Development](../../docs/development_EN.md).
