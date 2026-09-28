# CPA Quota Keeper

[English](./README_EN.md)

CPA Quota Keeper 给 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）增加两件事：

1. 读取订阅额度，在额度下降、即将重置或恢复时通过 [Bark](https://github.com/Finb/Bark) 通知。
2. 对启用了 **Window Ignition** 的 Provider，在真实 5 小时额度重置后发送一个最小请求，让下一轮 5 小时窗口立即开始计时。

监控和点火是两套开关。可以监控一个 Provider，但不让它自动消耗额度。

## 支持的 Provider

| Provider | 监控 | Window Ignition |
| --- | --- | --- |
| ChatGPT / Codex | 5 小时、7 天 | 可配置 |
| Claude | 5 小时、7 天、Fable | 可配置 |
| Antigravity | Gemini、Claude / GPT 等额度组 | 可配置 |
| Grok / xAI | xAI 返回的 billing 窗口 | 检测到真实 5 小时窗口时可配置 |

Keeper 不按 Provider 名字猜窗口长度。Provider adapter 只要能把上游额度归一化成 **5 小时窗口 + reset 时间**，调度器就能使用同一套 Window Ignition 逻辑。

Grok / xAI 的账号如果只返回周额度或月额度，Keeper 会照常监控这些窗口，但不会伪造一个 5 小时窗口，也不会为它点火。

支持同一 Provider 配置多个账号。新增账号后不需要修改账号列表。

## 配置

复制两个配置文件：

```bash
cp .env.example .env
cp keeper.example.toml keeper.toml
```

- `.env`：CPA、Bark、轮询和时区。
- `keeper.toml`：Window Ignition 和每个 Provider 的行为。

默认配置：

```toml
[providers.codex]
monitor = true
ignite = true

[providers.claude]
monitor = true
ignite = true

[providers.antigravity]
monitor = true
ignite = false

[providers.grok]
monitor = true
ignite = false
```

`monitor = true` 表示读取额度并发送通知。

`ignite = true` 表示这个 Provider 的真实 5 小时窗口会加入自动点火调度。

所以可以像上面的默认配置一样：**继续监控 Antigravity，但不开点火。**

### Window Ignition 时间

```toml
[window_ignition]
enabled = true
start_hour = 7
end_hour = 22
end_grace_minutes = 30
grace_seconds = 3
failure_retry_seconds = 300
post_success_hold_seconds = 60
```

每天 07:00 是起点。之后不按固定的 12:00、17:00、22:00 发请求，而是跟随每个额度窗口自己的 `reset_at`：

```text
07:00 点火
  ↓
读取真实 reset_at
  ↓
reset_at + 3 秒再次点火
  ↓
读取新的 reset_at
  ↓
继续
```

如果账号已经提前使用过，Keeper 会沿用当前窗口，不强行对齐到整点。

默认允许最后一轮漂移到 22:30。更晚的重置等到第二天 07:00。

点火失败后默认 5 分钟重试同一个 credential，不会切到另一个账号。

### Antigravity

Antigravity 一个账号可以同时有多个独立的 5 小时额度组，例如 Gemini 和 Claude / GPT。

开启：

```toml
[providers.antigravity]
monitor = true
ignite = true
```

默认会点火所有检测到的 5 小时额度组。

只想点火其中一组：

```toml
[providers.antigravity]
monitor = true
ignite = true
groups = ["Gemini"]
```

还可以给不同额度组指定模型：

```toml
[providers.antigravity.models]
"Gemini" = "gemini-3.5-flash-lite"
"Claude / GPT" = "gpt-oss-120b-medium"
```

不指定时，Keeper 会从该 credential 实际可用的模型里选择较轻的模型。

### Grok / xAI

开启方式相同：

```toml
[providers.grok]
monitor = true
ignite = true
model = ""
```

Keeper 会读取 xAI billing period。只有上游实际返回约 5 小时的窗口时，它才会加入 Window Ignition。

如果账号只返回 weekly / monthly billing，`ignite = true` 也不会制造额外请求。

## 点火请求

所有 Provider 共用同一条原则：请求尽可能小，并固定到具体 credential。

- ChatGPT / Codex：优先 Luna。
- Claude：优先 Haiku。
- Antigravity Gemini：优先 Flash Lite / Flash。
- Antigravity Claude / GPT：优先较轻的可用模型。
- Grok：优先非图像、非视频的轻量模型。

可以用 Provider 的 `model` 覆盖自动选择：

```toml
[providers.claude]
monitor = true
ignite = true
model = "your-model-id"
```

请求通过 CPA Management API 的 `auth_index` 绑定 credential。一个账号失败时，不会 fallback 到另一个账号。

## 通知

默认在剩余额度跨过这些阈值时提醒：

```text
50%
20%
10%
0%
```

此外：

- 所有额度窗口在重置前 1 小时提醒一次。
- 7 天窗口还会在重置前 1 天提醒一次。
- 额度恢复后通知一次。

示例：

```text
⚠️ Claude · 7d 48%

5h：96% | 04时 | 09/28 13:50
7d：48% | 03天 | 10/01 14:00
```

同一 Provider 有多个账号时，Keeper 会自动用邮箱用户名的前 2 个和后 2 个字符生成脱敏后缀：

```text
alice.work@example.com → ChatGPT#al~rk
bob.team@example.net   → ChatGPT#bo~am
```

这个名字只用于通知和日志。路由仍按 `auth_index`。

## 部署

### 1. 准备配置

```bash
cp .env.example .env
cp keeper.example.toml keeper.toml
```

配置 Bark：

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

提供 CPA Management API 密钥：

```env
CPA_MANAGEMENT_KEY=your_management_key
```

也可以继续使用 `/opt/cliproxyapi/watcher.env` 中的 `MANAGEMENT_PASSWORD`。

默认 Docker 配置假设：

| 项目 | 默认值 |
| --- | --- |
| CPA 容器 | `cliproxyapi` |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker 网络 | `cliproxyapi_default` |
| CPA 配置文件 | `/opt/cliproxyapi/config.yaml` |

环境不同的话修改 `compose.yaml`。

### 2. 启动

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## 常用命令

只刷新额度，不执行点火：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

查看当前点火计划：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

运行测试：

```bash
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## .env

```env
CPA_BASE_URL=http://cliproxyapi:8317/v0/management
CPA_CONFIG_FILE=/run/cliproxyapi/config.yaml
STATE_FILE=/data/state.json
CPA_MANAGEMENT_KEY=

BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA

POLL_INTERVAL=300
NOTICE_THRESHOLD=50
LOW_THRESHOLD=20
CRITICAL_THRESHOLD=10
NOTIFY_RECOVERY=true

TZ_OFFSET_HOURS=8
REQUEST_TIMEOUT=20
KEEPER_CONFIG=/app/keeper.toml
```

`POLL_INTERVAL` 只控制额度刷新和通知。Window Ignition 根据真实 reset 时间单独调度。

旧版 `IGNITE_*` 环境变量仍可作为兼容配置使用，但新部署建议统一写进 `keeper.toml`。

## 文件

| 文件 | 用途 |
| --- | --- |
| `watcher.py` | 读取额度、生成 Bark 通知、保存通知状态 |
| `scheduler.py` | Provider adapter、5 小时窗口调度和 Window Ignition |
| `keeper.example.toml` | Provider 与点火配置模板 |
| `keeper.toml` | 本地配置，不提交到 Git |
| `test_scheduler.py` | 单元测试 |
| `compose.yaml` | Docker Compose 配置 |
| `.env.example` | 环境变量模板 |
| `data/state.json` | 运行状态，自动生成 |

## 安全

不要提交：

- `.env`
- `keeper.toml`
- Bark device key
- CPA Management 密钥
- CPA auth 文件
- `data/state.json`

`.gitignore` 已忽略 `.env`、`keeper.toml` 和 `data/`。

额度读取和点火都通过 CPA Management API 完成。点火使用指定的 `auth_index`，不会经过普通负载均衡。
