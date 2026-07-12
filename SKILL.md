---
name: henu_campus_assistant
description: 河南大学校园助手，支持课表查询、选课状态/规划、空教室、资源映射、图书馆预约、研讨室预约（与 mcp-server 能力对齐）
---

# 河大校园助手

面向 Agent Skill 的本地 Skill，使用 CLI 调用内置核心能力。

## 功能

- 课表：`setup_account`、`sync_schedule`、`schedule_query`
- 选课：`smart_course_selection`、`course_selection_query`、`course_selection_plan`、`course_selection_submit`、`course_monitor_config`、`course_monitor_once`、`course_monitor_run`、`course_monitor_notify_test`
- 空教室：`empty_classroom_query`、`empty_classroom_sync`
- 资源映射：`resource_registry_query`、`resource_registry_sync`
- 图书馆：`library_query`、`library_reserve`、`library_auto_signin`、`library_cancel`
- 研讨室：`seminar_group`、`seminar_query`、`seminar_signin`、`seminar_reserve`、`seminar_cancel`
- 系统：`set_calibration_source`、`system_status`
- 河宝社区：`yunfz_leave_query`、`yunfz_signin_query`、`yunfz_checksleep_query`、`yunfz_activity_query`、`yunfz_collection_query`

## 执行方式

当用户询问课表/课程/图书馆/研讨室相关需求时，使用 `bash` 执行：

```bash
cd ~/.openclaw/workspace/skills/henu_campus_assistant && .venv/bin/python henu_cli.py <command> [args]
```

常用命令：

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
- 如 `.venv/bin/python` 不存在，先执行 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- 涉及“现在/今天/明天/当前预约/待签到”等相对时间时，先执行 `system_status`
- 课表里 `current` 只查“当前正在上的课 + 下一节课”；查某一天课程请用 `schedule_query --view day --target_date "YYYY-MM-DD"`
- `course_selection_query` 只读查询 xk 选课状态，会走统一认证与 frame 菜单入口；`course_monitor_*` 只读监控余量并提醒；`course_selection_submit` 当前不执行真实提交
- 图书馆预约前先用 `library_query --view locations` 确认区域，可用 `library_query --view seats` 查看当前可用座位
- 图书馆区域结果中的 `source`、`is_live`、`total`、`returned_count`、`truncated` 必须原样保留；后续只使用返回的 `locations[].location` 或 `locations[].area_id`
- 座位结果中的 `area`、`target_date`、`time_window`、`total_count`、`available_count`、`status_counts` 必须原样保留，预约只使用返回的 `seats[].seat_no`
- `source=live_empty` 且 `success=false` 时，只如实说明实时接口为空，不推测开放时间、区域或推荐替代方案；`fallback_locations` 只能作为静态参考
- 账号、密码、Cookie、Ticket、Token 和校准 `--data` 参数不得写入日志或回复；真实写操作只以 `success=true` 为准
- 图书馆查看当前预约或历史记录时，用 `library_query --view current` / `library_query --view records`
- 研讨室通常先按 `seminar_query --view filters` -> `seminar_query --view rooms` -> `seminar_query --view detail` 逐步查询
- 研讨室 `group` 保存的是同行成员学号，不含自己；建议保存 3-9 个学号，预约时会自动去重并排除当前账号
- 研讨室预约会按照房间限制校验总人数，通常为 4-5 人起、最多 10 人
- 研讨室申请说明必须多于 10 个字
- 研讨室可先用 `seminar_query --view records` 查记录，再用 `seminar_cancel` 取消
- 河宝社区相关查询前先执行 `system_status`
- 请假详情使用 `yunfz_leave_query --view detail --leave_id "..."`
- 账号与 Cookie 仅本地保存；教务登录优先复用或登录 IDS（`CASTGC`/`TGC`），失败后仅自动尝试一次 xk Kingo 独立登录
- IDS 模式可跨服务复用；Kingo 降级模式只保证课表、选课状态、空教室等 xk 能力，不生成或覆盖 CAS Jar
- 遇到 Kingo 验证码立即返回 `captcha_required`，禁止识别、绕过或循环重试；根据返回的 `auth.mode`、`degraded`、`error_code`、`warning` 向用户说明能力边界
