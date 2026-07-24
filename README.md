<p align="center">
  <img src="./assets/readme/hero.png" width="100%" alt="河大校园助手 Agent Skill：本地 CLI，能力与 MCP 版对齐">
</p>

面向 **Agent Skill / 本地 CLI** 的校园助手。能力与 MCP 服务器版对齐。

其它形态：[`main`](https://github.com/jry21223/HENU_Assistant) · [`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server) · [`langbot-plugin`](https://github.com/jry21223/HENU_Assistant/tree/langbot-plugin)

---

## 安装

```bash
git clone -b agent-skill https://github.com/jry21223/HENU_Assistant.git henu_campus_assistant
cp -r henu_campus_assistant ~/.openclaw/workspace/skills/
cd ~/.openclaw/workspace/skills/henu_campus_assistant

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Ubuntu/Debian 若 *externally managed environment*：

```bash
sudo apt install -y python3-venv
```

> 不要用系统级 `pip3 install -r requirements.txt`。

## 快速开始

```bash
.venv/bin/python henu_cli.py system_status
.venv/bin/python henu_cli.py schedule_query --view current
.venv/bin/python henu_cli.py library_query --view locations
```

Agent 侧建议始终走虚拟环境解释器（见 `SKILL.md`）：

```bash
cd ~/.openclaw/workspace/skills/henu_campus_assistant && .venv/bin/python henu_cli.py <command> [args]
```

---

## 命令

| 场景 | 命令 |
| --- | --- |
| 账号 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query --view current\|day\|week\|full` |
| 选课 | `smart_course_selection`, `course_selection_query`, `course_selection_plan`, `course_selection_submit`, `course_monitor_config`, `course_monitor_once`, `course_monitor_run`, `course_monitor_notify_test` |
| 空教室 | `empty_classroom_query`, `empty_classroom_sync` |
| 资源映射 | `resource_registry_query`, `resource_registry_sync` |
| 图书馆 | `library_query --view locations\|seats\|current\|records`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 校准 | `set_calibration_source` |

完整参数见 [`SKILL.md`](./SKILL.md)。

## 最短流程

### 图书馆

```bash
.venv/bin/python henu_cli.py system_status
.venv/bin/python henu_cli.py library_query --view locations
.venv/bin/python henu_cli.py library_query --view seats --location "<区域>"
.venv/bin/python henu_cli.py library_reserve --location "<区域>" --seat_no "<座位号>"
```

图书馆契约：先 `locations`，只用返回的 `location` / `area_id` 与 `seats[].seat_no`。  
`source=live_empty` 为明确空结果（`success=false`），不要猜区域或开放时间。写操作只认 `success=true`。

### 研讨室

```bash
.venv/bin/python henu_cli.py seminar_query --view filters
.venv/bin/python henu_cli.py seminar_query --view rooms \
  --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --members 4
.venv/bin/python henu_cli.py seminar_reserve \
  --area_id <ID> --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM \
  --content "<用途说明，须多于10个字>"
```

`group` 不含自己，建议 3–9 名同行。

### 空教室 / 资源

```bash
.venv/bin/python henu_cli.py empty_classroom_query \
  --week 1 --day_of_week 1 --period 1 --building_text "十号楼"
.venv/bin/python henu_cli.py empty_classroom_query \
  --view occupancy --classroom_text "十号楼101"
.venv/bin/python henu_cli.py resource_registry_query \
  --query "十号楼101" --building_code "<楼房代码>"
```

### 选课 / 监控

```bash
.venv/bin/python henu_cli.py smart_course_selection \
  --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
.venv/bin/python henu_cli.py course_selection_query --view status --xktype 2
.venv/bin/python henu_cli.py course_monitor_config \
  --config_json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
.venv/bin/python henu_cli.py course_monitor_once
```

`course_selection_query` / `course_monitor_*` 只读；监控可飞书提醒，不自动选课。

---

## 登录与说明

- 账号与 Cookie 仅本地保存。  
- IDS（`CASTGC` / `TGC`）优先；失败只自动一次 Kingo。  
- Kingo 主要保 xk 能力，不生成/覆盖 CAS Jar。  
- `auth`：`mode` / `degraded` / `error_code` / `warning`。  
- 敏感值（密码、Cookie、Ticket、Token、`--data`）不作为公开结果回显。

## 目录

```text
henu_cli.py      # CLI
mcp_server.py    # 薄门面
SKILL.md
henu_mcp/
campus_core/
scripts/
tests/
```

## 测试

```bash
.venv/bin/pip install pytest   # 若尚无
.venv/bin/pytest
```

## License

[MIT](LICENSE)
