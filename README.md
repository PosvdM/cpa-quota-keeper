# CPA Quota Keeper

[English](./README_EN.md)

CPA Quota Keeper 是 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）的额度监控和点火工具。它做两件事：

1. 定时读取 Codex、Claude、Antigravity 账号的剩余额度，额度下降、快要重置、已经恢复时通过 [Bark](https://github.com/Finb/Bark) 推送通知。
2. Codex / Claude 的 5 小时额度重置后，自动给该账号发一个最小请求，让下一个 5 小时窗口马上开始计时。

## 支持的额度

| Provider | 额度窗口 | 自动点火 |
| --- | --- | --- |
| Codex | 5 小时、7 天 | 支持 |
| Claude | 5 小时、7 天、Fable | 支持 |
| Antigravity | Gemini、Claude / GPT 额度组 | 不支持，只监控 |

同一 provider 可以有多个账号，新增账号后不需要改配置。

## 通知

以下情况会推送 Bark 通知：

- 剩余额度降到 50%、20%、10%、0%（阈值可配置）
- 任一额度窗口重置前 1 小时
- 7 天窗口重置前 1 天
- 额度重置后恢复正常

通知示例：

```text
⚠️ Claude · 7d 48%

5h：96% | 04时 | 09/28 13:50
7d：48% | 03天 | 10/01 14:00
```

```text
⏰ ChatGPT#bo~am · 5h 重置提醒

5h：19% | 01时 | 09/28 13:55
7d：81% | 06天 | 10/04 21:27
```

```text
✅ Claude · 7d 已恢复

5h：96% | 04时 | 09/28 13:50
7d：100% | 03天 | 10/01 14:00
```

每行依次是：窗口、剩余额度、距离重置的时间、重置时刻。

## 自动点火（Window Ignition）

5 小时窗口从第一次请求开始计时。如果额度重置后很久才用，中间这段时间就浪费了。Keeper 在每次重置后立即发一个请求，让窗口连续滚动。

调度方式：

```text
07:00 第一次点火
  ↓
读取该账号的 reset_at
  ↓
reset_at + 3 秒再次点火
  ↓
读取新的 reset_at，重复
```

- 每个账号按自己的 `reset_at` 调度，互不影响。账号已经在用时，Keeper 沿用当前窗口，不会强行对齐到 07:00。
- 当天最后一次点火最晚在 22:30（`IGNITE_END_HOUR` + `IGNITE_END_GRACE_MINUTES`）。更晚的重置不点火，等到第二天 07:00。
- 点火请求只要求模型回复 `OK`，不带工具。Codex 请求关闭推理，Claude 请求最多输出 4 个 token。
- Codex 默认用 Luna，Claude 默认用 Haiku，也可以用 `IGNITE_CODEX_MODEL` / `IGNITE_CLAUDE_MODEL` 指定。
- 请求通过 `auth_index` 绑定到具体账号。失败后 5 分钟重试同一个账号，不会换用其他账号。

## 部署

### 前提

- CPA 已用 Docker 部署，并开启了 Management API
- 一个 Bark device key

默认配置假设：

| 项目 | 默认值 |
| --- | --- |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker 网络 | `cliproxyapi_default` |
| CPA 配置文件 | `/opt/cliproxyapi/config.yaml` |

和你的环境不同时，修改 `compose.yaml`。

### 1. 创建配置

```bash
cp .env.example .env
```

填写 Bark 地址：

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. 提供 Management 密钥

Keeper 按以下顺序查找密钥，找到一个即可：

1. `.env` 中的 `CPA_MANAGEMENT_KEY`
2. `/opt/cliproxyapi/watcher.env` 中的 `MANAGEMENT_PASSWORD`
3. CPA `config.yaml` 中 `remote-management.secret-key` 的明文值

如果 `config.yaml` 里的 `secret-key` 已经被 CPA 转成 bcrypt 哈希，必须用前两种方式提供明文密钥。

`compose.yaml` 默认会加载 `/opt/cliproxyapi/watcher.env` 并挂载 `/opt/cliproxyapi/config.yaml`。文件不存在时 `docker compose` 会报错，不用的话请从 `compose.yaml` 中删掉对应的行。

### 3. 启动

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## 常用命令

```bash
# 刷新一次额度并发送通知，不点火
docker compose run --rm quota-keeper python /app/scheduler.py --once

# 查看每个账号的下一次点火时间
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# 运行测试
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## 配置项

所有配置写在 `.env` 中。

### 通知

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BARK_URL` | 空 | Bark 推送地址，为空时不推送 |
| `BARK_GROUP` | `CPA` | Bark 通知分组 |
| `BARK_ICON` | CPA logo | 通知图标 |
| `POLL_INTERVAL` | `300` | 额度刷新间隔（秒），最小 60 |
| `NOTICE_THRESHOLD` | `50` | 第一档提醒阈值（%） |
| `LOW_THRESHOLD` | `20` | 第二档提醒阈值（%） |
| `CRITICAL_THRESHOLD` | `10` | 第三档提醒阈值（%） |
| `NOTIFY_RECOVERY` | `true` | 额度恢复时是否通知 |
| `TZ_OFFSET_HOURS` | `8` | 显示时间和点火时段使用的时区（UTC 偏移） |
| `REQUEST_TIMEOUT` | `20` | HTTP 请求超时（秒） |

`POLL_INTERVAL` 只影响额度刷新和通知。点火时间由 `reset_at` 决定，不依赖轮询频率。

### 点火

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IGNITE_ENABLED` | `true` | 是否开启自动点火 |
| `IGNITE_START_HOUR` | `7` | 每天第一次点火的时间 |
| `IGNITE_END_HOUR` | `22` | 每天停止点火的时间 |
| `IGNITE_END_GRACE_MINUTES` | `30` | `IGNITE_END_HOUR` 之后允许的延后时间（分钟） |
| `IGNITE_GRACE_SECONDS` | `3` | `reset_at` 之后等待多少秒再点火 |
| `IGNITE_FAILURE_RETRY_SECONDS` | `300` | 失败后重试间隔（秒），最小 60 |
| `IGNITE_POST_SUCCESS_HOLD_SECONDS` | `60` | 成功后多久内不再点火（秒），最小 15 |
| `IGNITE_CODEX_MODEL` | 空 | Codex 点火模型，为空时自动选 Luna |
| `IGNITE_CLAUDE_MODEL` | 空 | Claude 点火模型，为空时自动选 Haiku |

## 多账号命名

同一 provider 只有一个账号时，通知里只显示 provider 名，例如 `Claude`、`ChatGPT`、`Gemini`。

有多个账号时，Keeper 取账号邮箱用户名的前 2 个和后 2 个字符作为后缀：

```text
alice.work@example.com → ChatGPT#al~rk
bob.team@example.net   → ChatGPT#bo~am
```

这个名字只用于通知和日志，调度和请求仍按 `auth_index` 区分账号。

## 文件

| 文件 | 用途 |
| --- | --- |
| `watcher.py` | 读取额度、生成和发送通知 |
| `scheduler.py` | 主程序：多账号调度和自动点火 |
| `test_scheduler.py` | 测试 |
| `compose.yaml` | Docker Compose 配置 |
| `.env.example` | 配置示例 |
| `data/state.json` | 运行状态（自动生成） |

## 安全

- Keeper 不直接读取磁盘上的凭证文件。额度查询和点火都通过 CPA Management API 完成，由 CPA 在服务端使用 `auth_index` 对应的凭证。Antigravity 凭证缺少 `project_id` 时，Keeper 会通过 Management API 下载该凭证文件来读取它。
- 容器以只读文件系统运行，并去掉了所有 Linux capabilities。
- 不要提交 `.env`、Bark device key、Management 密钥、CPA 凭证文件和 `data/state.json`。`.gitignore` 已排除 `.env` 和 `data/`。
