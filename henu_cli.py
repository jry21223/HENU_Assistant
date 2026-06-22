#!/usr/bin/env python3
"""
HENU Campus Assistant CLI for OpenClaw.
河南大学校园助手命令行接口。
"""

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from henu_campus_mcp import (  # noqa: E402
    course_monitor_config,
    course_monitor_notify_test,
    course_monitor_once,
    course_monitor_run,
    course_selection_plan,
    course_selection_query,
    course_selection_submit,
    empty_classroom_query,
    empty_classroom_sync,
    library_auto_signin,
    library_cancel,
    library_query,
    library_reserve,
    resource_registry_query,
    resource_registry_sync,
    schedule_query,
    seminar_cancel,
    seminar_group,
    seminar_query,
    seminar_reserve,
    seminar_signin,
    set_calibration_source,
    setup_account,
    sync_schedule,
    system_status,
    yunfz_activity_query,
    yunfz_checksleep_query,
    yunfz_collection_query,
    yunfz_leave_query,
    yunfz_signin_query,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="河南大学校园助手")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    setup_parser = subparsers.add_parser("setup_account", help="设置账号")
    setup_parser.add_argument("--student_id", required=True, help="学号")
    setup_parser.add_argument("--password", required=True, help="密码")
    setup_parser.add_argument("--library_location", default="", help="默认图书馆区域")
    setup_parser.add_argument("--library_seat_no", default="", help="默认座位号")
    setup_parser.add_argument("--no_verify_login", action="store_true", help="仅保存账号，不立即验证登录")
    setup_parser.add_argument(
        "--no_calibrate_period_time",
        action="store_true",
        help="初始化时不自动校准节次时间",
    )

    sync_parser = subparsers.add_parser("sync_schedule", help="同步课表")
    sync_parser.add_argument("--xn", default=None, help="学年")
    sync_parser.add_argument("--xq", default=None, help="学期")
    sync_parser.add_argument("--no_auto_calibrate", action="store_true", help="同步前不执行自动节次校准")

    schedule_parser = subparsers.add_parser("schedule_query", help="统一查询课表")
    schedule_parser.add_argument("--view", default="current", choices=["current", "day", "week", "full"], help="查询视图")
    schedule_parser.add_argument("--timezone", default="Asia/Shanghai", help="时区")
    schedule_parser.add_argument("--target_date", default="", help="日期 YYYY-MM-DD，仅 view=day 时使用")
    schedule_parser.add_argument("--no_auto_calibrate", action="store_true", help="查询当前课程前不执行自动节次校准")

    smart_course_parser = subparsers.add_parser("smart_course_selection", aliases=["smart_course_select"], help="从 Excel/JSON 课表智能规划选课")
    smart_course_parser.add_argument("--source_path", "--source", default="", help="Excel 或清洗后 JSON 文件路径")
    smart_course_parser.add_argument("--excel_path", "--excel", default="", help="教务导出的 Excel 文件路径")
    smart_course_parser.add_argument("--json_path", "--json", default="", help="清洗后的 JSON 文件路径")
    smart_course_parser.add_argument("--user_class", "--class", default="", help="班级，例如 25软工1")
    smart_course_parser.add_argument("--sheet_name", "--sheet", default="2026-2027-1学期", help="Excel 工作表名")
    smart_course_parser.add_argument("--semester", default="", help="学期标识，例如 2026-2027-1")
    smart_course_parser.add_argument("--mode", choices=["schema", "filter", "plan"], default="plan", help="输出模式")
    smart_course_parser.add_argument("--like_early8", action="store_true", help="偏好早八")
    smart_course_parser.add_argument("--avoid_early8", action="store_true", help="避免早八")
    smart_course_parser.add_argument("--compact_days", action="store_true", help="尽量集中上课天数")
    smart_course_parser.add_argument("--target_days", type=int, default=3, help="集中排课目标天数")
    smart_course_parser.add_argument("--avoid_evening", action="store_true", help="避免晚课")
    smart_course_parser.add_argument("--no_unscheduled", action="store_true", help="排除未排时间课程")
    smart_course_parser.add_argument("--no_common", action="store_true", help="排除全年级公共课")
    smart_course_parser.add_argument("--include_options", action="store_true", help="plan 模式也返回候选课程选项")
    smart_course_parser.add_argument("--top_k", type=int, default=3, help="返回推荐方案数量")
    smart_course_parser.add_argument("--max_combinations", type=int, default=200000, help="最大组合搜索数")

    course_query_parser = subparsers.add_parser("course_selection_query", help="查询选课状态（只读）")
    course_query_parser.add_argument("--view", default="status", choices=["status"], help="查询视图")
    course_query_parser.add_argument("--xktype", default="2", help="选课类型")

    course_plan_parser = subparsers.add_parser("course_selection_plan", help="生成选课计划（本地规划）")
    course_plan_parser.add_argument("--candidates_json", required=True, help="候选课程 JSON")
    course_plan_parser.add_argument("--existing_schedule_json", default="", help="已有课表 JSON")
    course_plan_parser.add_argument("--preferences_json", default="", help="偏好 JSON")
    course_plan_parser.add_argument("--top_k", type=int, default=10, help="返回计划数量")

    course_submit_parser = subparsers.add_parser("course_selection_submit", help="选课提交占位（不执行真实提交）")
    course_submit_parser.add_argument("--payload_json", default="", help="预留参数，当前不使用")

    course_monitor_config_parser = subparsers.add_parser("course_monitor_config", help="查看或保存选课余量监控配置")
    course_monitor_config_parser.add_argument("--config_json", default="", help="监控配置 JSON；留空表示查看")
    course_monitor_config_parser.add_argument("--replace", action="store_true", help="替换而不是合并现有配置")

    course_monitor_once_parser = subparsers.add_parser("course_monitor_once", help="执行一次选课余量监控（只读）")
    course_monitor_once_parser.add_argument("--config_json", default="", help="临时覆盖配置 JSON")
    course_monitor_once_parser.add_argument("--no_notify", action="store_true", help="不发送通知")

    course_monitor_run_parser = subparsers.add_parser("course_monitor_run", help="按间隔运行选课余量监控（只提醒不提交）")
    course_monitor_run_parser.add_argument("--config_json", default="", help="临时覆盖配置 JSON")
    course_monitor_run_parser.add_argument("--max_checks", type=int, default=1, help="最大检查次数，默认 1")
    course_monitor_run_parser.add_argument("--duration_seconds", type=int, default=0, help="最长运行秒数")
    course_monitor_run_parser.add_argument("--no_notify", action="store_true", help="不发送通知")

    course_monitor_notify_parser = subparsers.add_parser("course_monitor_notify_test", help="测试选课余量飞书通知")
    course_monitor_notify_parser.add_argument("--config_json", default="", help="临时覆盖配置 JSON")

    library_query_parser = subparsers.add_parser("library_query", help="统一查询图书馆信息")
    library_query_parser.add_argument("--view", default="current", choices=["locations", "seats", "current", "records"], help="查询视图")
    library_query_parser.add_argument("--record_type", default="1", help="记录类型")
    library_query_parser.add_argument("--page", type=int, default=1, help="页码")
    library_query_parser.add_argument("--limit", type=int, default=20, help="每页数量")
    library_query_parser.add_argument("--target_date", "--date", default="", help="日期 YYYY-MM-DD，view=locations/seats 时使用")
    library_query_parser.add_argument("--location", default="", help="区域名，view=seats 时使用")
    library_query_parser.add_argument("--area_id", default="", help="区域 ID，view=seats 时优先使用")
    library_query_parser.add_argument("--preferred_time", default="08:00", help="首选时间 HH:MM，view=seats 时使用")
    library_query_parser.add_argument("--preferred_end_time", default="", help="最晚结束时间 HH:MM，view=seats 时使用")

    library_reserve_parser = subparsers.add_parser("library_reserve", help="预约图书馆座位")
    library_reserve_parser.add_argument("--location", default="", help="区域名")
    library_reserve_parser.add_argument("--seat_no", default="", help="座位号")
    library_reserve_parser.add_argument("--target_date", default="", help="日期 YYYY-MM-DD")
    library_reserve_parser.add_argument("--preferred_time", default="08:00", help="首选时间 HH:MM")
    library_reserve_parser.add_argument("--preferred_end_time", default="", help="最晚结束时间 HH:MM，用于限制预约时间窗口")
    library_reserve_parser.add_argument("--retry_until", default="", help="自动抢约截止时间 HH:MM 或 ISO 日期时间")
    library_reserve_parser.add_argument("--retry_interval_seconds", type=int, default=2, help="抢约重试间隔秒数")
    library_reserve_parser.add_argument("--max_attempts", type=int, default=1, help="最大尝试次数")

    library_signin_parser = subparsers.add_parser("library_auto_signin", help="图书馆自动签到")
    library_signin_parser.add_argument("--record_id", default="", help="指定当前预约记录 ID")

    library_cancel_parser = subparsers.add_parser("library_cancel", help="取消图书馆预约")
    library_cancel_parser.add_argument("--record_id", required=True, help="记录 ID")
    library_cancel_parser.add_argument("--record_type", default="auto", help="记录类型")

    seminar_group_parser = subparsers.add_parser("seminar_group", help="统一管理研讨室 group")
    seminar_group_parser.add_argument("--action", default="list", choices=["list", "save", "delete"], help="动作")
    seminar_group_parser.add_argument("--group_name", default="", help="group 名称")
    seminar_group_parser.add_argument("--member_ids", default="", help="同行成员学号，逗号/空格/换行分隔")
    seminar_group_parser.add_argument("--note", default="", help="备注")

    seminar_query_parser = subparsers.add_parser("seminar_query", help="统一查询研讨室信息")
    seminar_query_parser.add_argument(
        "--view",
        default="rooms",
        choices=["filters", "rooms", "detail", "records", "signin_tasks"],
        help="查询视图",
    )
    seminar_query_parser.add_argument("--target_date", default="", help="日期 YYYY-MM-DD")
    seminar_query_parser.add_argument("--members", type=int, default=0, help="人数，0 表示不筛选")
    seminar_query_parser.add_argument("--name", default="", help="房间名称关键词")
    seminar_query_parser.add_argument("--room", default="", help="房型/房间筛选值")
    seminar_query_parser.add_argument("--start_time", default="", help="开始时间 HH:MM")
    seminar_query_parser.add_argument("--end_time", default="", help="结束时间 HH:MM")
    seminar_query_parser.add_argument("--library_ids", default="", help="馆舍 ID 列表")
    seminar_query_parser.add_argument("--library_names", default="", help="馆舍名称列表")
    seminar_query_parser.add_argument("--floor_ids", default="", help="楼层 ID 列表")
    seminar_query_parser.add_argument("--floor_names", default="", help="楼层名称列表")
    seminar_query_parser.add_argument("--category_ids", default="", help="分类 ID 列表")
    seminar_query_parser.add_argument("--category_names", default="", help="分类名称列表")
    seminar_query_parser.add_argument("--boutique_ids", default="", help="特色标签 ID 列表")
    seminar_query_parser.add_argument("--boutique_names", default="", help="特色标签名称列表")
    seminar_query_parser.add_argument("--page", type=int, default=1, help="页码")
    seminar_query_parser.add_argument("--area_id", default="", help="房间 area_id")
    seminar_query_parser.add_argument("--record_type", default="1", help="记录类型，1=普通空间 2=大型空间")
    seminar_query_parser.add_argument("--limit", type=int, default=20, help="每页数量")
    seminar_query_parser.add_argument("--mode", default="books", help="记录模式，books=预约记录 reneges=违约/取消记录")
    seminar_query_parser.add_argument("--status", default="", help="签到任务状态过滤")

    seminar_signin_parser = subparsers.add_parser("seminar_signin", help="研讨室签到或自动补扫")
    seminar_signin_parser.add_argument("--record_id", default="", help="研讨室预约记录 ID")
    seminar_signin_parser.add_argument("--auto_scan", action="store_true", help="扫描所有已到点任务并自动签到")

    seminar_reserve_parser = subparsers.add_parser("seminar_reserve", help="预约研讨室")
    seminar_reserve_parser.add_argument("--area_id", required=True, help="房间 area_id")
    seminar_reserve_parser.add_argument("--target_date", default="", help="开始日期 YYYY-MM-DD")
    seminar_reserve_parser.add_argument("--start_time", default="", help="开始时间 HH:MM")
    seminar_reserve_parser.add_argument("--end_time", default="", help="结束时间 HH:MM")
    seminar_reserve_parser.add_argument("--end_date", default="", help="结束日期 YYYY-MM-DD")
    seminar_reserve_parser.add_argument("--title", default="", help="申请主题")
    seminar_reserve_parser.add_argument("--title_id", default="", help="预设主题 ID")
    seminar_reserve_parser.add_argument("--content", required=True, help="申请内容，必须大于 10 字")
    seminar_reserve_parser.add_argument("--mobile", default="", help="联系电话")
    seminar_reserve_parser.add_argument("--group_name", default="", help="已保存的 group 名称")
    seminar_reserve_parser.add_argument("--member_ids", default="", help="直接传同行成员学号列表，不含自己")
    seminar_reserve_parser.add_argument("--is_open", type=int, default=0, help="是否公开，0=是 1=否")
    seminar_reserve_parser.add_argument("--cate_id", default="", help="半天/全天分类 ID")
    seminar_reserve_parser.add_argument("--time_ranges_json", default="", help="多时间段 JSON 数组")

    seminar_cancel_parser = subparsers.add_parser("seminar_cancel", help="取消研讨室预约")
    seminar_cancel_parser.add_argument("--record_id", required=True, help="研讨室预约记录 ID")

    calibration_parser = subparsers.add_parser("set_calibration_source", help="设置喜鹊节次校准请求参数")
    calibration_parser.add_argument("--data", required=True, help="抓包 data 参数")
    calibration_parser.add_argument("--cookie", required=True, help="抓包 cookie")
    calibration_parser.add_argument(
        "--user_agent",
        default="KingoPalm/2.6.449 (iPhone; iOS 26.3; Scale/3.00)",
        help="请求 User-Agent",
    )

    system_parser = subparsers.add_parser("system_status", help="查看系统状态")
    system_parser.add_argument("--timezone", default="Asia/Shanghai", help="时区")

    # 空教室查询
    empty_classroom_parser = subparsers.add_parser("empty_classroom_query", help="查询空教室/教室信息")
    empty_classroom_parser.add_argument("--view", default="free", help="free/day_matrix/occupancy/terms/campuses/buildings/classrooms/types")
    empty_classroom_parser.add_argument("--term_code", default="", help="学期代码")
    empty_classroom_parser.add_argument("--week", type=int, default=0, help="教学周")
    empty_classroom_parser.add_argument("--day_of_week", type=int, default=0, help="星期 1-7")
    empty_classroom_parser.add_argument("--period", type=int, default=0, help="大节 1-5")
    empty_classroom_parser.add_argument("--campus_code", default="", help="校区代码")
    empty_classroom_parser.add_argument("--building_code", default="", help="楼房代码")
    empty_classroom_parser.add_argument("--campus_text", default="", help="校区自然语言（如 明伦）")
    empty_classroom_parser.add_argument("--building_text", default="", help="楼房自然语言（如 十号楼）")
    empty_classroom_parser.add_argument("--freshness", default="cache_first", help="缓存策略")
    empty_classroom_parser.add_argument("--force_refresh", action="store_true", help="强制刷新")

    empty_classroom_sync_parser = subparsers.add_parser("empty_classroom_sync", help="同步教室课表缓存")
    empty_classroom_sync_parser.add_argument("--term_code", required=True, help="学期代码")
    empty_classroom_sync_parser.add_argument("--campus_code", required=True, help="校区代码")
    empty_classroom_sync_parser.add_argument("--building_code", required=True, help="楼房代码")
    empty_classroom_sync_parser.add_argument("--force_refresh", action="store_true", help="强制刷新")

    # 资源编号映射
    resource_query_parser = subparsers.add_parser("resource_registry_query", help="查询全局资源编号映射")
    resource_query_parser.add_argument("--view", default="search", help="search/resolve/list/stats")
    resource_query_parser.add_argument("--query", default="", help="搜索关键词")
    resource_query_parser.add_argument("--resource_type", default="", help="资源类型")
    resource_query_parser.add_argument("--campus_code", default="", help="校区代码")
    resource_query_parser.add_argument("--limit", type=int, default=20, help="返回数量")

    resource_sync_parser = subparsers.add_parser("resource_registry_sync", help="同步资源到全局编号映射")
    resource_sync_parser.add_argument("--scope", default="all", help="classrooms/library/seminar/all")
    resource_sync_parser.add_argument("--force_refresh", action="store_true", help="强制刷新")

    yunfz_leave_parser = subparsers.add_parser("yunfz_leave_query", help="查询河宝社区请假信息")
    yunfz_leave_parser.add_argument("--view", default="list", choices=["list", "detail", "statistics"], help="查询视图")
    yunfz_leave_parser.add_argument("--leave_id", default="", help="请假记录 ID，仅 view=detail 时使用")
    yunfz_leave_parser.add_argument("--page", type=int, default=1, help="页码")
    yunfz_leave_parser.add_argument("--page_size", type=int, default=20, help="每页数量")

    yunfz_signin_parser = subparsers.add_parser("yunfz_signin_query", help="查询河宝社区签到任务")
    yunfz_signin_parser.add_argument("--view", default="list", choices=["list", "statistics"], help="查询视图")
    yunfz_signin_parser.add_argument("--page", type=int, default=1, help="页码")
    yunfz_signin_parser.add_argument("--page_size", type=int, default=20, help="每页数量")

    yunfz_checksleep_parser = subparsers.add_parser("yunfz_checksleep_query", help="查询河宝社区查寝任务")
    yunfz_checksleep_parser.add_argument("--view", default="list", choices=["list", "statistics"], help="查询视图")
    yunfz_checksleep_parser.add_argument("--page", type=int, default=1, help="页码")
    yunfz_checksleep_parser.add_argument("--page_size", type=int, default=20, help="每页数量")

    yunfz_activity_parser = subparsers.add_parser("yunfz_activity_query", help="查询河宝社区活动信息")
    yunfz_activity_parser.add_argument("--view", default="list", choices=["list", "statistics"], help="查询视图")
    yunfz_activity_parser.add_argument("--page", type=int, default=1, help="页码")
    yunfz_activity_parser.add_argument("--page_size", type=int, default=20, help="每页数量")

    yunfz_collection_parser = subparsers.add_parser("yunfz_collection_query", help="查询河宝社区信息收集任务")
    yunfz_collection_parser.add_argument("--view", default="list", choices=["list", "statistics"], help="查询视图")
    yunfz_collection_parser.add_argument("--page", type=int, default=1, help="页码")
    yunfz_collection_parser.add_argument("--page_size", type=int, default=20, help="每页数量")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == "setup_account":
            result = setup_account(
                student_id=args.student_id,
                password=args.password,
                library_location=args.library_location,
                library_seat_no=args.library_seat_no,
                verify_login=not args.no_verify_login,
                calibrate_period_time=not args.no_calibrate_period_time,
            )
        elif args.command == "sync_schedule":
            result = sync_schedule(
                xn=args.xn,
                xq=args.xq,
                auto_calibrate=not args.no_auto_calibrate,
            )
        elif args.command == "schedule_query":
            result = schedule_query(
                view=args.view,
                timezone=args.timezone,
                target_date=args.target_date,
                auto_calibrate=not args.no_auto_calibrate,
            )
        elif args.command in {"smart_course_selection", "smart_course_select"}:
            from mcp_server import smart_course_selection

            result = smart_course_selection(
                source_path=args.source_path,
                excel_path=args.excel_path,
                json_path=args.json_path,
                user_class=args.user_class,
                sheet_name=args.sheet_name,
                semester=args.semester,
                mode=args.mode,
                like_early8=args.like_early8,
                avoid_early8=args.avoid_early8,
                compact_days=args.compact_days,
                target_days=args.target_days,
                avoid_evening=args.avoid_evening,
                allow_unscheduled=not args.no_unscheduled,
                include_common=not args.no_common,
                include_course_options=args.include_options,
                top_k=args.top_k,
                max_combinations=args.max_combinations,
            )

        elif args.command == "course_selection_query":
            result = course_selection_query(view=args.view, xktype=args.xktype)
        elif args.command == "course_selection_plan":
            result = course_selection_plan(
                candidates_json=args.candidates_json,
                existing_schedule_json=args.existing_schedule_json,
                preferences_json=args.preferences_json,
                top_k=args.top_k,
            )
        elif args.command == "course_selection_submit":
            result = course_selection_submit(payload_json=args.payload_json)
        elif args.command == "course_monitor_config":
            result = course_monitor_config(config_json=args.config_json, merge=not args.replace)
        elif args.command == "course_monitor_once":
            result = course_monitor_once(config_json=args.config_json, send_notifications=not args.no_notify)
        elif args.command == "course_monitor_run":
            result = course_monitor_run(
                config_json=args.config_json,
                max_checks=args.max_checks,
                duration_seconds=args.duration_seconds,
                send_notifications=not args.no_notify,
            )
        elif args.command == "course_monitor_notify_test":
            result = course_monitor_notify_test(config_json=args.config_json)
        elif args.command == "library_query":
            result = library_query(
                view=args.view,
                record_type=args.record_type,
                page=args.page,
                limit=args.limit,
                target_date=args.target_date,
                location=args.location,
                area_id=args.area_id,
                preferred_time=args.preferred_time,
                preferred_end_time=args.preferred_end_time,
            )
        elif args.command == "library_reserve":
            result = library_reserve(
                location=args.location,
                seat_no=args.seat_no,
                target_date=args.target_date,
                preferred_time=args.preferred_time,
                preferred_end_time=args.preferred_end_time,
                retry_until=args.retry_until,
                retry_interval_seconds=args.retry_interval_seconds,
                max_attempts=args.max_attempts,
            )
        elif args.command == "library_auto_signin":
            result = library_auto_signin(record_id=args.record_id)
        elif args.command == "library_cancel":
            result = library_cancel(record_id=args.record_id, record_type=args.record_type)
        elif args.command == "seminar_group":
            result = seminar_group(
                action=args.action,
                group_name=args.group_name,
                member_ids=args.member_ids,
                note=args.note,
            )
        elif args.command == "seminar_query":
            result = seminar_query(
                view=args.view,
                target_date=args.target_date,
                members=args.members,
                name=args.name,
                room=args.room,
                start_time=args.start_time,
                end_time=args.end_time,
                library_ids=args.library_ids,
                library_names=args.library_names,
                floor_ids=args.floor_ids,
                floor_names=args.floor_names,
                category_ids=args.category_ids,
                category_names=args.category_names,
                boutique_ids=args.boutique_ids,
                boutique_names=args.boutique_names,
                page=args.page,
                area_id=args.area_id,
                record_type=args.record_type,
                limit=args.limit,
                mode=args.mode,
                status=args.status,
            )
        elif args.command == "seminar_signin":
            result = seminar_signin(record_id=args.record_id, auto_scan=args.auto_scan)
        elif args.command == "seminar_reserve":
            result = seminar_reserve(
                area_id=args.area_id,
                target_date=args.target_date,
                start_time=args.start_time,
                end_time=args.end_time,
                end_date=args.end_date,
                title=args.title,
                title_id=args.title_id,
                content=args.content,
                mobile=args.mobile,
                group_name=args.group_name,
                member_ids=args.member_ids,
                is_open=args.is_open,
                cate_id=args.cate_id,
                time_ranges_json=args.time_ranges_json,
            )
        elif args.command == "seminar_cancel":
            result = seminar_cancel(record_id=args.record_id)
        elif args.command == "set_calibration_source":
            result = set_calibration_source(
                data=args.data,
                cookie=args.cookie,
                user_agent=args.user_agent,
            )
        elif args.command == "system_status":
            result = system_status(timezone=args.timezone)
        elif args.command == "empty_classroom_query":
            result = empty_classroom_query(
                view=args.view,
                term_code=args.term_code,
                week=args.week,
                day_of_week=args.day_of_week,
                period=args.period,
                campus_code=args.campus_code,
                building_code=args.building_code,
                campus_text=args.campus_text,
                building_text=args.building_text,
                freshness=args.freshness,
                force_refresh=args.force_refresh,
            )
        elif args.command == "empty_classroom_sync":
            result = empty_classroom_sync(
                term_code=args.term_code,
                campus_code=args.campus_code,
                building_code=args.building_code,
                force_refresh=args.force_refresh,
            )
        elif args.command == "resource_registry_query":
            result = resource_registry_query(
                view=args.view,
                query=args.query,
                resource_type=args.resource_type,
                campus_code=args.campus_code,
                limit=args.limit,
            )
        elif args.command == "resource_registry_sync":
            result = resource_registry_sync(
                scope=args.scope,
                force_refresh=args.force_refresh,
            )
        elif args.command == "yunfz_leave_query":
            result = yunfz_leave_query(
                view=args.view,
                leave_id=args.leave_id,
                page=args.page,
                page_size=args.page_size,
            )
        elif args.command == "yunfz_signin_query":
            result = yunfz_signin_query(
                view=args.view,
                page=args.page,
                page_size=args.page_size,
            )
        elif args.command == "yunfz_checksleep_query":
            result = yunfz_checksleep_query(
                view=args.view,
                page=args.page,
                page_size=args.page_size,
            )
        elif args.command == "yunfz_activity_query":
            result = yunfz_activity_query(
                view=args.view,
                page=args.page,
                page_size=args.page_size,
            )
        elif args.command == "yunfz_collection_query":
            result = yunfz_collection_query(
                view=args.view,
                page=args.page,
                page_size=args.page_size,
            )
        else:
            print(f"未知命令: {args.command}")
            return

        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(
            json.dumps(
                {"success": False, "msg": f"执行失败: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
