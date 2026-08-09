<p align="center">
  <img src="./assets/readme/hero.png" width="100%" alt="河大校园助手 Langbot 插件：QQ 接入，按发送者隔离账号">
</p>

Langbot 插件形态。对外主要是统一 CLI 工具 `henu_cli`，并按 **QQ 发送者** 隔离账号数据。

版本见 `manifest.yaml`（当前 **2.1.0**）。其它形态：[`main`](https://github.com/jry21223/HENU_Assistant) · [`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server) · [`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill)

> **命令方言**：本插件使用空格风格 CLI（`schedule now`、`library seats`、`--student-id`）。  
> Agent Skill / MCP 使用下划线子命令（`schedule_query`、`library_query`、`--student_id`）。**不要互相粘贴。**

---

## 安装

### A. 终端用户（LangBot 装插件包）

前置：已部署可用的 [LangBot](https://docs.langbot.app)，并接好消息适配器（如 NapCat / OneBot）。宿主插件运行时必须支持 `Tool.call(params, session, query_id)` 并向 Tool 注入可信调用上下文；本项目冻结测试 `langbot-plugin==0.5.0`。旧版只传 `params` 的 Handler 不受支持。本仓库**不是**独立 QQ 机器人。

1. 打开 [Releases](https://github.com/jry21223/HENU_Assistant/releases)，下载与版本号一致的 `*.lbpkg`（由 `langbot-plugin` 分支 CI 构建）。  
2. 在 LangBot 中安装该插件包（插件管理 / 本地导入，以你使用的 LangBot 版本文档为准）。  
3. 为运行环境配置稳定的 **`HENU_MASTER_KEY`**（长随机串；容器重建、迁移必须沿用同一密钥，否则已有密码密文无法解密）。  
4. 私聊机器人绑定账号（见下方「敏感命令」），再查询课表 / 图书馆等。

官方插件开发与调试说明可参考：<https://docs.langbot.app/en/plugin/dev/tutor.html>

### B. 开发者（本仓库调试 / 打包）

```bash
git clone -b langbot-plugin https://github.com/jry21223/HENU_Assistant.git
cd HENU_Assistant

# 推荐一键安装（匹配 Python minor 的 hash lock + .env）
chmod +x install.sh && ./install.sh

# 或手动：
python3 -m venv .venv
LOCK_FILE="$(python3 scripts/select_lockfile.py --check)"
HENU_PYPI_INDEX_URL="${HENU_PYPI_INDEX_URL:-https://pypi.org/simple}"
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL="$HENU_PYPI_INDEX_URL" PIP_EXTRA_INDEX_URL= \
  .venv/bin/python -m pip install --require-hashes -r "$LOCK_FILE"
python3.13 -m venv .lbp-build-venv
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL="$HENU_PYPI_INDEX_URL" PIP_EXTRA_INDEX_URL= \
  .lbp-build-venv/bin/python -m pip install --require-hashes \
  -r requirements-lock/lbp-py313.txt
cp -n env.example .env             # 已有 .env 时请勿覆盖
# 编辑 .env：生产必须设置 HENU_MASTER_KEY=

.venv/bin/lbp run                  # 连接本机 LangBot 调试 runtime
.venv/bin/python -m pytest
.venv/bin/python scripts/build_plugin.py  # 构建并验证 dist/*.lbpkg
```

业务运行核、真实插件 runtime 与测试在 POSIX（macOS/Linux）支持 Python
3.10–3.14，五套锁均固定 `langbot-plugin==0.5.0`。该版本会向 Tool 注入可信的
`session/query_id`，用于按 QQ 隔离请求。`lbp==0.1.2` 仅用于保持既定的产物
构建器；由于它会强制安装不兼容的旧 runtime，必须放在独立的 Python 3.13
`.lbp-build-venv`，不得装入 `.venv`。统一脚本用旧 builder 生成 ZIP，再由
现代 runtime 环境完成解包入口、组件基类与资源加载验收。
Windows 不在 2.1.0 发布与验收范围内。冻结安装默认使用官方 PyPI 并忽略
用户级额外索引；
可信镜像可通过 `HENU_PYPI_INDEX_URL` 显式覆盖。

统一构建脚本会检查压缩包完整性，加载
`campus_core/config/building_seed.json` 与
`campus_core/config/library_locations.json`，并拒绝 `.env` 或运行时 JSON
状态进入产物。验证已有产物可运行：

```bash
.venv/bin/python scripts/build_plugin.py \
  --verify-only dist/jry21223-henu_assistant-2.1.0.lbpkg
```

`.env` 关键字段：

| 变量 | 说明 |
| --- | --- |
| `DEBUG_RUNTIME_WS_URL` | 本地调试 WebSocket（默认 `ws://127.0.0.1:5401/debug/ws`） |
| `PLUGIN_DEBUG_KEY` | 调试鉴权；空表示关闭 |
| **`HENU_MASTER_KEY`** | 凭据加密主密钥；**生产必填且保持稳定** |
| `HENU_IMPORT_V204_ROLLBACK` | 仅限停服后从 v2.0.4 回升时的一次性显式 handoff；正常运行不得设置 |

> **部署限制（2.1.0）**：每个 Storage 后端只运行一个 LangBot worker
> 进程。当前跨请求冲突检测依赖进程内锁，后端尚无跨 worker 原子 CAS；多个
> worker 共享同一 Storage 可能丢更新，发布门禁会按单进程部署验收。

## CI 发布

`.github/workflows/test-python.yaml` 在 Linux runner 上对 Python 3.10–3.14
运行 hash fresh install、`pip check`、`compileall` 和完整测试。
`.github/workflows/release-lbp.yaml` 固定使用 Python 3.13，运行环境安装
`langbot-plugin==0.5.0`，独立 hash-locked builder 安装 `lbp==0.1.2`，再通过
统一脚本构建并验证产物。Tag push 不会自动发布；只有在 `v2.1.0`
上手动调度、验证三端固定 SHA 与三端成功 CI（含 LangBot 全版本矩阵）、附真实只读 smoke 证据并通过
`henu-production-release` 受保护环境审批，才上传 GitHub Release。完整顺序、
单进程部署限制与回滚规则见 [`RELEASE_TRAIN.md`](./RELEASE_TRAIN.md)。

---

## 常用命令

工具只收一条字符串 `command`。不确定时先 `help` 或 `help <topic>`。

```text
help
help library
account status
schedule now
schedule day --date YYYY-MM-DD
course status
course plan --excel ./courses.xlsx --class 25软工1 --like-early8 --compact-days --target-days 3
course filter --excel ./courses.xlsx --class 25软工1
course schema
course monitor config --config-json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
course monitor once
empty_classroom query --week 1 --day-of-week 1 --period 1 --building-text 十号楼
empty_classroom query --view occupancy --classroom-text 十号楼101
resource search 十号楼101
library current
library locations --date YYYY-MM-DD
library seats --location "<区域>" --date YYYY-MM-DD --time 08:00
library reserve --location "<区域>" --seat-no "<座位号>" --date YYYY-MM-DD --time 08:00
seminar rooms --date YYYY-MM-DD --start 14:00 --end 16:00 --members 4
seminar signin --auto-scan
confirm <token>
```

### 敏感命令（仅私聊直发）

账号绑定与校准必须在**私聊中由用户直接发送**（不要让模型用 Tool 拼密码）：

```text
account set --student-id <学号> --password '<密码>'
calibration set --data '<请求体>' --cookie '<Cookie>'
```

事件监听器会在进模型前拦截这两类命令；密码、Cookie、校准请求体不会进入模型消息或历史。**群聊中的敏感命令会被拒绝。**

### 外部写操作：两阶段确认

图书馆 / 研讨室的预约、签到、取消：

1. 首次调用只生成预览 + 短期确认令牌，**不**提交校园系统。  
2. 用户核对后，下一条消息发送工具返回的 `confirm <token>`。  
3. 同一轮自动确认、令牌过期、参数变化 → 拒绝。

若 `external_committed=true` 且 `storage_persisted=false`：校园已提交、本地 Storage 失败——**不得重试**，应先查询当前预约/记录反查。

---

## 时间语义

每个请求的临时 system prompt 注入 `[HENU_RUNTIME_CONTEXT_V2]`：

- 校园时间默认 `Asia/Shanghai`。  
- 时间快照每请求重新生成，不用五分钟缓存。  
- `schedule now/current` 不缓存。  
- 时间 / QQ / 学号状态不写进用户消息历史。  
- 非法时区会标明请求时区与实际生效时区。

`week_filter_applied=false` 时，不要把结果说成已按教学周精确过滤。

## 数据隔离

| 规则 | 行为 |
| --- | --- |
| 群聊 | 优先 `sender_id` |
| 私聊 | 可用 `launcher_id` |
| 缺发送者 | fail closed，不回退群号 / `unknown` |

Storage Adapter：

- 每请求独立 staging；同一用户 load/execute/save 由 per-user lock 串行。  
- 用户文件以一个 `snapshot_v2` 权威快照提交，账号与 Cookie 不会出现半代组合；读取异常不当作空数据。
- 保存前乐观冲突检查。  
- 成功提交后同步旧版 individual keys 作为 `v2.0.4` 降级镜像；启动时会修复中断的镜像。
- xiqueer Cookie 为用户私有；共享区只放无个人凭据的节次时间与校准状态。

用户目录：账号、IDS/CAS Cookie、业务 Cookie/Token、课表、研讨室签到任务、选课监控。  
插件版**不**启动后台研讨室自动签到线程；需要时 `seminar signin --auto-scan` 并完成二次确认。

## CLI 输出与安全

- QQ 载荷约 2200 字符预算；长列表生成机器可读摘要。  
- 缓存深复制，截断不影响后续结果。  
- `source=live_empty` 明确失败，禁止猜开放时间/区域。  
- 密码、Cookie、Ticket、Token、确认令牌、校准 data 不进普通日志。  
- 工具返回 `reply_text` 时，最终回复优先复述/压缩 `reply_text`，不要整段 JSON 甩给用户。

## 登录

IDS 优先；失败时只一次 Kingo。Kingo 主要保 xk 能力，不生成/覆盖其他服务 CAS Cookie。验证码 → `captcha_required`。

## 目录

```text
manifest.yaml
main.py
install.sh                  # 运行 venv + 隔离 builder venv
requirements-dev.txt        # 运行/测试依赖（固定现代 LangBot runtime）
requirements-build.txt      # 独立构建工具输入（固定 lbp 版本）
scripts/build_plugin.py     # 唯一构建 + 产物验证入口
components/event_listener/  # 身份、时间注入、敏感命令
components/cli_tools/       # CLI、写确认、QQ 安全输出
henu_plugin/hardened_service.py
henu_plugin/storage_adapter.py
henu_plugin/confirmation.py
henu_mcp/  campus_core/
tests/
```

## 测试

```bash
.venv/bin/python -m pytest
```

## 边界

- 需要河大学生账号与可访问校园系统的网络。  
- 仅供学习与个人使用。  
- 正式认证 / 成绩 / 选课 / 财务以官方渠道为准。

## License

[MIT](LICENSE)
