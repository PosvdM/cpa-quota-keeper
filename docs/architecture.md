# 架构

[English](./architecture_EN.md)

CPA Quota Keeper 把“额度监控”和“模型点火”分成两条路径。Keeper 负责调度、状态和通知；CLIProxyAPI（CPA）负责凭证和模型执行。

## 组件职责

| 文件或组件 | 职责 |
| --- | --- |
| `scheduler.py` | 主循环、Provider 额度适配、5 小时窗口调度、模型选择、点火确认、失败保护、Did Codex Reset 轮询 |
| `watcher.py` | CPA Management API 客户端、Claude/Antigravity 额度解析、状态文件、额度阈值和 Bark 通知 |
| `keeper.toml` | 点火时段、Provider 开关、模型覆盖和失败重试参数 |
| `.env` | CPA 管理密钥、Bark、轮询频率、通知阈值、时区和 HTTP 超时 |
| `data/state.json` | 通知基线、点火调度状态和 Codex 重置信号去重状态 |
| `cpa_plugin/quota-keeper-bridge` | 把指定 `auth_index` 的点火请求交给 CPA 自己的模型执行器 |

## 额度监控路径

Keeper 先从 CPA 的 `/auth-files` 读取可用凭证，再按 Provider 查询额度。需要访问上游额度接口时，Keeper 调用 CPA 的 `/v0/management/api-call`，并传入该凭证的 `auth_index`。

```text
Keeper
  -> CPA /v0/management/auth-files
  -> 选定凭证 auth_index
  -> CPA /v0/management/api-call
  -> Provider 额度接口
  -> 标准化为 group/window
  -> 更新 state.json
  -> 必要时发送 Bark
```

`/api-call` 只用于额度等非模型请求。Keeper 仍能精确指定账号，但不需要直接持有 Provider 的 OAuth token。

当前额度适配入口：

- Codex：`scheduler.fetch_codex_groups()`
- Claude：`watcher.fetch_claude_groups()`
- Antigravity：`watcher.fetch_antigravity_groups()`
- xAI：`scheduler.fetch_xai_groups()`

所有 Provider 最终转换成相同的 group/window 结构。通知逻辑只处理标准化后的 `remaining`、`reset`、窗口 ID 和标签。

## 点火路径

自动点火走 CPA 的正常模型执行链。Keeper 不构造 Provider 原生 URL、OAuth 请求或 User-Agent。

```text
scheduler.py
  -> 选定账号和模型
  -> POST /v0/management/quota-keeper/ignite
  -> quota-keeper-bridge
  -> host.auth.get_runtime(auth_index)
  -> 得到 CPA runtime AuthID
  -> host.model.execute(AuthID, ForcedProvider, Model, Body)
  -> CPA Provider Executor
  -> Provider
```

桥接插件只做两件事：把 `auth_index` 解析为 CPA 的 runtime `AuthID`，然后用该 `AuthID` 调用 `host.model.execute`。`AuthID` 会把请求锁定到指定凭证，`ForcedProvider` 会限制 Provider。

插件注册的接口是：

```text
POST /v0/management/quota-keeper/ignite
```

这条路由属于 CPA Management API，使用 CPA 原有的管理认证。

请求字段：

| 字段 | 说明 |
| --- | --- |
| `auth_index` | Keeper 从 CPA 凭证列表取得的账号索引 |
| `model` | 本次点火选择的模型 ID |
| `entry_protocol` | Keeper 提供的请求协议，如 `openai`、`openai-response`、`claude` |
| `exit_protocol` | 期望的响应协议；当前与 `entry_protocol` 相同 |
| `body` | 最小模型请求体 |

当前协议选择：

| Provider | `entry_protocol` |
| --- | --- |
| Codex | `openai-response` |
| Claude | `claude` |
| Antigravity | `openai` |
| xAI | `openai` |

插件在 `host.model.execute` 返回 2xx 后只返回执行成功信息，不把模型正文传回 Keeper。Keeper 随后重新查询额度，只有看到有效的固定 5 小时 `reset` 才把这次点火记为成功。

## 5 小时窗口判断

有些 Provider 在窗口尚未启动时会返回一个接近“当前时间 + 5 小时”的 `reset`，而且这个时间会随轮询一起向前移动。Keeper 把这种值视为滑动占位。

`observe_reset_behavior()` 用连续两次观测判断：

1. `reset` 距当前时间约 5 小时；
2. 两次观测之间，`reset` 的移动量接近实际经过时间。

满足这两个条件时，`rolling_reset=true`，调度器可以在日间点火。真实窗口启动后，`reset` 会固定，调度器改为按 `reset + grace_seconds` 安排下一次点火。

点火请求返回后，`confirm_ignition()` 按 `5、10、15、30、15` 秒的间隔重新读取额度。整个确认过程没有看到未来的固定 `reset` 时，这次点火按失败处理。

## 调度和失败保护

默认日间点火范围来自 `keeper.toml`：

- `start_hour = 7`
- `end_hour = 22`
- `end_grace_minutes = 30`
- `grace_seconds = 3`

固定窗口在 `reset + grace_seconds` 后触发；超出日间范围时等到下一天的 `start_hour`。

失败状态保存在 `state.json` 的 `scheduler` 下。失败分两类：

- 高风险错误：常见 4xx、429、模型不存在/不支持、凭证不可用，以及“请求返回但没有确认到固定 5h reset”。第一次出现就暂停该账号到下一天的 `start_hour`，并发送一次 Bark。
- 临时错误：按 `failure_retry_seconds * failure_backoff_multiplier^(n-1)` 退避。默认第一次等 5 分钟，第二次等 15 分钟；达到 `max_transient_failures = 3` 时暂停到下一天。

看到有效的未来固定 `reset` 或一次点火确认成功后，会清空连续失败和熔断状态。

## 模型选择

模型列表来自 CPA：

```text
GET /v0/management/auth-files/models?name=<credential>
```

`keeper.toml` 中的显式 `model` 或 Antigravity 分组 `models` 覆盖自动选择。没有覆盖时：

- Codex：优先最新的 Luna。
- Claude：优先最新的非 thinking Haiku。
- Antigravity / Gemini：优先最新的非图片 Flash；再尝试其他非 Pro Gemini；Pro 作为最后一档。
- Antigravity / Claude / GPT：Haiku -> Sonnet -> Opus -> GPT-OSS -> 其他非 thinking 模型。
- xAI：排除图片/视频模型后，依次偏好 `fast`、`mini`、其他 Grok。

自动选择只决定传给 CPA Executor 的模型 ID。Provider 的请求格式和上游兼容由 CPA Executor 处理。

## 状态文件

`data/state.json` 是持久状态，主要包含：

- `groups`：每个额度窗口的剩余额度、reset、通知等级和重置提醒去重信息；
- `scheduler`：每个可点火账号的 reset 观测、最近成功/失败、重试时间和熔断状态；
- `codex_reset_updates`：Did Codex Reset 的已见记录和轮询状态。

代码读取旧状态时使用缺省值，并包含旧 group key 的迁移逻辑。增加状态字段时应保持这种向后兼容方式，避免要求用户删除 `state.json`。

## 设计约束

修改实现时保持以下边界：

- 额度请求继续使用 CPA `/api-call`；模型点火继续使用 CPA Provider Executor。
- 点火必须携带具体 `auth_index`，插件必须把它解析并锁定到具体 `AuthID`。
- `host.model.execute` 成功不等于窗口已启动；固定 `reset` 才是点火成功条件。
- Provider 原生模型 URL、OAuth 刷新、User-Agent 和协议兼容不应重新放回 Keeper。
- 修改桥接请求字段时，需要同时更新 `scheduler.py`、插件和相关测试。
