# CPA Quota Keeper

[English](./README_EN.md)

为 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）监控订阅额度，通过 [Bark](https://github.com/Finb/Bark) 推送低额度、重置和恢复提醒。可选的 **Window Ignition（点火）** 在真实 5 小时额度重置后发送最小请求，启动下一轮窗口。

支持同一 Provider 多账号，无需维护账号列表。监控和点火可独立开关。

| Provider | 监控范围 | 默认点火 |
| --- | --- | --- |
| ChatGPT / Codex | 5 小时、7 天 | 开启 |
| Claude | 5 小时、7 天、Fable | 开启 |
| Antigravity | Gemini、Claude / GPT 等额度组 | 关闭 |
| Grok / xAI | xAI 返回的 billing 窗口 | 关闭 |

所有 Provider 默认开启监控。点火仅适用于上游返回的真实 5 小时窗口；Grok 只有周或月额度时，即使开启点火也不会发送点火请求。

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

- [`.env.example`](./.env.example)：CPA、Bark、通知阈值、轮询和时区。默认每 300 秒刷新额度，时区为 UTC+8；`POLL_INTERVAL` 不影响点火调度。
- [`keeper.example.toml`](./keeper.example.toml)：点火时间和 Provider 设置。修改复制后的 `keeper.toml`；旧版 `IGNITE_*` 环境变量仍可作为兼容配置。

### 点火规则

默认每天 07:00 开始，之后跟随各窗口的 `reset_at + 3 秒`，已开始的窗口沿用其重置时间。最后一轮可延至 22:30，更晚的重置等到次日 07:00。失败后 5 分钟重试同一账号。

点火通过 CPA Management API 的 `auth_index` 固定到具体账号，不会失败后切换账号。默认选择该账号可用的轻量模型：Codex 优先 Luna，Claude 优先 Haiku，Antigravity Gemini 优先 Flash Lite / Flash，其他组和 Grok 优先轻量文本模型。

用 `monitor`、`ignite` 分别控制监控和点火，`model` 可覆盖自动模型选择。例如，修改已有的 Claude 配置段：

```toml
[providers.claude]
monitor = true
ignite = true
model = "your-model-id"
```

Antigravity 开启点火后默认覆盖所有检测到的 5 小时额度组。可用 `groups` 限定范围，并按组指定模型：

```toml
[providers.antigravity]
monitor = true
ignite = true
groups = ["Gemini"]

[providers.antigravity.models]
"Gemini" = "gemini-3.5-flash-lite"
```

## 通知

- 剩余额度跨过 **50%、20%、10%、0%** 时提醒。
- 所有窗口重置前 1 小时提醒；7 天窗口另在重置前 1 天提醒。
- 额度恢复后通知一次。

多账号通知和日志使用脱敏后缀，例如 `alice.work@example.com` → `ChatGPT#al~rk`；路由仍使用 `auth_index`。

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
