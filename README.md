# CPA Quota Keeper

[English](./README_EN.md)

监控 CLIProxyAPI 中的 Codex、Claude 和 Antigravity 额度，通过 Bark 发通知，并可自动维持 Codex / Claude 的 5 小时额度窗口。

## 功能

- 主动读取上游额度，不依赖管理页面缓存。
- 支持多个 Codex 和 Claude 凭证，每个账号独立记录额度与重置时间。
- 支持 Codex 的 5 小时和 7 天额度。
- 支持 Claude 的 5 小时、7 天和 Fable 额度。
- 支持 Antigravity 的 Gemini、Claude / GPT 额度组。
- 剩余额度跨过 `50%`、`20%`、`10%`、`0%` 时通过 Bark 通知。
- 额度恢复后通知一次。
- 所有额度窗口在重置前 1 小时提醒一次，7 天窗口还会在重置前 1 天提醒一次。
- 自动为 Codex / Claude 启动新的 5 小时窗口。
- 自动选择低消耗模型：Codex 优先 Luna，Claude 优先 Haiku。
- 点火请求明确绑定单个 `auth_index`，失败不会切到另一个账号。
- 状态保存在 `data/state.json`，重启后继续使用。

## Window Ignition（窗口点火）

默认每天 **07:00** 是锚点。

07:00 之后不再硬编码 12:00、17:00、22:00，而是读取每个账号真实的 `reset_at`：

```text
07:00 首轮
  ↓
读取真实 reset_at
  ↓
reset_at + 3 秒点火
  ↓
重新读取新的 reset_at
  ↓
继续
```

所以不同账号可以有不同的时间，例如：

```text
Claude   → 12:00:04 → 17:00:07 → 22:00:10
Codex #1 → 12:03:21 → 17:03:24 → 22:03:27
Codex #2 → 12:18:05 → 17:18:08 → 22:18:11
```

如果某个账号当天已经被正常使用过，keeper 会沿用它当前真实的窗口，而不是强行重新对齐。

默认在 22:00 后保留 30 分钟漂移范围。落在夜间的下一次 reset 不会继续点火，而是等到第二天 07:00。

### 为什么不会串账号

点火不是通过普通 `/v1` 请求进入 CPA 负载均衡，而是通过 Management API 的 `api-call`，并明确传入该凭证的 `auth_index`。

因此：

```text
Codex #1 点火失败
        ↓
只记录 Codex #1 失败并稍后重试

不会：
Codex #1 → Codex #2
```

默认失败后 5 分钟只重试同一个 credential。

## 点火请求

Codex 会优先选择可用的 Luna 模型，Claude 会优先选择 Haiku。可以用环境变量手动指定：

```env
IGNITE_CODEX_MODEL=
IGNITE_CLAUDE_MODEL=
```

请求会关闭工具和不必要的推理，并要求只返回：

```text
OK
```

Claude 还会限制极短输出。Codex 的 ChatGPT 后端不接受 `max_output_tokens`，因此使用精确指令控制输出。

Antigravity 目前只监控额度，不参与窗口点火。

## 通知示例

```text
⚠️ CPA · Claude · 7d 48%

5h：83% | 02时 | 09/28 05:59
7d：48% | 03天 | 10/01 13:59
```

```text
⏰ CPA · Codex #2 · 5h 重置提醒

5h：19% | 01时 | 09/28 13:55
7d：81% | 06天 | 10/04 21:27
```

```text
✅ CPA · Claude · 7d 已恢复

5h：96% | 04时 | 09/28 13:50
7d：100% | 03天 | 10/01 13:59
```

## 部署

### 1. 准备配置

```bash
cp .env.example .env
```

填写 Bark：

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. 提供 CLIProxyAPI 管理密钥

可以写进 `.env`：

```env
CPA_MANAGEMENT_KEY=your_management_key
```

当前 `compose.yaml` 也会读取：

```text
/opt/cliproxyapi/watcher.env
```

其中可以写：

```env
MANAGEMENT_PASSWORD=your_management_password
```

两种方式选一种即可。

### 3. 检查 Docker 网络

默认假设：

- CLIProxyAPI 容器名为 `cliproxyapi`
- Management API 为 `http://cliproxyapi:8317/v0/management`
- Docker 网络为 `cliproxyapi_default`

环境不同的话修改 `compose.yaml` 和 `.env`。

### 4. 启动

```bash
docker compose up -d
```

查看日志：

```bash
docker logs -f cpa-quota-keeper
```

只刷新一次额度，不发送点火请求：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

查看当前每个账号的下一次点火时间：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

运行测试：

```bash
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## 默认参数

```env
POLL_INTERVAL=300
NOTICE_THRESHOLD=50
LOW_THRESHOLD=20
CRITICAL_THRESHOLD=10
NOTIFY_RECOVERY=true
TZ_OFFSET_HOURS=8
REQUEST_TIMEOUT=20

IGNITE_ENABLED=true
IGNITE_START_HOUR=7
IGNITE_END_HOUR=22
IGNITE_END_GRACE_MINUTES=30
IGNITE_GRACE_SECONDS=3
IGNITE_FAILURE_RETRY_SECONDS=300
IGNITE_POST_SUCCESS_HOLD_SECONDS=60
```

`POLL_INTERVAL` 仍然只负责额度刷新和 Bark 通知。点火调度直接使用真实 `reset_at` 的本地定时，不需要每 10 秒查询一次额度。

## 文件

- `watcher.py`：额度接口、Bark 通知和原有状态逻辑
- `scheduler.py`：Codex / Claude 多账号额度读取和 5 小时窗口调度
- `test_scheduler.py`：调度边界和定向 credential 测试
- `compose.yaml`：Docker Compose 配置
- `.env.example`：环境变量示例
- `README_EN.md`：英文 README

## 安全

不要提交：

- `.env`
- Bark device key
- CLIProxyAPI management key / password
- CLIProxyAPI auth 文件
- `data/state.json`

keeper 不直接读取账号凭证文件。额度查询和点火都通过 CLIProxyAPI Management API 完成，`$TOKEN$` 由 CPA 根据指定 `auth_index` 在服务端替换。

## 多账号

不需要把账号数量写死。新增 Codex 或 Claude OAuth credential 后，keeper 会自动发现并分别调度。

例如以后有三个 Codex：

```text
Codex #1
Codex #2
Codex #3
```

它们各自维护自己的额度、`reset_at` 和下一次点火时间。
