# 河大校园助手 MCP 服务器版

把河大校园能力暴露为 MCP tools，适合 Cherry Studio 等支持 stdio MCP 的客户端。

## 安装与诊断

```bash
git clone -b mcp-server https://github.com/jry21223/HENU_Assistant.git henu-mcp
cd henu-mcp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 diagnose_mcp.py
```

Windows 将激活命令换成：

```cmd
venv\Scripts\activate
```

## 启动

```bash
python3 mcp_server.py --transport stdio
# 或
./run.sh
```

Cherry Studio 示例：

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

## 工具

| 类别 | 工具 |
| --- | --- |
| 账号/系统 | `setup_account`, `system_status` |
| 课表 | `sync_schedule`, `schedule_query` |
| 选课 | `smart_course_selection`, `smart_course_select`, `course_selection_query`, `course_selection_plan`, `course_selection_submit`, `course_monitor_config`, `course_monitor_once`, `course_monitor_run`, `course_monitor_notify_test` |
| 图书馆 | `library_query`, `library_reserve`, `library_auto_signin`, `library_cancel` |
| 研讨室 | `seminar_group`, `seminar_query`, `seminar_reserve`, `seminar_signin`, `seminar_cancel` |
| 河宝社区 | `yunfz_leave_query`, `yunfz_signin_query`, `yunfz_checksleep_query`, `yunfz_activity_query`, `yunfz_collection_query` |
| 节次校准 | `set_calibration_source` |

## 常用流程

1. `setup_account` 绑定学号、密码，可保存默认图书馆区域和座位。
2. 查询前先用 `system_status` 确认服务器时间。
3. 图书馆预约前先查 `library_query(view="locations")`，可用 `library_query(view="seats")` 查看当前可用座位，预约后用 `library_query(view="current")` 核对。
4. 研讨室按 `filters -> rooms -> detail -> reserve` 查询和预约。

账号、Cookie 和抓取结果都保存在本地。

## 智能选课

本版本包含通用智能选课模块 `campus_core.smart_course_selector`，三种接入形态复用同一套逻辑；同时保留 xk 只读状态查询入口：

- 从教务导出的 Excel 或清洗后的 JSON 读取课程选项。
- 按班级筛选班级对应专业课、专业选课班 / 专业公共课、全年级公共课。
- 根据偏好规划无冲突课表：早八偏好、集中上课天数、避免晚课、是否允许未排时间。
- 输出统一结构 `henu.smart_course_selection.v1`，其中 `plans[].selection_actions` 可作为后续自动选课提交器的 dry-run 输入。
- `course_selection_query` 会先走统一认证和 xk frame 菜单入口，只查询选课状态，不提交教务系统。
- `course_monitor_*` 只读监控指定教学班余量，检测到余量变化时可发飞书提醒；不会点击选课、提交或退选。

MCP / OpenClaw 示例：

```bash
python3 henu_cli.py smart_course_selection --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
python3 henu_cli.py course_monitor_config --config_json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
python3 henu_cli.py course_monitor_once
```

Langbot CLI 示例：

```text
course plan --excel ./courses.xlsx --class 25软工1 --like-early8 --compact-days --target-days 3
course filter --excel ./courses.xlsx --class 25软工1
course schema
```
