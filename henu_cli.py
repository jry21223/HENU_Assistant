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
    library_auto_signin,
    library_cancel,
    library_query,
    library_reserve,
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

    library_query_parser = subparsers.add_parser("library_query", help="统一查询图书馆信息")
    library_query_parser.add_argument("--view", default="current", choices=["locations", "current", "records"], help="查询视图")
    library_query_parser.add_argument("--record_type", default="1", help="记录类型")
    library_query_parser.add_argument("--page", type=int, default=1, help="页码")
    library_query_parser.add_argument("--limit", type=int, default=20, help="每页数量")

    library_reserve_parser = subparsers.add_parser("library_reserve", help="预约图书馆座位")
    library_reserve_parser.add_argument("--location", default="", help="区域名")
    library_reserve_parser.add_argument("--seat_no", default="", help="座位号")
    library_reserve_parser.add_argument("--target_date", default="", help="日期 YYYY-MM-DD")
    library_reserve_parser.add_argument("--preferred_time", default="08:00", help="首选时间 HH:MM")

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
        elif args.command == "library_query":
            result = library_query(
                view=args.view,
                record_type=args.record_type,
                page=args.page,
                limit=args.limit,
            )
        elif args.command == "library_reserve":
            result = library_reserve(
                location=args.location,
                seat_no=args.seat_no,
                target_date=args.target_date,
                preferred_time=args.preferred_time,
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
