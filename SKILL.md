---
name: henu_campus_assistant
description: 河南大学校园助手本地 CLI Skill。用于课表查询/同步、选课状态与智能规划、选课余量只读监控（可飞书提醒）、空教室、资源映射、图书馆座位预约/签到/取消、研讨室成员组与预约/签到/取消、河宝社区请假/签到/查寝/活动/信息收集、节次校准与系统状态。当用户提到河大、HENU、教务、课表、选课、空教室、图书馆座位、研讨室、河宝、查寝、请假、喜鹊校准等时使用。能力与 mcp-server 对齐；命令为 snake_case 子命令，不是 LangBot 空格风格 CLI。
---

# 河大校园助手

面向 Agent Skill 的本地 Skill：用 **本 skill 目录内** 的 `henu_cli.py` 调用校园能力。

## 命令方言（必读）

| 形态 | 示例 |
| --- | --- |
| **本 Skill / MCP 工具名** | `schedule_query`、`library_query`、`setup_account --student_id` |
| LangBot 插件 | `schedule now`、`library seats`、`account set --student-id` |

只使用本文件中的 **snake_case 子命令**。不要把 LangBot 的空格命令粘贴到这里。

## 定位 skill 根目录

根目录特征：同时存在 `henu_cli.py`、`SKILL.md`、`requirements.txt`、`henu_mcp/`。

1. 若当前工作目录已是 skill 根目录，直接使用。  
2. 否则在常见位置查找（按顺序，找到即停）：
   - 本仓库检出目录（例如 clone 后的路径）
   - `$HOME/.openclaw/workspace/skills/henu_campus_assistant`
   - `$HOME/.claude/skills/henu_campus_assistant`
   - `$HOME/.codex/skills/henu_campus_assistant`
3. 将找到的路径记为 `SKILL_DIR`。后续所有命令都在该目录执行。

**不要写死** `~/.openclaw/...` 为唯一路径。

## 环境准备

```bash
cd "$SKILL_DIR"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
```

Ubuntu/Debian 若 *externally managed environment*：`sudo apt install -y python3-venv`。  
不要用系统级 `pip3 install -r requirements.txt`。

执行模板（始终用 venv 解释器）：

```bash
cd "$SKILL_DIR" && .venv/bin/python henu_cli.py <command> [args]
```

## 决策流程

1. **相对时间**（现在/今天/明天/当前预约/待签到）→ 先 `system_status`。  
2. **未绑定账号** 或登录失败 → `setup_account`（勿在回复中回显密码）。  
3. 按领域走最短路径（见下）。  
4. 解释结果时遵守契约：`success`、`auth.mode` / `degraded` / `error_code`、`source=live_empty`、`captcha_required`。  
5. **禁止**：识别验证码、循环重试登录、编造开放时间/座位/区域、把 Cookie/密码写进日志或用户可见回复。

### 图书馆

```text
system_status
library_query --view locations
library_query --view seats --location "<返回的区域>" [--target_date YYYY-MM-DD]
library_reserve --location "..." --seat_no "<返回的座位号>"
library_query --view current
```

### 研讨室

```text
seminar_query --view filters
seminar_query --view rooms --target_date YYYY-MM-DD --start_time HH:MM --end_time HH:MM --members N
seminar_query --view detail --area_id <ID>
seminar_reserve --area_id <ID> --target_date ... --start_time ... --end_time ... --content "<>10字>"
```

### 空教室 / 资源

```text
empty_classroom_query --week W --day_of_week D --period P --building_text "十号楼"
empty_classroom_query --view occupancy --classroom_text "十号楼101"
resource_registry_query --query "十号楼101"
```

### 课表

```text
sync_schedule          # 需要刷新时
schedule_query --view current|day|week|full
# day 视图必须带 --target_date YYYY-MM-DD
```

### 选课 / 监控（只读提醒）

```text
smart_course_selection --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3
course_selection_query --view status --xktype 2
course_monitor_config --config_json '...'
course_monitor_once
# course_selection_submit 当前不执行真实提交
```

### 河宝

```text
system_status
yunfz_leave_query --view list|detail|statistics
yunfz_signin_query --view list|statistics
yunfz_checksleep_query --view list|statistics
yunfz_activity_query --view list|statistics
yunfz_collection_query --view list|statistics
```

## 功能与命令一览

- 课表：`setup_account`、`sync_schedule`、`schedule_query`
- 选课：`smart_course_selection`、`course_selection_query`、`course_selection_plan`、`course_selection_submit`、`course_monitor_config`、`course_monitor_once`、`course_monitor_run`、`course_monitor_notify_test`
- 空教室：`empty_classroom_query`、`empty_classroom_sync`
- 资源映射：`resource_registry_query`、`resource_registry_sync`
- 图书馆：`library_query`、`library_reserve`、`library_auto_signin`、`library_cancel`
- 研讨室：`seminar_group`、`seminar_query`、`seminar_signin`、`seminar_reserve`、`seminar_cancel`
- 系统：`set_calibration_source`、`system_status`
- 河宝社区：`yunfz_leave_query`、`yunfz_signin_query`、`yunfz_checksleep_query`、`yunfz_activity_query`、`yunfz_collection_query`

## 常用参数示例

- `setup_account --student_id "<学号>" --password "<密码>"`
- `sync_schedule`
- `schedule_query --view current`
- `schedule_query --view day --target_date "2026-03-19"`
- `schedule_query --view week`
- `schedule_query --view full`
- `smart_course_selection --excel ./courses.xlsx --class 25软工1 --like_early8 --compact_days --target_days 3`
- `course_selection_query --view status --xktype 2`
- `course_selection_plan --candidates_json "<JSON>"`
- `course_selection_submit`（当前只返回未实现提示，不执行真实提交）
- `course_monitor_config --config_json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'`
- `course_monitor_once`（只读检查余量，必要时提醒，不执行选课提交）
- `empty_classroom_query --week 1 --day_of_week 1 --period 1 --building_text "十号楼"`
- `empty_classroom_query --view occupancy --classroom_text "十号楼101"`
- `empty_classroom_query --view classrooms --type_code "01" --min_capacity 80 --keyword "多媒体"`
- `empty_classroom_sync --term_code "2025,1" --campus_code "01" --building_code "0013" --type_code "01" --force_refresh`
- `resource_registry_query --query "十号楼101" --building_code "<楼房代码>"`
- `library_query --view locations`
- `library_query --view seats --location "<区域>" --target_date "2026-03-19" --preferred_time "08:00"`
- `library_query --view current`
- `library_query --view records --record_type 1 --page 1 --limit 20`
- `library_reserve --location "<区域>" --seat_no "<座位号>" --preferred_time "10:30"`
- `library_auto_signin [--record_id "<记录ID>"]`
- `library_cancel --record_id "<记录ID>"`
- `seminar_group --action save --group_name "<组名>" --member_ids "<学号1,学号2,学号3>" [--note "<备注>"]`
- `seminar_group --action list`
- `seminar_group --action delete --group_name "<组名>"`
- `seminar_query --view filters`
- `seminar_query --view rooms --target_date "2026-03-14" --members 0 [--library_names "<馆舍名>"]`
- `seminar_query --view detail --area_id "<房间ID>" [--target_date "2026-03-14"]`
- `seminar_query --view records --record_type 1 --mode books`
- `seminar_query --view signin_tasks [--status "pending,success"]`
- `seminar_signin --record_id "<记录ID>"`
- `seminar_signin --auto_scan`
- `seminar_reserve --area_id "<房间ID>" --target_date "2026-03-14" --start_time "14:00" --end_time "16:00" --group_name "<组名>" --title "<主题>" --content "<超过10字的申请说明>" --mobile "<手机号>"`
- `seminar_cancel --record_id "<记录ID>"`
- `set_calibration_source --data "<DATA>" --cookie "<COOKIE>"`
- `system_status`
- `yunfz_leave_query --view list|detail|statistics [--leave_id "<请假ID>"] [--page 1] [--page_size 20]`
- `yunfz_signin_query --view list|statistics [--page 1] [--page_size 20]`
- `yunfz_checksleep_query --view list|statistics [--page 1] [--page_size 20]`
- `yunfz_activity_query --view list|statistics [--page 1] [--page_size 20]`
- `yunfz_collection_query --view list|statistics [--page 1] [--page_size 20]`

## 注意

- 首次使用先执行 `setup_account`
- 如 `.venv/bin/python` 不存在，先按「环境准备」创建
- 课表 `current` 只查「当前正在上的课 + 下一节课」；查某一天用 `schedule_query --view day --target_date "YYYY-MM-DD"`
- `course_selection_query` 只读；`course_monitor_*` 只读监控余量并提醒；`course_selection_submit` 当前不执行真实提交
- 图书馆预约前先用 `library_query --view locations`；后续只使用返回的 `locations[].location` 或 `locations[].area_id`
- 座位结果中的 `area`、`target_date`、`time_window`、`total_count`、`available_count`、`status_counts` 必须原样保留；预约只使用返回的 `seats[].seat_no`
- `source=live_empty` 且 `success=false` 时，只如实说明实时接口为空，不推测开放时间、区域或推荐替代；`fallback_locations` 只能作为静态参考
- 账号、密码、Cookie、Ticket、Token 和校准 `--data` 不得写入日志或回复；真实写操作只以 `success=true` 为准
- 研讨室 `group` 不含自己；建议 3–9 个同行学号；申请说明必须多于 10 个字
- 河宝相关查询前先 `system_status`
- 教务登录优先 IDS（`CASTGC`/`TGC`）；失败后仅自动一次 xk Kingo；不生成/覆盖 CAS Jar
- 遇到 Kingo 验证码立即返回 `captcha_required`，禁止识别、绕过或循环重试；根据 `auth.mode`、`degraded`、`error_code`、`warning` 说明能力边界
