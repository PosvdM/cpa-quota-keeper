# CPA Quota Watcher

[English](./README_EN.md)

一个用于 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 的轻量级 Docker 额度监控器。它会主动刷新 Claude / Antigravity 的额度，并通过 Bark 推送阈值告警、恢复通知和重置前提醒。

## 功能

- 主动调用 CLIProxyAPI Management API 刷新额度，不依赖网页端缓存。
- 支持 Claude OAuth 额度：
  - 5 小时额度
  - 7 天额度
  - Fable 额度（上游返回时）
- 支持 Antigravity 额度组：
  - Gemini
  - Claude / GPT
- 剩余额度跨过以下阈值时推送：
  - 50%
  - 20%
  - 10%
  - 0%
- 同一严重等级内不会重复提醒。
- 额度恢复或重置后会发送一次恢复通知。
- **无论当前额度还剩多少，所有窗口在距离重置时间不超过 1 小时时都会提醒一次。**
- **7d 类窗口还会额外在距离重置时间不超过 1 天、但仍超过 1 小时时提醒一次。**
- 1 天提醒和 1 小时提醒分别去重；同一个窗口、同一个 reset 周期每个阶段最多一次，watcher 重启后也不会重复发送。
- reset 时间变化进入下一周期后，可以再次触发重置前提醒。
- 状态持久化保存到 `data/state.json`。

## 通知示例

阈值告警：

```text
⚠️ CPA · Claude · 7d 48%

5h：83% | 02时 | 09/28 05:59
7d：48% | 03天 | 10/01 13:59
```

重置前提醒：

```text
⏰ CPA · Gemini · 5h 重置提醒

5h：94% | 07分 | 09/28 04:16
```

恢复通知：

```text
✅ CPA · Claude · 7d 已恢复

5h：83% | 02时 | 09/28 05:59
7d：已恢复 | 03天 | 10/01 13:59
```

## 时间显示规则

正文中的粗略剩余时间使用固定的紧凑格式，并按**实际剩余时间所在单位**进行四舍五入：

- 小于 1 小时：分钟，例如 `59分`、`60分`
- 1 小时到不足 1 天：小时，例如 `02时`、`24时`
- 至少 1 天：天，例如 `03天`、`04天`

绝对重置时间仍会同时显示，例如：

```text
7d：48% | 03天 | 10/01 13:59
```

## 文件

- `watcher.py`：额度获取、阈值状态机、重置提醒、通知格式和 Bark 推送
- `compose.yaml`：当前部署使用的 Docker Compose 配置
- `.env.example`：安全的环境变量模板
- `.gitignore`：排除密钥和运行时状态

## 部署

### 1. 准备环境变量

复制：

```bash
cp .env.example .env
```

至少配置 Bark：

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. 提供 CLIProxyAPI Management Key

当前 `compose.yaml` 还会加载：

```text
/opt/cliproxyapi/watcher.env
```

其中可以放：

```env
MANAGEMENT_PASSWORD=your_management_password
```

也可以直接在 `.env` 中配置：

```env
CPA_MANAGEMENT_KEY=
```

如果使用 `CPA_MANAGEMENT_KEY`，可以按自己的部署方式移除额外的 `watcher.env`。

### 3. Docker 网络

默认假设 CLIProxyAPI 容器名为：

```text
cliproxyapi
```

Management API 地址为：

```text
http://cliproxyapi:8317/v0/management
```

并且已经存在外部 Docker 网络：

```text
cliproxyapi_default
```

### 4. 启动

```bash
docker compose up -d
```

查看日志：

```bash
docker logs -f cpa-quota-watcher
```

手动只轮询一次：

```bash
docker compose run --rm quota-watcher python /app/watcher.py --once
```

## 默认配置

`.env.example` 中默认：

```env
POLL_INTERVAL=300
NOTICE_THRESHOLD=50
LOW_THRESHOLD=20
CRITICAL_THRESHOLD=10
NOTIFY_RECOVERY=true
TZ_OFFSET_HOURS=8
REQUEST_TIMEOUT=20
```

轮询间隔为 5 分钟。重置前提醒条件为：

```text
所有窗口：0 < 距离 reset 的时间 <= 1 小时
7d 类窗口额外：1 小时 < 距离 reset 的时间 <= 1 天
```

因此实际通知会出现在“进入对应提醒窗口后的下一次轮询”，最多受到轮询间隔影响。

## 安全

不要提交：

- 真实 `.env`
- Bark device key
- CLIProxyAPI management password / key
- CLIProxyAPI auth 文件
- `data/state.json`

仓库提供的 `.gitignore` 已排除本地 `.env` 和 `data/`。

## 说明

watcher 通过 CLIProxyAPI 的 Management API 代为调用上游额度接口，因此本身不需要直接读取 Claude 或 Antigravity 的账号凭证文件。
