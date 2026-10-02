# CPA Quota Keeper

[English](./README_EN.md)

为 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）监控订阅额度，通过 [Bark](https://github.com/Finb/Bark) 推送低额度、重置和恢复提醒。可选的 **Window Ignition（点火）** 会发送极小请求启动下一轮真实 5 小时额度窗口；还可把 [Did Codex Reset](https://didcodexreset.com/zh.html) 的 Codex 重置信号转成 Bark 通知。

支持同一 Provider 多账号，无需维护账号列表。监控、点火和 Codex 重置信号转发可独立开关。

| Provider | 监控范围 | 默认点火 |
| --- | --- | --- |
| ChatGPT / Codex | 5 小时、7 天 | 开启 |
| Claude | 5 小时、7 天、Fable | 开启（仅 5 小时；7 天 / Fable 仅监控） |
| Antigravity | Gemini、Claude / GPT 等额度组 | 关闭（启用时默认仅 Gemini） |
| Grok / xAI | xAI 返回的 billing 窗口 | 关闭 |

所有 Provider 默认开启监控。点火只会对检测到的真实 5 小时窗口创建任务；没有 5 小时窗口时不会发送点火请求。

## 快速开始

需要已运行的 CPA 和 Docker Compose。

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

默认 [compose.yaml](./compose.yaml) 使用以下配置；环境不同时请修改：

| 项目 | 默认值 |
| --- | --- |
| CPA 容器 | `cliproxyapi` |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker 网络 | `cliproxyapi_default` |
| CPA 配置文件 | `/opt/cliproxyapi/config.yaml` |
| 额外环境文件 | `/opt/cliproxyapi/watcher.env` |

可沿用 `watcher.env` 中的 `MANAGEMENT_PASSWORD`。若不使用该文件，请删除 `compose.yaml` 中对应的 `env_file` 条目，并设置 `CPA_MANAGEMENT_KEY`。

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## 配置

- [`.env.example`](./.env.example)：CPA、Bark、通知阈值、轮询和时区。默认每 300 秒刷新额度，时区为 UTC+8；`POLL_INTERVAL` 不控制精确点火时间。
- [`keeper.example.toml`](./keeper.example.toml)：点火时间、Provider、Antigravity 分组和 Did Codex Reset 转发设置。修改复制后的 `keeper.toml`；旧版 `IGNITE_*` 环境变量仍可作为兼容配置。

### 点火规则

默认每天 07:00 开始，正常情况下跟随各窗口返回的 `reset_at + 3 秒`。最后一轮可延至 22:30，更晚的重置等到次日 07:00。失败后 5 分钟重试同一账号。

部分 Provider 在窗口尚未启动时会返回一个“始终约为当前时间 + 5 小时”的滑动 `reset_at` 占位值。Keeper 会比较连续观测：如果 `reset_at` 随墙上时间一起向后移动，就把它识别为未启动窗口，而不是把这个占位时间当成真实重置时间。

点火请求返回后不会立即记为成功。Keeper 会按约 5s → 10s → 15s → 30s → 15s 的节奏重新读取额度，只有观察到未来的固定 `reset_at` 才确认点火成功。确认后继续使用上游真实返回的 `reset_at` 调度下一次点火，不人为增加固定 5 小时等待周期。

点火通过 CPA Management API 的 `auth_index` 固定到具体账号，不会失败后切换账号。默认模型选择按轻量家族的最新版本优先，再向旧版本顺延：

- ChatGPT / Codex：最新 **Luna**。
- Claude：最新非 thinking **Haiku**。
- Antigravity / Gemini：最新非 image **Flash** 优先，按版本向旧版顺延；没有 Flash 时先尝试其他非 Pro Gemini，最后再使用最新 Pro。
- Antigravity / Claude / GPT（默认不点火）：Haiku → Sonnet → Opus → GPT-OSS → 其他非 thinking 文本模型。
- Grok / xAI：优先名称含 `fast`、`mini` 的文本模型。

模型列表中若以后出现更新的 Luna / Haiku / Flash，Keeper 会按版本数字自动优先新版本，无需手工改死模型 ID。设置 `model` 或分组 `models` 时，显式配置永远优先于自动选择。

所有点火 Provider 使用同一条严格 prompt：

```text
This is an automated quota-window trigger. Do not think, reason, deliberate, use tools, or perform any other task. Reply with exactly OK and nothing else.
```

Codex 请求同时设置 `reasoning.effort = none`、空工具列表和 `store = false`；Claude、Antigravity 和 xAI 的点火响应也限制为极小输出。

### Provider 与分组

用 `monitor`、`ignite` 分别控制监控和点火，`model` 可覆盖自动模型选择：

```toml
[providers.claude]
monitor = true
ignite = true
model = ""
```

Claude 的 7 天和 Fable 5 窗口始终只监控，不参与自动点火。

Antigravity 示例配置默认只允许 Gemini 分组进入自动点火；`Claude / GPT` 仍会监控，但不会消耗它的小额度：

```toml
[providers.antigravity]
monitor = true
ignite = false
model = ""
groups = ["Gemini"]
```

如果需要启用 Antigravity 点火，把 `ignite` 改成 `true` 即可；只有 `groups` 列出的分组会被点火。也可以按组指定模型：

```toml
[providers.antigravity.models]
"Gemini" = "your-model-id"
```

## 通知

- 剩余额度跨过 **50%、20%、10%、0%** 时提醒。
- `NOTIFY_RESET_REMINDERS` 控制重置前提醒。
- `NOTIFY_RECOVERY` 控制额度恢复提醒。
- 示例配置默认把后两项关闭，避免开启 Window Ignition 后重复打扰；低额度告警不受影响。
- 通知中的剩余时间使用紧凑单位：`d`（天）、`h`（小时）、`m`（分钟），例如 `06d`、`05h`、`30m`。
- 多账号通知和日志使用脱敏后缀，例如 `alice.work@example.com` → `ChatGPT#al~rk`；路由仍使用 `auth_index`。

### Did Codex Reset → Bark

可选地轮询 Did Codex Reset 的公开 API，把新发布的 Codex 全局重置、重置卡等信号转成 Bark：

```toml
[codex_reset_updates]
enabled = true
poll_seconds = 300
notify_current_pending = true
```

- 最低轮询间隔为 300 秒。
- 第一次开启时只推送当前仍在等待执行的排期，不补发旧历史。
- 后续按稳定事件键去重；对上游可能变化 ID 的人工记录也不会重复刷历史通知。
- 有 `announcedAt` 的记录会给 Bark 附上 Did Codex Reset 中文详情页链接，点击通知即可跳转。
- 通知正文保留时间、置信度和适用套餐，不显示来源账号。
- Did Codex Reset 是第三方监测站；这些通知是监测信号，不是 OpenAI 官方保证。

## 常用命令

```bash
# 只刷新额度，不点火
docker compose run --rm quota-keeper python /app/scheduler.py --once

# 查看点火计划
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# 运行测试
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

不要提交密钥、CPA auth 文件或运行状态。`.gitignore` 已忽略 `.env`、`keeper.toml` 和 `data/`。
