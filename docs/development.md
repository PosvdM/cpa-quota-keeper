# 开发

[English](./development_EN.md)

## 环境

主服务只使用 Python 标准库，生产 Compose 使用 `python:3.12-alpine`。CPA 点火桥是 Go 动态插件，`go.mod` 当前依赖 `github.com/router-for-me/CLIProxyAPI/v8 v8.0.4`。

常用工具：

- Python 3.12
- Docker 和 Docker Compose
- 能运行动态插件的 CPA
- 构建插件时使用 Docker 拉取 `golang:1.26-bookworm`

插件需要为 CPA 所在的平台和 CPU 架构构建。仓库的 `build.sh` 会在当前 Docker 主机架构上生成 `.so`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `scheduler.py` | 运行入口和点火调度 |
| `watcher.py` | CPA 客户端、额度解析、通知和状态持久化 |
| `test_scheduler.py` | 调度、模型选择、点火桥、通知和失败保护单元测试 |
| `keeper.example.toml` | 点火和 Provider 配置示例 |
| `.env.example` | 运行环境变量示例 |
| `compose.yaml` | Keeper 容器示例 |
| `cpa_plugin/quota-keeper-bridge/` | CPA 点火桥源码和构建脚本 |
| `docs/architecture.md` | 数据流、状态和设计约束 |

## 单元测试

测试使用假客户端，不应发送真实模型请求：

```bash
python3 -m unittest -v
```

提交前至少运行：

```bash
git diff --check
python3 -m unittest -v
```

测试覆盖的重点包括：

- 滑动/固定 5 小时 `reset` 判断；
- 日间调度边界；
- 主额度和 Did Codex Reset 轮询的绝对时间边界；
- Provider 模型选择顺序；
- 点火请求是否携带精确 `auth_index`；
- 点火桥协议；
- 失败退避和熔断；
- Bark 阈值、恢复基线和重置提醒；
- Did Codex Reset 去重。

## 构建 CPA 点火桥

运行：

```bash
sh cpa_plugin/quota-keeper-bridge/build.sh
```

脚本在 `cpa_plugin/quota-keeper-bridge/` 生成：

```text
quota-keeper-bridge.so
```

生成的 `.so` 和 cgo `.h` 已被 `.gitignore` 忽略，不提交到仓库。

修改以下任一内容后需要重新构建并安装插件：

- `cpa_plugin/quota-keeper-bridge/main.go`
- `go.mod` / `go.sum`
- CPA plugin ABI 或 `host.model.execute` / `host.auth.get_runtime` 契约

插件安装到 CPA 配置的 `plugins.dir` 后，需要重启 CPA 才会重新加载动态库。

## 本地和容器命令

只刷新一次额度，不点火：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --once
```

查看下一次点火时间：

```bash
docker compose run --rm quota-keeper python /app/scheduler.py --show-schedule
```

查看运行日志：

```bash
docker compose logs -f quota-keeper
```

`--once` 和 `--show-schedule` 仍需要可访问的 CPA Management API 和有效管理密钥，因为它们会读取真实额度。

## 修改周期轮询

主额度轮询和 Did Codex Reset 轮询都通过 `scheduler.next_aligned_epoch()` 计算下一次时间。周期任务按绝对时间边界调度，不使用 `time.time() + interval` 作为下一次目标；相对调度会让轮询时间随进程启动时间漂移。默认 300 秒间隔应落在每小时 `:00 / :05 / :10 / ... / :55`。

修改这部分逻辑时，同时更新边界测试和中英文 README、架构文档。

## 修改 Provider 适配

额度适配最终都要返回相同的 group/window 结构。修改某个 Provider 时：

1. 保持窗口至少包含稳定的 `id`、`label`、`remaining` 和可选的 `reset`；
2. 需要自动点火的 5 小时窗口必须能被 `is_five_hour_window()` 识别；
3. 为新解析规则和边界情况增加单元测试；
4. 用户可见的额度范围、配置项或默认行为变化时，同步 README；
5. 数据流、接口或状态结构变化时，同步 `docs/architecture.md`。

不要把模型调用的 Provider 原生 HTTP 细节加入 Keeper。点火请求应经过 `/quota-keeper/ignite` 和 CPA Provider Executor。

## 修改模型选择

自动模型选择集中在 `choose_model()`。规则变更时同时更新对应测试。显式配置覆盖始终优先，并且覆盖模型必须出现在 CPA 为该凭证返回的模型列表中。

Antigravity 可能有多个独立额度组。分组模型覆盖使用：

```toml
[providers.antigravity.models]
"Gemini" = "..."
"Claude / GPT" = "..."
```

不要把某个时间点观察到的模型 ID 写成长期固定事实；模型列表由 CPA 提供，文档只描述选择规则。

## 修改状态

`data/state.json` 会跨容器重启保留。新增字段时使用缺省值，旧状态缺字段时应继续运行。需要改 group key 时提供迁移逻辑，避免要求用户手动删除状态文件。

通知状态和调度状态共享同一个 JSON 文件，因此写入继续使用 `watcher.save_state()` 的临时文件替换方式，不直接原地覆盖。

## 真实点火测试

调用 `/v0/management/quota-keeper/ignite` 会发送真实模型请求并消耗额度，也可能启动新的 5 小时窗口。默认开发流程使用单元测试；只有需要验证 CPA/Provider 集成时才做真实点火，并确认账号、模型和当前窗口状态。

验证真实点火时，HTTP 2xx 只说明模型执行返回。还要重新读取额度，确认滑动 reset 变成未来的固定 reset，才能判定窗口已启动。

## 文档检查

代码改动结束前按 `AGENTS.md` 检查文档：

- 使用者可见行为、安装、配置、命令和限制写在 README；
- 架构、接口、状态和开发约束写在 `docs/`；
- README 有中英文版本时同步修改；
- 文档只描述当前状态，不记录改动经过。
