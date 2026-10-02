# CPA Quota Keeper

[English](./README_EN.md)

为 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）监控订阅额度，通过 [Bark](https://github.com/Finb/Bark) 接收低额度提醒，并自动启动下一轮 5 小时额度窗口。

## 功能

- **额度监控**：支持同一服务的多个账号，额度降至 50%、20%、10% 和耗尽时发送通知。
- **自动点火**：在设定时段发送极小请求，启动额度窗口。几乎不消耗额度，可单独关闭。
- **Codex 重置提醒**：可转发 [Did Codex Reset](https://didcodexreset.com/zh.html) 的重置信号，通知支持跳转详情页。

| 服务 | 监控范围 | 默认点火 |
| --- | --- | --- |
| ChatGPT / Codex | 5 小时、7 天额度 | 开启 |
| Claude | 5 小时、7 天、Fable 额度 | 仅 5 小时窗口 |
| Antigravity | Gemini、Claude / GPT 等额度组 | 关闭；启用后默认仅 Gemini |
| Grok / xAI | 上游返回的额度窗口 | 关闭 |

## 快速开始

需要已运行的 CPA、可用的管理密钥，以及 Docker Compose。

### 1. 下载并创建配置

```bash
git clone https://github.com/PosvdM/cpa-quota-keeper.git
cd cpa-quota-keeper
cp .env.example .env
cp keeper.example.toml keeper.toml
```

在 `.env` 中填写 CPA 管理密钥和 Bark 推送地址：

```env
CPA_MANAGEMENT_KEY=your_management_key
BARK_URL=https://api.day.app/your_device_key
```

### 2. 对照 CPA 部署修改 Compose

[compose.yaml](./compose.yaml) 默认连接以下环境，请按实际部署修改：

| 配置项 | 默认值 |
| --- | --- |
| CPA 管理接口 | `http://cliproxyapi:8317/v0/management` |
| 已有 Docker 网络 | `cliproxyapi_default` |
| 宿主机 CPA 配置文件 | `/opt/cliproxyapi/config.yaml` |
| 额外环境文件 | `/opt/cliproxyapi/watcher.env` |

如果没有 `watcher.env`，删除 `env_file` 中对应的一行，保留 `.env`。如果已有文件包含 `MANAGEMENT_PASSWORD`，也可以沿用，`CPA_MANAGEMENT_KEY` 留空即可。

管理接口地址在 Compose 的 `environment` 中设置，只改 `.env` 不会覆盖它。

### 3. 启动

```bash
docker compose up -d
docker compose logs -f quota-keeper
```

## 配置与日常使用

| 文件 | 可调整的内容 |
| --- | --- |
| [`.env`](./.env.example) | CPA 密钥、Bark 地址、通知阈值、轮询间隔、时区 |
| [`keeper.toml`](./keeper.example.toml) | 点火开关和时段、各服务监控与点火、Antigravity 分组、Codex 重置提醒 |

默认每 5 分钟刷新额度，时区为 UTC+8。点火从每天 07:00 开始，后续跟随额度重置时间，最晚到 22:30。

在 `keeper.toml` 中：

- 只需要监控：将 `[window_ignition]` 下的 `enabled` 改为 `false`。
- 调整某个服务：用对应配置中的 `monitor` 和 `ignite` 分别控制监控和点火。
- 启用 Antigravity 点火：将 `[providers.antigravity]` 下的 `ignite` 改为 `true`；默认仅点火 Gemini。
- 订阅 Codex 重置信号：将 `[codex_reset_updates]` 下的 `enabled` 改为 `true`。

点火模型会自动选择，也可以在配置中指定。完整选项见上方示例文件。

Did Codex Reset 是第三方监测站，其信号不代表 OpenAI 官方确认。示例配置默认关闭额度恢复和重置前提醒，可在 `.env` 中开启。

修改配置后重新创建容器：

```bash
docker compose up -d --force-recreate
```

查看下一次点火时间：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

请保留 `data/` 中的运行状态，避免丢失通知与点火记录。不要提交密钥、CPA 认证文件或运行状态；`.env`、`keeper.toml` 和 `data/` 已被 Git 忽略。
