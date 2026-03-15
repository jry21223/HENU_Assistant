# Skill 对齐摘要

## 目标

让 `openclaw-skill` 与 `mcp-server` 在能力和命名上保持一致，但以 CLI 方式供 OpenClaw 调用。

## 已完成

- Skill CLI 收敛为 14 个统一入口
- 课表命令合并为 `schedule_query`
- 图书馆查询命令合并为 `library_query`
- 研讨室 group 管理命令合并为 `seminar_group`
- 研讨室查询命令合并为 `seminar_query`
- 研讨室手动签到与自动补扫合并为 `seminar_signin`
- Skill 层仍保持薄封装：`scripts/henu_campus_mcp.py`

## 当前 CLI 入口

- `setup_account`
- `sync_schedule`
- `schedule_query`
- `library_query`
- `library_reserve`
- `library_auto_signin`
- `library_cancel`
- `seminar_group`
- `seminar_query`
- `seminar_signin`
- `seminar_reserve`
- `seminar_cancel`
- `set_calibration_source`
- `system_status`

## 兼容性说明

- 旧命令名如 `current_course`、`latest_schedule_current_week`、`library_locations`、`seminar_rooms`、`seminar_auto_signin` 已不再作为独立 CLI 入口暴露
- 调用方需要改为使用统一入口配合 `view`、`action`、`auto_scan` 等参数
