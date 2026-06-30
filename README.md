# 河大校园助手 Agent Skill

Agent Skill 版校园助手，能力与 MCP 服务器版保持一致：课表、选课状态/规划、空教室、资源映射、图书馆、研讨室、河宝社区、节次校准和系统状态。

## 安装

```bash
git clone -b agent-skill https://github.com/jry21223/HENU_Assistant.git henu_campus_assistant
cp -r henu_campus_assistant ~/.openclaw/workspace/skills/
cd ~/.openclaw/workspace/skills/henu_campus_assistant

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Ubuntu/Debian 如遇到 externally managed environment，先安装：

```bash
sudo apt install -y python3-venv
```

## CLI 入口

| 场景 | 命令 |
| --- | --- |
| 账号 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query --view current|day|week|full` |
| 选课 | `smart_course_selection`, `course_selection_query`, `course_selection_plan`, `course_selection_submit`, `course_monitor_config`, `course_monitor_once`, `course_monitor_run`, `course_monitor_notify_test` |
| 空教室 | `empty_classroom_query`, `empty_classroom_sync` |
| 资源映射 | `resource_registry_query`, `resource_registry_sync` |
| 图书馆 | `library_query --view locations|seats|current|records`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝社区 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 校准 | `set_calibration_source` |

## 目录结构

- `henu_cli.py`：Agent Skill 的命令行入口。
- `mcp_server.py`：兼容 MCP/Agent 公共工具函数的薄门面。
- `henu_mcp/`：复用的校园业务逻辑和运行时路径切换。
- `campus_core/`：底层校园集成模块。

## 最短流程

图书馆：

```bash
python3 henu_cli.py system_status
python3 henu_cli.py library_query --view locations
python3 henu_cli.py library_query --view seats --location "<区域>"
python3 henu_cli.py library_reserve --location "<区域>" --seat_no "<座位号>"
```

实际运行建议使用虚拟环境解释器：

```bash
.venv/bin/python henu_cli.py system_status
```

研讨室：

```bash
python3 henu_cli.py seminar_query --view filters
python3 henu_cli.py seminar_query --view rooms --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --members 4
python3 henu_cli.py seminar_reserve --area_id <ID> --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --content "<用途说明>"
```

空教室与资源映射：

```bash
python3 henu_cli.py empty_classroom_query --week 1 --day_of_week 1 --period 1 --building_text "十号楼"
python3 henu_cli.py empty_classroom_query --view occupancy --classroom_text "十号楼101"
python3 henu_cli.py resource_registry_query --query "十号楼101" --building_code "<楼房代码>"
```

## 说明

- 账号与 Cookie 仅本地保存；同一账号会复用本用户的 IDS CAS Cookie jar（`CASTGC`/`TGC` 等），失败后再回退密码登录。
- 研讨室 `group` 不包含自己，建议保存 3-9 个同行成员。
- 研讨室申请内容必须多于 10 个字。
- 不建议直接执行系统级 `pip3 install -r requirements.txt`。

## 智能选课

本版本包含通用智能选课模块 `campus_core.smart_course_selector`，三种接入形态复用同一套逻辑；同时保留 xk 只读状态查询入口：

- 从教务导出的 Excel 或清洗后的 JSON 读取课程选项。
- 按班级筛选班级对应专业课、专业选课班 / 专业公共课、全年级公共课。
- 根据偏好规划无冲突课表：早八偏好、集中上课天数、避免晚课、是否允许未排时间。
- 输出统一结构 `henu.smart_course_selection.v1`，其中 `plans[].selection_actions` 可作为后续自动选课提交器的 dry-run 输入。
- `course_selection_query` 会先走统一认证和 xk frame 菜单入口，只查询选课状态，不提交教务系统。
- `course_monitor_*` 只读监控指定教学班余量，检测到余量变化时可发飞书提醒；不会点击选课、提交或退选。

MCP / Agent Skill 示例：

```bash
python3 henu_cli.py smart_course_selection --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
python3 henu_cli.py course_selection_query --view status --xktype 2
python3 henu_cli.py course_monitor_config --config_json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
python3 henu_cli.py course_monitor_once
```

Langbot CLI 示例：

```text
course plan --excel ./courses.xlsx --class 25软工1 --like-early8 --compact-days --target-days 3
course filter --excel ./courses.xlsx --class 25软工1
course schema
```
