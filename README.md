# CPA Quota Keeper

[English](./README_EN.md)

CPA Quota Keeper 为 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）监控订阅额度，并用 [Bark](https://github.com/Finb/Bark) 发送通知。

它还能做两件事：

- **Window Ignition（点火）**：在 5 小时额度窗口需要启动时，发送一个很小的请求。
- **Did Codex Reset 转发**：把 [Did Codex Reset](https://didcodexreset.com/zh.html) 的 Codex 重置信号转成 Bark 通知。

同一个 Provider 可以有多个账号。监控、点火和 Codex 重置信号转发可以分别开关。

| Provider | 监控 | 默认点火 |
| --- | --- | --- |
| ChatGPT / Codex | 5 小时、7 天 | 开启 |
| Claude | 5 小时、7 天、Fable | 开启，但只点火 5 小时窗口 |
| Antigravity | Gemini、Claude / GPT 等额度组 | 关闭；启用后默认只点火 Gemini |
| Grok / xAI | xAI 返回的 billing 窗口 | 关闭 |

如果上游没有 5 小时窗口，Keeper 不会发送点火请求。

## 快速开始

需要：

- 已运行的 CPA
- Docker Compose

```bash
git clone https://github.com/PosvdM/cpa-quota-keeper.git
cd cpa-quota-keeper
cp .env.example .env
cp keeper.example.toml keeper.toml
```

在 `.env` 中填写：

```env
CPA_MANAGEMENT_KEY=your_management_key
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

默认 [compose.yaml](./compose.yaml) 使用这些路径：

| 项目 | 默认值 |
| --- | --- |
| CPA 容器 | `cliproxyapi` |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker 网络 | `cliproxyapi_default` |
| CPA 配置文件 | `/opt/cliproxyapi/config.yaml` |
| 额外环境文件 | `/opt/cliproxyapi/watcher.env` |

如果你的环境不同，请修改 `compose.yaml`。

如果 `watcher.env` 里已经有 `MANAGEMENT_PASSWORD`，可以直接沿用。否则删除对应的 `env_file`，并设置 `CPA_MANAGEMENT_KEY`。

启动：

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## 配置

主要配置文件：

- [`.env.example`](./.env.example)：CPA、Bark、通知阈值、轮询间隔、时区。
- [`keeper.example.toml`](./keeper.example.toml)：点火时间、Provider、Antigravity 分组、Did Codex Reset。

默认每 300 秒刷新一次额度，时区为 UTC+8。旧版 `IGNITE_*` 环境变量仍可作为兼容配置。

## 点火

### 调度

默认从每天 07:00 开始。

窗口已经启动时，下一次点火按上游返回的：

```text
reset_at + 3 秒
```

来安排。

当天最后一轮可以延到 22:30。更晚的重置会等到次日 07:00。

点火失败后，5 分钟后重试同一个账号。

### 识别还没启动的 5 小时窗口

有些 Provider 会在窗口还没启动时返回一个假的 `reset_at`。这个时间通常一直保持在“当前时间 + 5 小时”附近，并会随着时间向后移动。

Keeper 会比较连续两次观测：

- 如果 `reset_at` 跟着当前时间一起移动，就把它当成占位值。
- 如果 `reset_at` 固定下来，就把它当成已经启动的窗口。

这样不会把滑动占位时间当成真正的重置时间。

### 确认点火是否成功

模型接口返回成功，不代表 5 小时窗口一定已经启动。

点火后，Keeper 会在大约这些时间重新读取额度：

```text
5s → 10s → 15s → 30s → 15s
```

只有看到固定的未来 `reset_at`，才会确认点火成功。

确认后，下一次点火继续使用上游返回的 `reset_at`。Keeper 不会自己再加一个固定的 5 小时等待周期。

### 账号路由

点火通过 CPA Management API 的 `auth_index` 固定到具体账号。

一个账号点火失败时，不会切换到另一个账号。

### 模型优先级

显式配置的 `model` 或分组 `models` 优先级最高。

没有显式配置时：

- **ChatGPT / Codex**：最新 Luna，找不到再向旧版 Luna 顺延。
- **Claude**：最新非 thinking Haiku，找不到再向旧版 Haiku 顺延。
- **Antigravity / Gemini**：最新非 image Flash，向旧版 Flash 顺延；没有 Flash 时尝试其他非 Pro Gemini；最后使用最新 Pro，再向旧版 Pro 顺延。
- **Antigravity / Claude / GPT**：Haiku → Sonnet → Opus → GPT-OSS → 其他非 thinking 文本模型。这个分组默认不点火。
- **Grok / xAI**：优先名称中带 `fast`、`mini` 的文本模型。

如果模型列表以后出现更新的 Luna、Haiku 或 Flash，Keeper 会按版本号自动优先新版本。

### 点火 Prompt

所有 Provider 使用同一条 prompt：

```text
This is an automated quota-window trigger. Do not think, reason, deliberate, use tools, or perform any other task. Reply with exactly OK and nothing else.
```

Codex 还会设置：

- `reasoning.effort = none`
- `tools = []`
- `store = false`

其他 Provider 的输出也会限制得很短。

## Provider 和分组

`monitor` 控制监控，`ignite` 控制点火。

例如：

```toml
[providers.claude]
monitor = true
ignite = true
model = ""
```

Claude 的 7 天窗口和 Fable 5 只监控，不点火。

Antigravity 默认只允许 Gemini 进入点火：

```toml
[providers.antigravity]
monitor = true
ignite = false
model = ""
groups = ["Gemini"]
```

要启用 Antigravity 点火，把 `ignite` 改成 `true`。只有 `groups` 中列出的分组会被点火。

也可以单独指定分组模型：

```toml
[providers.antigravity.models]
"Gemini" = "your-model-id"
```

## Bark 通知

额度跨过这些阈值时会通知：

```text
50% → 20% → 10% → 0%
```

其他通知开关：

- `NOTIFY_RESET_REMINDERS`：重置前提醒
- `NOTIFY_RECOVERY`：额度恢复提醒

示例配置默认关闭这两项，避免和点火产生重复提醒。低额度告警不受影响。

剩余时间使用短单位：

- `d`：天
- `h`：小时
- `m`：分钟

例如：`06d`、`05h`、`30m`。

多账号通知会使用脱敏后缀，例如：

```text
alice.work@example.com → ChatGPT#al~rk
```

实际请求仍然使用 `auth_index` 路由。

## Did Codex Reset → Bark

可以轮询 Did Codex Reset 的公开 API，并把新记录转成 Bark：

```toml
[codex_reset_updates]
enabled = true
poll_seconds = 300
notify_current_pending = true
```

规则：

- 最低轮询间隔是 300 秒。
- 第一次启用时，只推送当前仍在等待执行的排期，不补发旧历史。
- 后续按稳定事件键去重。
- 上游人工记录即使更换 ID，也不会重复刷同一条历史通知。
- 有 `announcedAt` 的记录会附上 Did Codex Reset 中文详情页链接。
- 点击 Bark 通知可以打开详情页。
- 通知正文显示时间、置信度和适用套餐，不显示来源账号。

Did Codex Reset 是第三方监测站。它的通知代表监测信号，不代表 OpenAI 官方确认。

## 常用命令

```bash
# 只刷新额度，不点火
docker compose run --rm quota-keeper python /app/scheduler.py --once

# 查看点火计划
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# 运行测试
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

不要提交密钥、CPA auth 文件或运行状态。

`.gitignore` 已忽略：

```text
.env
keeper.toml
data/
```
