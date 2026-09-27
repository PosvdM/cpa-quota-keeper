# CPA Quota Watcher

[English](./README_EN.md)

监控 CLIProxyAPI 中的 Claude / Antigravity 额度，并通过 Bark 发通知。

## 功能

- 主动刷新额度，不依赖管理页面里的缓存。
- 支持 Claude 的 5 小时、7 天和 Fable 额度。
- 支持 Antigravity 的 Gemini、Claude / GPT 额度组。
- 剩余额度跨过 `50%`、`20%`、`10%`、`0%` 时通知。
- 同一阈值区间不会重复通知。
- 额度恢复后通知一次。
- 所有额度窗口在重置前 1 小时内通知一次。
- 7 天窗口还会在重置前 1 天内通知一次。
- 1 天提醒和 1 小时提醒分别去重，重启 watcher 也不会重复发送同一轮提醒。
- 重置提醒正文会显示同一额度组的全部窗口。
- 恢复通知只在标题写“已恢复”，正文继续显示当前百分比。
- 状态保存在 `data/state.json`。

## 通知示例

额度下降：

```text
⚠️ CPA · Claude · 7d 48%

5h：83% | 02时 | 09/28 05:59
7d：48% | 03天 | 10/01 13:59
```

重置提醒：

```text
⏰ CPA · Gemini · 5h 重置提醒

5h：94% | 07分 | 09/28 04:16
7d：95% | 03天 | 10/01 13:59
```

额度恢复：

```text
✅ CPA · Claude · 7d 已恢复

5h：83% | 02时 | 09/28 05:59
7d：100% | 03天 | 10/01 13:59
```

## 时间格式

正文同时显示粗略剩余时间和准确重置时间：

```text
7d：48% | 03天 | 10/01 13:59
```

粗略时间按当前所在单位四舍五入：

- 不到 1 小时：`59分`、`60分`
- 1 小时到不足 1 天：`02时`、`24时`
- 1 天以上：`03天`、`04天`

## 部署

### 1. 准备配置

```bash
cp .env.example .env
```

填写 Bark 地址：

```env
BARK_URL=https://api.day.app/your_device_key
BARK_GROUP=CPA
```

### 2. 提供 CLIProxyAPI 管理密钥

可以直接写进 `.env`：

```env
CPA_MANAGEMENT_KEY=your_management_key
```

当前 `compose.yaml` 也会读取：

```text
/opt/cliproxyapi/watcher.env
```

可在这个文件里写：

```env
MANAGEMENT_PASSWORD=your_management_password
```

两种方式选一种即可。

### 3. 检查 Docker 网络

当前配置假设：

- CLIProxyAPI 容器名是 `cliproxyapi`
- Management API 地址是 `http://cliproxyapi:8317/v0/management`
- Docker 网络名是 `cliproxyapi_default`

如果你的环境不同，修改 `compose.yaml` 和 `.env`。

### 4. 启动

```bash
docker compose up -d
```

查看日志：

```bash
docker logs -f cpa-quota-watcher
```

只轮询一次：

```bash
docker compose run --rm quota-watcher python /app/watcher.py --once
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
```

默认每 5 分钟刷新一次。

重置提醒规则：

```text
所有窗口：0 < 距离 reset <= 1 小时
7 天窗口：1 小时 < 距离 reset <= 1 天
```

通知会在进入对应时间范围后的下一次轮询发送。

## 文件

- `watcher.py`：额度查询、通知和状态逻辑
- `compose.yaml`：Docker Compose 配置
- `.env.example`：环境变量示例
- `.gitignore`：忽略本地密钥和运行数据
- `README_EN.md`：英文 README

## 安全

不要提交：

- `.env`
- Bark device key
- CLIProxyAPI management key / password
- CLIProxyAPI auth 文件
- `data/state.json`

`.gitignore` 已忽略 `.env` 和 `data/`。

## 工作方式

watcher 通过 CLIProxyAPI Management API 调用上游额度接口。它不需要直接读取 Claude 或 Antigravity 的账号凭证文件。
