# CPA Quota Keeper

[English](./README_EN.md)

给 CLIProxyAPI 加一层额度监控和自动点火。

它会读取 Codex、Claude 和 Antigravity 的额度，通过 Bark 发提醒，并在 Codex / Claude 的 5 小时额度重置后自动发一个最小请求，启动下一轮额度窗口。

## 支持什么

- Codex：5 小时、7 天额度
- Claude：5 小时、7 天、Fable 额度
- Antigravity：Gemini、Claude / GPT 额度组
- 多个 Codex / Claude 账号
- Bark 额度提醒、重置提醒和恢复提醒
- Codex / Claude 5 小时窗口自动点火

默认提醒阈值：

```text
50% → 提醒
20% → 提醒
10% → 提醒
 0% → 提醒
```

所有额度窗口会在重置前 1 小时提醒一次。7 天窗口还会在重置前 1 天提醒一次。

额度恢复后会再通知一次。

## Window Ignition

每天 **07:00** 作为起点。

07:00 之后，Keeper 不再按固定的 12:00、17:00、22:00 发请求，而是读取每个账号真实的 `reset_at`。

```text
07:00 点火
  ↓
读取 reset_at
  ↓
reset_at + 3 秒再次点火
  ↓
读取新的 reset_at
  ↓
继续
```

这样每个账号都按自己的实际重置时间运行。

如果某个账号已经提前用过，Keeper 会继续沿用它当前的额度窗口，不会强行重新对齐。

默认允许最后一轮在 22:00 后漂移 30 分钟。再晚的重置不会继续点火，会等到第二天 07:00。

### 点火请求

Codex 优先使用可用的 Luna，Claude 优先使用 Haiku。

请求只要求模型回复：

```text
OK
```

工具调用会关闭，推理也会尽量关闭或降到最低。

点火会直接绑定当前 credential 的 `auth_index`。某个账号失败时，只会重试这个账号，不会切到另一个账号。

默认失败后 5 分钟重试。

Antigravity 目前只监控额度，不参与点火。

## 多账号显示

同一 provider 只有一个账号时，只显示 provider 名：

```text
Gemini
Claude
ChatGPT
```

有多个同类账号时，Keeper 会自动读取 credential 的账号邮箱，并用邮箱用户名的前 2 个字符和后 2 个字符生成脱敏后缀。

例如：

```text
trr244426@…  → ChatGPT#u5~xx
posvdm6+eg@… → ChatGPT#u4~xx
```

这个名字只用于通知和日志。实际路由仍按 `auth_index` 区分。

新增账号后不需要改代码。

## 通知示例

额度下降：

```text
⚠️ Claude · 7d 48%

5h：96% | 04时 | 09/28 13:50
7d：48% | 03天 | 10/01 14:00
```

重置提醒：

```text
⏰ ChatGPT#u4~xx · 5h 重置提醒

5h：19% | 01时 | 09/28 13:55
7d：81% | 06天 | 10/04 21:27
```

额度恢复：

```text
✅ Claude · 7d 已恢复

5h：96% | 04时 | 09/28 13:50
7d：100% | 03天 | 10/01 14:00
```

## 部署

复制配置：

```bash
cp .env.example .env
```

填写 Bark：

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

再提供 CLIProxyAPI 的 Management API 密钥。

可以直接写进 `.env`：

```env
CPA_MANAGEMENT_KEY=your_management_key
```

也可以使用：

```text
/opt/cliproxyapi/watcher.env
```

并填写：

```env
MANAGEMENT_PASSWORD=your_management_password
```

两种方式选一种。

默认 Docker 配置假设：

```text
CLIProxyAPI 容器：cliproxyapi
Management API：http://cliproxyapi:8317/v0/management
Docker 网络：cliproxyapi_default
```

如果你的环境不同，修改 `compose.yaml` 和 `.env`。

启动：

```bash
docker compose up -d
```

查看日志：

```bash
docker logs -f cpa-quota-keeper
```

## 常用命令

只刷新一次额度，不点火：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

查看每个账号下一次点火时间：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

运行测试：

```bash
docker compose run --rm quota-keeper python /app/test_scheduler.py
```

## 默认配置

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

IGNITE_CODEX_MODEL=
IGNITE_CLAUDE_MODEL=
```

`POLL_INTERVAL` 只控制额度刷新和 Bark 通知。

Window Ignition 使用 `reset_at` 安排下一次点火，不需要高频轮询额度。

## 文件

- `watcher.py`：读取额度、生成通知、保存通知状态
- `scheduler.py`：多账号调度和 Window Ignition
- `test_scheduler.py`：测试
- `compose.yaml`：Docker Compose 配置
- `.env.example`：配置示例
- `data/state.json`：运行状态

## 安全

不要提交这些文件或内容：

- `.env`
- Bark device key
- CLIProxyAPI management key / password
- CLIProxyAPI auth 文件
- `data/state.json`

Keeper 不直接读取账号凭证文件。

额度查询和点火都通过 CLIProxyAPI Management API 完成。CPA 会根据指定的 `auth_index` 在服务端使用对应 credential。
