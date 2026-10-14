# 河大校园助手 · Agent Skill

面向 **Agent Skill / 本地 CLI** 的校园助手。能力与 MCP 服务器版对齐：课表、选课状态/规划、空教室、资源映射、图书馆、研讨室、河宝社区、节次校准与系统状态。

> 同能力的其他形态：[`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server)、[`langbot-plugin`](https://github.com/jry21223/HENU_Assistant/tree/langbot-plugin)。

## 安装

```bash
git clone -b agent-skill https://github.com/jry21223/HENU_Assistant.git henu_campus_assistant
cp -r henu_campus_assistant ~/.openclaw/workspace/skills/
cd ~/.openclaw/workspace/skills/henu_campus_assistant

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Ubuntu/Debian 若遇到 *externally managed environment*：

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

Agent 侧推荐始终通过虚拟环境解释器调用（见 `SKILL.md`）：

```bash
cd ~/.openclaw/workspace/skills/henu_campus_assistant && .venv/bin/python henu_cli.py <command> [args]
```

## CLI 命令

| 场景 | 命令 |
| --- | --- |
| 账号 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query --view current\|day\|week\|full` |
| 选课 | `smart_course_selection`, `course_selection_query`, `course_selection_plan`, `course_selection_submit`, `course_monitor_config`, `course_monitor_once`, `course_monitor_run`, `course_monitor_notify_test` |
| 空教室 | `empty_classroom_query`, `empty_classroom_sync` |
| 资源映射 | `resource_registry_query`, `resource_registry_sync` |
| 图书馆 | `library_query --view locations\|seats\|current\|records`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝社区 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 校准 | `set_calibration_source` |

完整参数示例见 [`SKILL.md`](./SKILL.md)。

## 最短流程

### 图书馆

```bash
.venv/bin/python henu_cli.py system_status
.venv/bin/python henu_cli.py library_query --view locations
.venv/bin/python henu_cli.py library_query --view seats --location "<区域>"
.venv/bin/python henu_cli.py library_reserve --location "<区域>" --seat_no "<座位号>"
```

### 研讨室

```bash
.venv/bin/python henu_cli.py seminar_query --view filters
.venv/bin/python henu_cli.py seminar_query --view rooms \
  --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --members 4
.venv/bin/python henu_cli.py seminar_reserve \
  --area_id <ID> --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM \
  --content "<用途说明，须多于10个字>"
```

### 空教室 / 资源映射

```bash
.venv/bin/python henu_cli.py empty_classroom_query \
  --week 1 --day_of_week 1 --period 1 --building_text "十号楼"
.venv/bin/python henu_cli.py empty_classroom_query \
  --view occupancy --classroom_text "十号楼101"
.venv/bin/python henu_cli.py resource_registry_query \
  --query "十号楼101" --building_code "<楼房代码>"
```

### 智能选课 / 监控

```bash
.venv/bin/python henu_cli.py smart_course_selection \
  --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
.venv/bin/python henu_cli.py course_selection_query --view status --xktype 2
.venv/bin/python henu_cli.py course_monitor_config \
  --config_json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
.venv/bin/python henu_cli.py course_monitor_once
```

## 目录结构

```text
henu_cli.py            # CLI 入口
mcp_server.py          # 兼容 MCP/Agent 的薄门面
SKILL.md               # Agent Skill 元数据与命令说明
henu_mcp/              # 复用的业务逻辑与路径切换
campus_core/           # 底层校园集成
scripts/               # 辅助脚本
tests/                 # pytest
```

## 认证与说明

- 账号与 Cookie **仅本地保存**。  
- 教务登录优先 IDS（`CASTGC` / `TGC`）；失败时**仅自动一次** xk Kingo 降级，不识别验证码、不循环重试。  
- IDS 可跨服务 SSO；Kingo 主要保证课表 / 选课状态 / 空教室等 xk 能力，不生成或覆盖 CAS Jar。  
- 账号初始化、登录检查、课表同步、系统状态通过 `auth` 返回 `mode`、`degraded`、`error_code`、`warning`。  
- 研讨室 `group` **不包含自己**，建议保存 3–9 名同行成员；申请内容须 **>10 字**。  
- `course_selection_query` / `course_monitor_*` 为只读；`course_selection_submit` 若未接真实提交会返回未实现提示。

## 智能选课

模块：`campus_core.smart_course_selector`。

- 从 Excel 或清洗后的 JSON 读取选项；按班级筛选。  
- 偏好：早八、集中天数、避免晚课、是否允许未排时间。  
- 输出：`henu.smart_course_selection.v1`（含 `plans[].selection_actions` dry-run 输入）。  
- 监控只读余量，可发飞书提醒，**不会**自动提交选课。

## 测试

```bash
.venv/bin/pip install pytest   # 若环境中尚无
.venv/bin/pytest
```
