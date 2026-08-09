<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="河大校园助手 MCP 服务器：stdio MCP tools，本地课表 / 空教室 / 图书馆 / 研讨室">
</p>

把河大校园能力暴露为 **MCP tools**。当前产品版本为 **2.1.0**，使用
`mcp==2.0.0`，支持 stdio、仅回环地址的 Streamable HTTP 与 SSE。

首页与其它形态：[`main`](https://github.com/jry21223/HENU_Assistant) · [`langbot-plugin`](https://github.com/jry21223/HENU_Assistant/tree/langbot-plugin) · [`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill)

> **入口说明**：本分支入口是 `mcp_server.py`（MCP tools）。  
> **没有** `henu_cli.py`。若需要本地 CLI / Agent Skill，请使用 [`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill) 分支。  
> 工具名与 agent-skill 子命令对齐（`schedule_query` 等），与 LangBot 空格 CLI 不同。

---

## 安装

```bash
git clone -b mcp-server https://github.com/jry21223/HENU_Assistant.git henu-mcp
cd henu-mcp
python3 -m venv venv
LOCK_FILE="$(python3 scripts/select_lockfile.py --check)"
HENU_PYPI_INDEX_URL="${HENU_PYPI_INDEX_URL:-https://pypi.org/simple}"
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL="$HENU_PYPI_INDEX_URL" PIP_EXTRA_INDEX_URL= \
  venv/bin/python -m pip install --require-hashes -r "$LOCK_FILE"
venv/bin/python diagnose_mcp.py
```

也可：`./install.sh`（创建 `venv/`、安装依赖并跑 `diagnose_mcp.py`）。
本版在 POSIX（macOS/Linux）支持 Python 3.10–3.14；Windows 不在 2.1.0
发布与验收范围内。

## 启动

```bash
python3 mcp_server.py --transport stdio
# 或
./run.sh
```

`diagnose_mcp.py` 会真实启动两个 stdio 子进程，分别验证 modern
`server/discover` 与 legacy `initialize`，然后执行 `tools/list` 和一个无凭据、
无写入的 `course_selection_submit` 安全调用。任何一项失败都会返回非零退出码。

HTTP transport 默认只允许 `127.0.0.1`、`localhost` 或 `::1`；在尚未配置认证前，
非回环绑定会 fail closed：

```bash
python3 mcp_server.py --transport streamable-http \
  --host 127.0.0.1 --port 8001 --path /mcp

python3 mcp_server.py --transport sse \
  --host 127.0.0.1 --port 8001 \
  --sse-path /sse --message-path /messages/
```

### Cherry Studio

```json
{
  "mcpServers": {
    "henu-campus": {
      "command": "bash",
      "args": [
        "-lc",
        "cd \"<YOUR_PROJECT_PATH>\" && source venv/bin/activate && python3 mcp_server.py --transport stdio"
      ]
    }
  }
}
```

将 `<YOUR_PROJECT_PATH>` 换成上面 clone 后的绝对路径。

---

## 工具

| 类别 | 工具 |
| --- | --- |
| 账号 / 系统 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query` |
| 选课 | `smart_course_selection`, `smart_course_select`, `course_selection_query`, `course_selection_plan`, `course_selection_submit`, `course_monitor_config`, `course_monitor_once`, `course_monitor_run`, `course_monitor_notify_test` |
| 空教室 | `empty_classroom_query`, `empty_classroom_sync` |
| 资源映射 | `resource_registry_query`, `resource_registry_sync` |
| 图书馆 | `library_query`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 校准 | `set_calibration_source` |

## 常用流程

1. `setup_account` 绑定学号、密码（可存默认图书馆区域/座位）。  
2. 相对时间先 `system_status`。  
3. 图书馆：`locations` → `seats` → `library_reserve` → `current`。  
4. 空教室：`empty_classroom_query(view="free")`；不确定校区/楼房时先 `campuses` / `buildings`。  
5. 研讨室：`filters` → `rooms` → `detail` → `seminar_reserve`。  
6. 选课规划：Excel/JSON + 偏好调用 `smart_course_selection`；监控用 `course_monitor_*`（只读）。

### 图书馆结果契约

- 先 `library_query(view="locations")`，后续只用返回的 `locations[].location` / `area_id`。  
- 座位预约只用 `seats[].seat_no`。  
- `source="live_empty"` 表示实时接口明确空列表（`success=false`），**不要**推测开放时间或替代方案。  
- 写操作只以 `success=true` 为准。

## 给 MCP 客户端 / LLM

- 空教室 / 自习室：**不要**说「不支持」，优先 `empty_classroom_query`。  
- 「今天 / 现在」先 `system_status`，再传具体日期/周次/节次。  
- 大 JSON 只概括 `msg` / `rooms` / `data`，不要整段粘贴。  
- 需要 shell CLI 时改用 agent-skill，不要在本目录找 `henu_cli.py`。

---

## 登录与数据

- 账号、Cookie、抓取结果**仅本地保存**。  
- Profile、Cookie、研讨室任务、选课监控与资源 registry 使用同目录临时文件、
  `flush` / `fsync` 和原子替换，避免进程中断留下半写 JSON。
- **IDS CAS 优先**（`CASTGC` / `TGC`）；失败时只自动 **一次** xk Kingo。  
- 不识别验证码、不绕过风控、不循环重试。  
- Kingo 主要保课表 / 选课状态 / 空教室；图书馆、研讨室、河宝仍需 IDS。  
- `auth` 返回 `mode`（`ids_cas` / `xk_kingo`）、`degraded`、`error_code`、`warning`。

## 智能选课

模块：`campus_core.smart_course_selector`。

- Excel / JSON 读选项；按班级筛选。  
- 偏好：早八、集中天数、避免晚课、未排时间。  
- 输出：`henu.smart_course_selection.v1`。  
- `course_selection_query` / `course_monitor_*` **只读**；监控可飞书提醒，不自动提交。

通过 **MCP tool** `smart_course_selection` 调用（参数与 agent-skill 对齐），例如在客户端里传入 excel 路径、班级与偏好标志。本地 shell 示例请使用 agent-skill：

```bash
# 仅 agent-skill 分支存在 henu_cli.py
.venv/bin/python henu_cli.py smart_course_selection \
  --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
```

## 目录

```text
mcp_server.py       # 唯一 transport runner
diagnose_mcp.py
install.sh / run.sh
henu_mcp/api.py     # transport-free 权威工具契约
henu_mcp/executor.py # stateful gate + runtime execution boundary
henu_mcp/adapters/  # MCPServer 2.0 adapter
henu_mcp/core/      # 课表、存储、选课监控
henu_mcp/tools/     # MCP 工具实现
henu_mcp/runtime.py
campus_core/        # 图书馆 / 研讨室 / 河宝 / 空教室 / 智能选课
tests/
```

## 测试

```bash
source venv/bin/activate
pytest
python3 diagnose_mcp.py
python3 scripts/stdio_smoke.py
```

CI 在 Linux runner 上对 Python 3.10–3.14 选择对应的
`requirements-lock/py3xx.txt`，使用 `pip --require-hashes` 安装，再依次执行
`pip check`、`compileall`、完整 pytest、诊断与真实 stdio 冒烟。独立
Python 3.11 job 会把
`mcp==1.29.0` 真客户端装入隔离 venv，再连接 MCP 2 server 完成
initialize/list/call。锁文件缺失时 `scripts/select_lockfile.py --check` 会直接
失败，避免回退到未冻结依赖。
冻结锁默认从官方 PyPI 安装并忽略用户级额外索引；如使用已完整同步的可信镜像，
可显式设置 `HENU_PYPI_INDEX_URL`。

## License

[MIT](LICENSE)
