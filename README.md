# CPA Quota Keeper

[English](./README_EN.md)

CPA Quota Keeper 是 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（CPA）的额度监控与自动点火工具：

1. 定时查询 Codex、Claude、Antigravity 账号的剩余额度，并在额度下降、即将重置或恢复时通过 [Bark](https://github.com/Finb/Bark) 发送通知。
2. 在 Codex 和 Claude 的 5 小时额度重置后，自动向该账号发送最小请求，使下一个 5 小时窗口立即开始计时。

## 支持的额度

| Provider | 额度窗口 | 自动点火 |
| --- | --- | --- |
| Codex | 5 小时、7 天 | 支持 |
| Claude | 5 小时、7 天、Fable | 支持 |
| Antigravity | Gemini、Claude / GPT 额度组 | 不支持（仅监控） |

支持单 Provider 配置多账号，新增账号无需修改配置。

## 通知

推送触发条件：

- 剩余额度降至 50%、20%、10%、0%（阈值可配置）
- 任意额度窗口重置前 1 小时
- 7 天额度窗口重置前 1 天
- 额度重置后恢复

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

每行依次显示：窗口名称、剩余额度、距重置时长、重置时间。

## 自动点火（Window Ignition）

5 小时额度窗口自首次请求起算。若重置后未及时使用，窗口不会自动计时。Keeper 在每次重置后立即发送轻量请求，保持窗口连续滚动。

调度流程：

```text
07:00 首次点火
  ↓
读取账号的 reset_at
  ↓
在 reset_at + 3 秒再次点火
  ↓
读取新的 reset_at，循环
```

- 各账号依据自身的 `reset_at` 独立调度。账号处于使用中时，Keeper 沿用当前窗口，不对齐到 07:00。
- 每天最晚点火时间为 22:30（`IGNITE_END_HOUR` + `IGNITE_END_GRACE_MINUTES`）。晚于此时段的重置不触发点火，顺延至次日 07:00。
- 点火请求仅要求模型回复 `OK`，不携带工具。Codex 请求关闭推理，Claude 请求限制输出最多 4 个 token。
- Codex 默认选用 Luna，Claude 默认选用 Haiku；可通过 `IGNITE_CODEX_MODEL` 与 `IGNITE_CLAUDE_MODEL` 自定义。
- 请求通过 `auth_index` 绑定具体账号。点火失败时在 5 分钟后重试同一账号，不轮换至其他账号。

## 部署

### 前提条件

- CPA 已在 Docker 中运行，并开启了 Management API
- Bark device key

默认假定配置：

| 项目 | 默认值 |
| --- | --- |
| Management API | `http://cliproxyapi:8317/v0/management` |
| Docker 网络 | `cliproxyapi_default` |
| CPA 配置文件 | `/opt/cliproxyapi/config.yaml` |

若与实际环境不符，需修改 `compose.yaml`。

### 1. 创建配置

```bash
cp .env.example .env
```

配置 Bark 地址：

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. 提供 Management 密钥

Keeper 按以下顺序查找密钥，命中首项即止：

1. `.env` 中的 `CPA_MANAGEMENT_KEY`
2. `/opt/cliproxyapi/watcher.env` 中的 `MANAGEMENT_PASSWORD`
3. CPA `config.yaml` 中 `remote-management.secret-key` 的明文值

若 `config.yaml` 中的 `secret-key` 已被 CPA 转换为 bcrypt 哈希，须通过前两种方式提供明文。

`compose.yaml` 默认加载 `/opt/cliproxyapi/watcher.env` 并挂载 `/opt/cliproxyapi/config.yaml`。若相应文件不存在，`docker compose` 会报错；未使用的文件需在 `compose.yaml` 中移除对应行。

### 3. 启动容器

```bash
docker compose up -d
docker logs -f cpa-quota-keeper
```

## 常用命令

```bash
# 刷新一次额度并发送通知（不点火）
docker compose run --rm quota-keeper python /app/scheduler.py --once

# 查看各账号的下一次点火时间
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule

# 运行测试
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## 配置项

所有配置项均写在 `.env` 中。

### 通知配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BARK_URL` | 空 | Bark 推送地址，为空时不发送通知 |
| `BARK_GROUP` | `CPA` | Bark 通知分组 |
| `BARK_ICON` | CPA logo | 通知图标 |
| `POLL_INTERVAL` | `300` | 额度轮询间隔（秒），最小 60 |
| `NOTICE_THRESHOLD` | `50` | 第一档提醒阈值（%） |
| `LOW_THRESHOLD` | `20` | 第二档提醒阈值（%） |
| `CRITICAL_THRESHOLD` | `10` | 第三档提醒阈值（%） |
| `NOTIFY_RECOVERY` | `true` | 额度恢复时是否通知 |
| `TZ_OFFSET_HOURS` | `8` | 显示时间与点火时段所用时区（UTC 偏移量） |
| `REQUEST_TIMEOUT` | `20` | HTTP 请求超时时间（秒） |

`POLL_INTERVAL` 仅用于额度刷新和通知。点火时刻由 `reset_at` 决定，不依赖轮询间隔。

### 点火配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IGNITE_ENABLED` | `true` | 是否启用自动点火 |
| `IGNITE_START_HOUR` | `7` | 每天首次点火时间（小时） |
| `IGNITE_END_HOUR` | `22` | 每天停止点火时间（小时） |
| `IGNITE_END_GRACE_MINUTES` | `30` | `IGNITE_END_HOUR` 后的宽限时间（分钟） |
| `IGNITE_GRACE_SECONDS` | `3` | `reset_at` 后延迟点火时间（秒） |
| `IGNITE_FAILURE_RETRY_SECONDS` | `300` | 点火失败后的重试间隔（秒），最小 60 |
| `IGNITE_POST_SUCCESS_HOLD_SECONDS` | `60` | 点火成功后的冷却时间（秒），最小 15 |
| `IGNITE_CODEX_MODEL` | 空 | Codex 点火模型，为空时自动选用 Luna |
| `IGNITE_CLAUDE_MODEL` | 空 | Claude 点火模型，为空时自动选用 Haiku |

## 多账号命名

Provider 仅有一个账号时，通知中仅显示 Provider 名称（如 `Claude`、`ChatGPT`、`Gemini`）。

同一 Provider 存在多个账号时，Keeper 取账号邮箱用户名的前 2 个和后 2 个字符生成后缀：

```text
alice.work@example.com → ChatGPT#al~rk
bob.team@example.net   → ChatGPT#bo~am
```

此名称仅用于通知显示与日志输出；账号调度与请求路由仍依据 `auth_index` 执行。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `watcher.py` | 读取额度，生成并发送通知 |
| `scheduler.py` | 主程序：多账号调度与自动点火 |
| `test_scheduler.py` | 单元测试 |
| `compose.yaml` | Docker Compose 服务配置 |
| `.env.example` | 环境变量模板 |
| `data/state.json` | 运行时状态持久化文件（自动生成） |

## 安全说明

- Keeper 不直接读取宿主机上的凭证文件。额度查询与点火均通过 CPA Management API 交互，由 CPA 服务端使用 `auth_index` 对应的凭证执行。当 Antigravity 凭证缺少 `project_id` 时，Keeper 会通过 Management API 下载凭证文件解析。
- 容器使用只读根文件系统运行，并丢弃了所有 Linux capabilities。
- 切勿提交 `.env`、Bark device key、Management 密钥、CPA 凭证文件或 `data/state.json`；`.gitignore` 已忽略 `.env` 和 `data/`。
