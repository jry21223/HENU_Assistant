---
name: henu_campus_assistant
description: 河南大学校园助手，支持课表查询、图书馆预约、研讨室预约（与 mcp-server 能力对齐）
---

# 河大校园助手

面向 OpenClaw 的本地 Skill，使用 CLI 调用内置核心能力。

## 功能

- 课表：`setup_account`、`sync_schedule`、`schedule_query`
- 选课：`smart_course_selection`、`course_selection_query`、`course_selection_plan`、`course_selection_submit`
- 图书馆：`library_query`、`library_reserve`、`library_auto_signin`、`library_cancel`
- 研讨室：`seminar_group`、`seminar_query`、`seminar_signin`、`seminar_reserve`、`seminar_cancel`
- 系统：`set_calibration_source`、`system_status`
- 河宝社区：`yunfz_leave_query`、`yunfz_signin_query`、`yunfz_checksleep_query`、`yunfz_activity_query`、`yunfz_collection_query`

## 执行方式

当用户询问课表/课程/图书馆/研讨室相关需求时，使用 `bash` 执行：

```bash
cd ~/.openclaw/workspace/skills/henu_campus_assistant && python3 henu_cli.py <command> [args]
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
- 涉及“现在/今天/明天/当前预约/待签到”等相对时间时，先执行 `system_status`
- 课表里 `current` 只查“当前正在上的课 + 下一节课”；查某一天课程请用 `schedule_query --view day --target_date "YYYY-MM-DD"`
- `course_selection_query` 只读查询 xk 选课状态，会走统一认证与 frame 菜单入口；`course_selection_submit` 当前不执行真实提交
- 图书馆预约前先用 `library_query --view locations` 确认区域，可用 `library_query --view seats` 查看当前可用座位
- 图书馆查看当前预约或历史记录时，用 `library_query --view current` / `library_query --view records`
- 研讨室通常先按 `seminar_query --view filters` -> `seminar_query --view rooms` -> `seminar_query --view detail` 逐步查询
- 研讨室 `group` 保存的是同行成员学号，不含自己；建议保存 3-9 个学号，预约时会自动去重并排除当前账号
- 研讨室预约会按照房间限制校验总人数，通常为 4-5 人起、最多 10 人
- 研讨室申请说明必须多于 10 个字
- 研讨室可先用 `seminar_query --view records` 查记录，再用 `seminar_cancel` 取消
- 河宝社区相关查询前先执行 `system_status`
- 请假详情使用 `yunfz_leave_query --view detail --leave_id "..."`
- 账号与 Cookie 仅本地保存
