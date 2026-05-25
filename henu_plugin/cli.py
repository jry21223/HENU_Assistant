from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class CliCommandSpec:
    raw: str
    argv: tuple[str, ...]
    resolved_tool: str | None
    params: dict[str, Any]
    action: str
    should_preload_runtime_context: bool
    is_help: bool = False
    help_topic: str = ""
    error: str = ""


def inspect_cli_command(command: Any) -> CliCommandSpec:
    raw = str(command or "").strip()
    if not raw:
        return _error_spec(
            raw,
            "请提供 command，例如 `help`、`account status`、`schedule now`。",
            help_topic="",
        )

    try:
        argv = tuple(shlex.split(raw))
    except ValueError as exc:
        return _error_spec(raw, f"命令解析失败: {exc}", help_topic="")

    if not argv:
        return _error_spec(raw, "命令不能为空。", help_topic="")

    head = argv[0].lower()
    if head in {"help", "-h", "--help"}:
        topic = _normalize_topic(argv[1:])
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool=None,
            params={},
            action="help",
            should_preload_runtime_context=False,
            is_help=True,
            help_topic=topic,
        )

    if head in {"status", "whoami"}:
        return _system_status_spec(raw, argv, action=head)

    if head == "system":
        return _parse_system(raw, argv)
    if head == "account":
        return _parse_account(raw, argv)
    if head == "schedule":
        return _parse_schedule(raw, argv)
    if head == "library":
        return _parse_library(raw, argv)
    if head == "seminar":
        return _parse_seminar(raw, argv)
    if head in {"calibration", "calibrate"}:
        return _parse_calibration(raw, argv)
    if head in {"yunfz", "hebao"}:
        return _parse_yunfz(raw, argv)

    return _error_spec(
        raw,
        f"未知命令 `{argv[0]}`。先执行 `help` 查看一级命令。",
        help_topic="",
    )


def build_help_payload(topic: str) -> dict[str, Any]:
    normalized = _normalize_topic(topic.split())

    if not normalized:
        return {
            "topic": "root",
            "summary": "HENU CLI 采用渐进式披露。先看一级命令，再进入具体主题。",
            "commands": [
                "account status",
                "account set --student-id ... --password ...",
                "schedule now",
                "schedule day --date 2026-03-30",
                "library current",
                "seminar rooms --date 2026-03-30 --start 14:00 --end 16:00 --members 4",
                "yunfz leave list",
                "yunfz signin list",
                "status",
                "help <topic>",
            ],
            "examples": [
                "help account",
                "help schedule",
                "help library",
                "help seminar",
                "help yunfz",
            ],
            "tips": [
                "写操作只在 `success=true` 时才算完成。",
                "参数值包含空格时要加引号。",
                "不确定参数时，不要一次展开全部，先进入更窄的 help 主题。",
            ],
        }

    if normalized == "account":
        return {
            "topic": normalized,
            "summary": "账号相关命令。",
            "commands": [
                "account status",
                "account set --student-id ... --password ... [--library-location ...] [--library-seat-no ...]",
            ],
            "examples": [
                "account status",
                "account set --student-id 20230001 --password 'secret'",
            ],
            "tips": [
                "默认会验证登录并自动校准节次。",
                "若只想保存不验证，可追加 `--no-verify-login`。",
            ],
        }

    if normalized == "account set":
        return {
            "topic": normalized,
            "summary": "绑定当前 QQ 对应的河大账号。",
            "commands": [
                "account set --student-id <学号> --password <密码> [--library-location <区域>] [--library-seat-no <座位>]",
            ],
            "examples": [
                "account set --student-id 20230001 --password 'secret'",
                "account set --student-id 20230001 --password 'secret' --library-location 金明馆北区 --library-seat-no 201",
            ],
            "tips": [
                "默认 `verify_login=true`、`calibrate_period_time=true`。",
                "可用 `--no-verify-login`、`--no-calibrate-period-time` 关闭默认行为。",
            ],
        }

    if normalized == "schedule":
        return {
            "topic": normalized,
            "summary": "课表查询与同步。",
            "commands": [
                "schedule sync [--xn <学年>] [--xq <学期>]",
                "schedule now [--timezone Asia/Shanghai]",
                "schedule day --date YYYY-MM-DD",
                "schedule week",
                "schedule full",
            ],
            "examples": [
                "schedule now",
                "schedule day --date 2026-03-30",
                "schedule sync",
            ],
            "tips": [
                "`schedule now` 默认自动校准节次时间。",
                "需要精确某天时，用 `schedule day --date ...`。",
            ],
        }

    if normalized == "library":
        return {
            "topic": normalized,
            "summary": "图书馆查询、预约、签到、取消。",
            "commands": [
                "library locations [--date YYYY-MM-DD]",
                "library seats --area-id <ID> [--date YYYY-MM-DD] [--time HH:MM]",
                "library current",
                "library records [--record-type 1] [--page 1] [--limit 20]",
                "library reserve [--location <区域>] [--seat-no <座位>] [--date YYYY-MM-DD] [--time HH:MM]",
                "library signin [--record-id <ID>]",
                "library cancel --record-id <ID> [--record-type auto]",
            ],
            "examples": [
                "library locations --date 2026-03-30",
                "library seats --area-id 12 --date 2026-03-30 --time 06:30",
                "library current",
                "library reserve --location 金明馆北区 --seat-no 201 --date 2026-03-30",
            ],
            "tips": [
                "预约前先查真实区域和座位，不要凭旧名称或旧 area_id 预约。",
                "先用 `library current` 或 `library records` 看现状。",
                "取消与签到属于真实写操作，只认 `success=true`。",
            ],
        }

    if normalized == "seminar":
        return {
            "topic": normalized,
            "summary": "研讨室分组、查询、预约、签到、取消。",
            "commands": [
                "seminar groups list",
                "seminar groups save --group-name <名称> --member-ids '20230001,20230002'",
                "seminar filters [--date YYYY-MM-DD] [--members 4]",
                "seminar rooms [--date YYYY-MM-DD] [--start HH:MM] [--end HH:MM] [--members 4]",
                "seminar detail --area-id <ID> [--date YYYY-MM-DD]",
                "seminar records [--record-type 1] [--page 1] [--limit 20]",
                "seminar signin-tasks [--status pending]",
                "seminar reserve --area-id <ID> --date YYYY-MM-DD --start HH:MM --end HH:MM ...",
                "seminar signin [--record-id <ID>] [--auto-scan]",
                "seminar cancel --record-id <ID>",
            ],
            "examples": [
                "seminar rooms --date 2026-03-30 --start 14:00 --end 16:00 --members 4",
                "seminar reserve --area-id 12345 --date 2026-03-30 --start 14:00 --end 16:00 --title '组会' --content '课程讨论使用，已征得成员同意' --mobile 13800138000 --group-name 项目组",
            ],
            "tips": [
                "先查 filters/rooms/detail，再做 reserve。",
                "插件版不会启动后台自动签到线程，签到时要显式执行 `seminar signin`。",
            ],
        }

    if normalized == "seminar reserve":
        return {
            "topic": normalized,
            "summary": "预约研讨室。",
            "commands": [
                "seminar reserve --area-id <ID> --date YYYY-MM-DD --start HH:MM --end HH:MM [--end-date YYYY-MM-DD] [--title <标题>] [--title-id <主题ID>] --content <用途说明> [--mobile <手机号>] [--group-name <分组>] [--member-ids <成员学号列表>] [--is-open 0] [--cate-id <分类ID>] [--time-ranges-json <JSON>]",
            ],
            "examples": [
                "seminar reserve --area-id 12345 --date 2026-03-30 --start 14:00 --end 16:00 --title '课程讨论' --content '课程讨论使用，已征得成员同意' --mobile 13800138000 --group-name 项目组",
            ],
            "tips": [
                "`--content` 需要足够具体，避免过短。",
                "预约成功后若需要签到，再执行 `seminar signin --auto-scan` 或指定 `--record-id`。",
            ],
        }

    if normalized == "calibration":
        return {
            "topic": normalized,
            "summary": "节次校准相关命令。",
            "commands": [
                "calibration set --data <抓包data> --cookie <抓包cookie> [--user-agent <UA>]",
            ],
            "examples": [
                "calibration set --data 'i=...' --cookie 'JSESSIONID=...'",
            ],
            "tips": [
                "这是共享配置，通常只在节次校准失准时才需要。",
            ],
        }

    if normalized == "yunfz":
        return {
            "topic": normalized,
            "summary": "河宝社区（云发阵）相关命令：请假、签到、查寝、活动、信息收集。",
            "commands": [
                "yunfz leave list [--page 1] [--page-size 20]",
                "yunfz leave detail --leave-id <ID>",
                "yunfz leave statistics",
                "yunfz signin list [--page 1] [--page-size 20]",
                "yunfz signin statistics",
                "yunfz checksleep list [--page 1] [--page-size 20]",
                "yunfz checksleep statistics",
                "yunfz activity list [--page 1] [--page-size 20]",
                "yunfz activity statistics",
                "yunfz collection list [--page 1] [--page-size 20]",
                "yunfz collection statistics",
            ],
            "examples": [
                "yunfz leave list",
                "yunfz leave detail --leave-id 12345",
                "yunfz signin list",
                "yunfz checksleep list",
            ],
            "tips": [
                "河宝社区使用统一身份认证（ids.henu.edu.cn）登录。",
                "先调用 `status` 获取当前时间，再查询相对时间相关的记录。",
            ],
        }

    return {
        "topic": normalized,
        "summary": f"未找到 `help {normalized}` 的专用说明，可先退回上一级主题。",
        "commands": ["help", "help account", "help schedule", "help library", "help seminar"],
        "examples": ["help seminar", "help account set"],
        "tips": ["按“一级主题 -> 二级主题 -> 精确命令”的顺序查看帮助。"],
    }


def build_next_commands(spec: CliCommandSpec, result: dict[str, Any] | None = None) -> list[str]:
    if spec.is_help:
        topic = spec.help_topic
        if not topic:
            return ["help account", "help schedule", "help library", "help seminar", "help yunfz"]
        if topic == "account":
            return ["account status", "help account set"]
        if topic == "schedule":
            return ["schedule now", "schedule sync"]
        if topic == "library":
            return ["library current", "library locations"]
        if topic == "seminar":
            return ["seminar filters", "seminar rooms --date 2026-03-30 --start 14:00 --end 16:00 --members 4"]
        if topic == "yunfz":
            return ["yunfz leave list", "yunfz signin list", "yunfz checksleep list"]
        return ["help"]

    resolved_tool = spec.resolved_tool or ""
    success = bool(result.get("success")) if isinstance(result, dict) else False

    if resolved_tool == "setup_account":
        return ["account status", "schedule sync", "schedule now"]
    if resolved_tool == "sync_schedule":
        return ["schedule now", "schedule week"]
    if resolved_tool == "schedule_query":
        return ["schedule day --date 2026-03-30", "schedule week"]
    if resolved_tool == "library_query":
        if spec.params.get("view") == "locations":
            return ["library seats --area-id <ID> --date YYYY-MM-DD --time HH:MM", "library reserve --location <区域> --seat-no <座位> --date YYYY-MM-DD"]
        if spec.params.get("view") == "seats":
            return ["library reserve --location <区域或area_id> --seat-no <座位> --date YYYY-MM-DD --time HH:MM"]
        return ["library current", "library records"]
    if resolved_tool == "library_reserve":
        return ["library current", "library signin"] if success else ["library locations", "library current"]
    if resolved_tool == "library_auto_signin":
        return ["library current", "library records"]
    if resolved_tool == "library_cancel":
        return ["library current", "library records"]
    if resolved_tool == "seminar_group":
        return ["seminar groups list", "seminar rooms --date 2026-03-30 --start 14:00 --end 16:00 --members 4"]
    if resolved_tool == "seminar_query":
        view = str(spec.params.get("view") or "")
        if view in {"filters", "rooms"}:
            return ["seminar detail --area-id <ID> --date YYYY-MM-DD", "seminar reserve --area-id <ID> --date YYYY-MM-DD --start HH:MM --end HH:MM --title '<标题>' --content '<用途说明>'"]
        if view == "detail":
            return ["seminar reserve --area-id <ID> --date YYYY-MM-DD --start HH:MM --end HH:MM --title '<标题>' --content '<用途说明>'"]
        if view == "records":
            return ["seminar signin-tasks", "seminar signin --auto-scan"]
        return ["seminar rooms", "seminar records"]
    if resolved_tool == "seminar_reserve":
        return ["seminar signin-tasks", "seminar signin --auto-scan"]
    if resolved_tool == "seminar_signin":
        return ["seminar signin-tasks", "seminar records"]
    if resolved_tool == "seminar_cancel":
        return ["seminar records", "seminar signin-tasks"]
    if resolved_tool == "system_status":
        return ["account status", "schedule now", "library current"]
    if resolved_tool == "set_calibration_source":
        return ["schedule now", "schedule sync"]
    if resolved_tool == "yunfz_leave_query":
        view = str(spec.params.get("view") or "list")
        if view == "detail":
            return ["yunfz leave list", "yunfz leave statistics"]
        return ["yunfz leave detail --leave-id <ID>", "yunfz leave statistics"]
    if resolved_tool in {"yunfz_signin_query", "yunfz_checksleep_query", "yunfz_activity_query", "yunfz_collection_query"}:
        return ["yunfz leave list", "yunfz signin list", "yunfz checksleep list", "yunfz activity list"]
    return ["help"]


def _parse_system(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    if len(argv) == 1:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool=None,
            params={},
            action="help system",
            should_preload_runtime_context=False,
            is_help=True,
            help_topic="",
        )

    sub = argv[1].lower()
    if sub == "status":
        return _system_status_spec(raw, argv, action="system status")

    return _error_spec(raw, f"未知命令 `system {argv[1]}`。", help_topic="")


def _parse_account(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    if len(argv) == 1:
        return _help_spec(raw, argv, "account")

    sub = argv[1].lower()
    if sub in {"help", "-h", "--help"}:
        return _help_spec(raw, argv, "account")
    if sub in {"status", "show", "whoami"}:
        return _system_status_spec(raw, argv, action=f"account {sub}")
    if sub in {"set", "bind"}:
        options, _, error = _parse_options(argv[2:])
        if error:
            return _error_spec(raw, error, help_topic="account set")
        missing = _missing_required(options, "student_id", "password")
        if missing:
            return _error_spec(raw, missing, help_topic="account set")
        params = {
            "student_id": _string_option(options, "student_id"),
            "password": _string_option(options, "password", strip=False),
            "library_location": _string_option(options, "library_location"),
            "library_seat_no": _string_option(options, "library_seat_no"),
            "verify_login": not _flag(options, "no_verify_login"),
            "calibrate_period_time": not _flag(options, "no_calibrate_period_time"),
        }
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="setup_account",
            params=params,
            action="account set",
            should_preload_runtime_context=False,
        )

    return _error_spec(raw, f"未知命令 `account {argv[1]}`。", help_topic="account")


def _parse_schedule(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    if len(argv) == 1:
        return _help_spec(raw, argv, "schedule")

    sub = argv[1].lower()
    if sub in {"help", "-h", "--help"}:
        return _help_spec(raw, argv, "schedule")

    options, _, error = _parse_options(argv[2:])
    if error:
        return _error_spec(raw, error, help_topic="schedule")

    if sub == "sync":
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="sync_schedule",
            params={
                "xn": _string_option(options, "xn"),
                "xq": _string_option(options, "xq"),
                "auto_calibrate": not _flag(options, "no_auto_calibrate"),
            },
            action="schedule sync",
            should_preload_runtime_context=True,
        )

    if sub in {"now", "current"}:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="schedule_query",
            params={
                "view": "current",
                "timezone": _string_option(options, "timezone") or "Asia/Shanghai",
                "auto_calibrate": not _flag(options, "no_auto_calibrate"),
            },
            action=f"schedule {sub}",
            should_preload_runtime_context=True,
        )

    if sub == "day":
        date = _string_option(options, "date") or _string_option(options, "target_date")
        if not date:
            return _error_spec(raw, "缺少参数 `--date YYYY-MM-DD`。", help_topic="schedule")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="schedule_query",
            params={
                "view": "day",
                "target_date": date,
                "timezone": _string_option(options, "timezone") or "Asia/Shanghai",
                "auto_calibrate": not _flag(options, "no_auto_calibrate"),
            },
            action="schedule day",
            should_preload_runtime_context=True,
        )

    if sub in {"week", "full"}:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="schedule_query",
            params={
                "view": sub,
                "timezone": _string_option(options, "timezone") or "Asia/Shanghai",
                "auto_calibrate": not _flag(options, "no_auto_calibrate"),
            },
            action=f"schedule {sub}",
            should_preload_runtime_context=True,
        )

    return _error_spec(raw, f"未知命令 `schedule {argv[1]}`。", help_topic="schedule")


def _parse_library(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    if len(argv) == 1:
        return _help_spec(raw, argv, "library")

    sub = argv[1].lower()
    if sub in {"help", "-h", "--help"}:
        return _help_spec(raw, argv, "library")

    options, _, error = _parse_options(argv[2:])
    if error:
        return _error_spec(raw, error, help_topic="library")

    if sub in {"locations", "areas"}:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="library_query",
            params={
                "view": "locations",
                "target_date": _string_option(options, "date") or _string_option(options, "target_date"),
            },
            action="library locations",
            should_preload_runtime_context=True,
        )
    if sub in {"seats", "seat"}:
        area_id = _string_option(options, "area_id") or _string_option(options, "area-id")
        if not area_id:
            return _error_spec(raw, "缺少参数 `--area-id`。", help_topic="library")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="library_query",
            params={
                "view": "seats",
                "area_id": area_id,
                "target_date": _string_option(options, "date") or _string_option(options, "target_date"),
                "preferred_time": _string_option(options, "time") or _string_option(options, "preferred_time") or "08:00",
            },
            action="library seats",
            should_preload_runtime_context=True,
        )
    if sub in {"current", "now"}:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="library_query",
            params={"view": "current"},
            action=f"library {sub}",
            should_preload_runtime_context=True,
        )
    if sub in {"records", "history"}:
        page, error = _int_option(options, "page", 1)
        if error:
            return _error_spec(raw, error, help_topic="library")
        limit, error = _int_option(options, "limit", 20)
        if error:
            return _error_spec(raw, error, help_topic="library")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="library_query",
            params={
                "view": "records",
                "record_type": _string_option(options, "record_type") or "1",
                "page": page,
                "limit": limit,
            },
            action=f"library {sub}",
            should_preload_runtime_context=True,
        )
    if sub in {"reserve", "book"}:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="library_reserve",
            params={
                "location": _string_option(options, "location"),
                "seat_no": _string_option(options, "seat_no"),
                "target_date": _string_option(options, "date") or _string_option(options, "target_date"),
                "preferred_time": _string_option(options, "time") or _string_option(options, "preferred_time") or "08:00",
            },
            action=f"library {sub}",
            should_preload_runtime_context=True,
        )
    if sub in {"signin", "checkin"}:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="library_auto_signin",
            params={"record_id": _string_option(options, "record_id")},
            action=f"library {sub}",
            should_preload_runtime_context=True,
        )
    if sub == "cancel":
        record_id = _string_option(options, "record_id")
        if not record_id:
            return _error_spec(raw, "缺少参数 `--record-id`。", help_topic="library")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="library_cancel",
            params={
                "record_id": record_id,
                "record_type": _string_option(options, "record_type") or "auto",
            },
            action="library cancel",
            should_preload_runtime_context=True,
        )

    return _error_spec(raw, f"未知命令 `library {argv[1]}`。", help_topic="library")


def _parse_seminar(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    if len(argv) == 1:
        return _help_spec(raw, argv, "seminar")

    sub = argv[1].lower()
    if sub in {"help", "-h", "--help"}:
        return _help_spec(raw, argv, "seminar")

    if sub == "groups":
        return _parse_seminar_groups(raw, argv)

    options, _, error = _parse_options(argv[2:])
    if error:
        return _error_spec(raw, error, help_topic="seminar")

    if sub == "filters":
        members, error = _int_option(options, "members", 0)
        if error:
            return _error_spec(raw, error, help_topic="seminar")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_query",
            params={
                "view": "filters",
                "target_date": _string_option(options, "date") or _string_option(options, "target_date"),
                "members": members,
            },
            action="seminar filters",
            should_preload_runtime_context=True,
        )

    if sub in {"rooms", "detail", "records", "signin-tasks", "signin_tasks"}:
        params: dict[str, Any] = {
            "view": "rooms",
            "target_date": _string_option(options, "date") or _string_option(options, "target_date"),
            "members": 0,
            "name": _string_option(options, "name"),
            "room": _string_option(options, "room"),
            "start_time": _string_option(options, "start") or _string_option(options, "start_time"),
            "end_time": _string_option(options, "end") or _string_option(options, "end_time"),
            "library_ids": _string_option(options, "library_ids"),
            "library_names": _string_option(options, "library_names"),
            "floor_ids": _string_option(options, "floor_ids"),
            "floor_names": _string_option(options, "floor_names"),
            "category_ids": _string_option(options, "category_ids"),
            "category_names": _string_option(options, "category_names"),
            "boutique_ids": _string_option(options, "boutique_ids"),
            "boutique_names": _string_option(options, "boutique_names"),
            "page": 1,
            "area_id": _string_option(options, "area_id"),
            "record_type": _string_option(options, "record_type") or "1",
            "limit": 20,
            "mode": _string_option(options, "mode") or "books",
            "status": _string_option(options, "status"),
        }
        members, error = _int_option(options, "members", 0)
        if error:
            return _error_spec(raw, error, help_topic="seminar")
        params["members"] = members
        page, error = _int_option(options, "page", 1)
        if error:
            return _error_spec(raw, error, help_topic="seminar")
        params["page"] = page
        limit, error = _int_option(options, "limit", 20)
        if error:
            return _error_spec(raw, error, help_topic="seminar")
        params["limit"] = limit

        if sub == "detail":
            if not params["area_id"]:
                return _error_spec(raw, "缺少参数 `--area-id`。", help_topic="seminar")
            params["view"] = "detail"
        elif sub == "records":
            params["view"] = "records"
        elif sub in {"signin-tasks", "signin_tasks"}:
            params["view"] = "signin_tasks"
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_query",
            params=params,
            action=f"seminar {sub}",
            should_preload_runtime_context=True,
        )

    if sub == "reserve":
        if not (_string_option(options, "area_id")):
            return _error_spec(raw, "缺少参数 `--area-id`。", help_topic="seminar reserve")
        is_open, error = _int_option(options, "is_open", 0)
        if error:
            return _error_spec(raw, error, help_topic="seminar reserve")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_reserve",
            params={
                "area_id": _string_option(options, "area_id"),
                "target_date": _string_option(options, "date") or _string_option(options, "target_date"),
                "start_time": _string_option(options, "start") or _string_option(options, "start_time"),
                "end_time": _string_option(options, "end") or _string_option(options, "end_time"),
                "end_date": _string_option(options, "end_date"),
                "title": _string_option(options, "title"),
                "title_id": _string_option(options, "title_id"),
                "content": _string_option(options, "content", strip=False),
                "mobile": _string_option(options, "mobile"),
                "group_name": _string_option(options, "group_name"),
                "member_ids": _string_option(options, "member_ids"),
                "is_open": is_open,
                "cate_id": _string_option(options, "cate_id"),
                "time_ranges_json": _string_option(options, "time_ranges_json", strip=False),
            },
            action="seminar reserve",
            should_preload_runtime_context=True,
        )

    if sub in {"signin", "checkin"}:
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_signin",
            params={
                "record_id": _string_option(options, "record_id"),
                "auto_scan": _flag(options, "auto_scan"),
            },
            action=f"seminar {sub}",
            should_preload_runtime_context=True,
        )

    if sub == "cancel":
        record_id = _string_option(options, "record_id")
        if not record_id:
            return _error_spec(raw, "缺少参数 `--record-id`。", help_topic="seminar")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_cancel",
            params={"record_id": record_id},
            action="seminar cancel",
            should_preload_runtime_context=True,
        )

    return _error_spec(raw, f"未知命令 `seminar {argv[1]}`。", help_topic="seminar")


def _parse_seminar_groups(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    if len(argv) == 2:
        return _help_spec(raw, argv, "seminar")

    action = argv[2].lower()
    options, _, error = _parse_options(argv[3:])
    if error:
        return _error_spec(raw, error, help_topic="seminar")

    if action == "list":
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_group",
            params={"action": "list"},
            action="seminar groups list",
            should_preload_runtime_context=True,
        )

    if action == "save":
        group_name = _string_option(options, "group_name")
        if not group_name:
            return _error_spec(raw, "缺少参数 `--group-name`。", help_topic="seminar")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_group",
            params={
                "action": "save",
                "group_name": group_name,
                "member_ids": _string_option(options, "member_ids"),
                "note": _string_option(options, "note", strip=False),
            },
            action="seminar groups save",
            should_preload_runtime_context=True,
        )

    if action == "delete":
        group_name = _string_option(options, "group_name")
        if not group_name:
            return _error_spec(raw, "缺少参数 `--group-name`。", help_topic="seminar")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="seminar_group",
            params={"action": "delete", "group_name": group_name},
            action="seminar groups delete",
            should_preload_runtime_context=True,
        )

    return _error_spec(raw, f"未知命令 `seminar groups {argv[2]}`。", help_topic="seminar")


def _parse_calibration(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    if len(argv) == 1:
        return _help_spec(raw, argv, "calibration")

    sub = argv[1].lower()
    if sub in {"help", "-h", "--help"}:
        return _help_spec(raw, argv, "calibration")
    if sub != "set":
        return _error_spec(raw, f"未知命令 `calibration {argv[1]}`。", help_topic="calibration")

    options, _, error = _parse_options(argv[2:])
    if error:
        return _error_spec(raw, error, help_topic="calibration")
    missing = _missing_required(options, "data", "cookie")
    if missing:
        return _error_spec(raw, missing, help_topic="calibration")

    return CliCommandSpec(
        raw=raw,
        argv=argv,
        resolved_tool="set_calibration_source",
        params={
            "data": _string_option(options, "data", strip=False),
            "cookie": _string_option(options, "cookie", strip=False),
            "user_agent": _string_option(options, "user_agent", strip=False),
        },
        action="calibration set",
        should_preload_runtime_context=False,
    )


def _parse_yunfz(raw: str, argv: tuple[str, ...]) -> CliCommandSpec:
    """Parse yunfz (河宝社区) commands."""
    if len(argv) == 1:
        return _help_spec(raw, argv, "yunfz")

    sub = argv[1].lower()
    if sub in {"help", "-h", "--help"}:
        return _help_spec(raw, argv, "yunfz")

    options, _, error = _parse_options(argv[2:])
    if error:
        return _error_spec(raw, error, help_topic="yunfz")

    page, error = _int_option(options, "page", 1)
    if error:
        return _error_spec(raw, error, help_topic="yunfz")
    page_size, error = _int_option(options, "page_size", 20)
    if error:
        return _error_spec(raw, error, help_topic="yunfz")

    if sub in {"leave", "请假"}:
        view = _string_option(options, "view") or "list"
        leave_id = _string_option(options, "leave_id") or _string_option(options, "id")
        if view == "detail" and not leave_id:
            return _error_spec(raw, "view=detail 时需要参数 `--leave-id`。", help_topic="yunfz")
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="yunfz_leave_query",
            params={
                "view": view,
                "leave_id": leave_id,
                "page": page,
                "page_size": page_size,
            },
            action=f"yunfz leave {view}",
            should_preload_runtime_context=True,
        )

    if sub in {"signin", "签到"}:
        view = _string_option(options, "view") or "list"
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="yunfz_signin_query",
            params={
                "view": view,
                "page": page,
                "page_size": page_size,
            },
            action=f"yunfz signin {view}",
            should_preload_runtime_context=True,
        )

    if sub in {"checksleep", "查寝"}:
        view = _string_option(options, "view") or "list"
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="yunfz_checksleep_query",
            params={
                "view": view,
                "page": page,
                "page_size": page_size,
            },
            action=f"yunfz checksleep {view}",
            should_preload_runtime_context=True,
        )

    if sub in {"activity", "活动"}:
        view = _string_option(options, "view") or "list"
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="yunfz_activity_query",
            params={
                "view": view,
                "page": page,
                "page_size": page_size,
            },
            action=f"yunfz activity {view}",
            should_preload_runtime_context=True,
        )

    if sub in {"collection", "收集", "info"}:
        view = _string_option(options, "view") or "list"
        return CliCommandSpec(
            raw=raw,
            argv=argv,
            resolved_tool="yunfz_collection_query",
            params={
                "view": view,
                "page": page,
                "page_size": page_size,
            },
            action=f"yunfz collection {view}",
            should_preload_runtime_context=True,
        )

    return _error_spec(raw, f"未知命令 `yunfz {argv[1]}`。", help_topic="yunfz")


def _help_spec(raw: str, argv: tuple[str, ...], topic: str) -> CliCommandSpec:
    return CliCommandSpec(
        raw=raw,
        argv=argv,
        resolved_tool=None,
        params={},
        action=f"help {topic}".strip(),
        should_preload_runtime_context=False,
        is_help=True,
        help_topic=topic,
    )


def _system_status_spec(raw: str, argv: tuple[str, ...], action: str) -> CliCommandSpec:
    return CliCommandSpec(
        raw=raw,
        argv=argv,
        resolved_tool="system_status",
        params={},
        action=action,
        should_preload_runtime_context=False,
    )


def _error_spec(raw: str, message: str, *, help_topic: str) -> CliCommandSpec:
    return CliCommandSpec(
        raw=raw,
        argv=(),
        resolved_tool=None,
        params={},
        action="error",
        should_preload_runtime_context=False,
        help_topic=help_topic,
        error=message,
    )


def _normalize_topic(parts: Sequence[str]) -> str:
    return " ".join(str(part).strip().lower() for part in parts if str(part).strip())


def _parse_options(tokens: Sequence[str]) -> tuple[dict[str, Any], list[str], str | None]:
    options: dict[str, Any] = {}
    positionals: list[str] = []
    index = 0

    while index < len(tokens):
        token = str(tokens[index])
        if not token.startswith("--"):
            positionals.append(token)
            index += 1
            continue

        name, has_equals, value = token[2:].partition("=")
        key = name.replace("-", "_").strip()
        if not key:
            return {}, [], "参数格式错误：`--` 后缺少参数名。"

        if has_equals:
            options[key] = value
            index += 1
            continue

        if index + 1 < len(tokens) and not str(tokens[index + 1]).startswith("--"):
            options[key] = tokens[index + 1]
            index += 2
            continue

        options[key] = True
        index += 1

    return options, positionals, None


def _missing_required(options: dict[str, Any], *keys: str) -> str:
    missing = [f"--{key.replace('_', '-')}" for key in keys if not _string_option(options, key)]
    if not missing:
        return ""
    return f"缺少必填参数：{', '.join(missing)}。"


def _string_option(options: dict[str, Any], key: str, *, strip: bool = True) -> str:
    value = options.get(key)
    if value in (None, False, True):
        return ""
    text = str(value)
    return text.strip() if strip else text


def _flag(options: dict[str, Any], key: str) -> bool:
    value = options.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text not in {"0", "false", "no", "off"}


def _int_option(options: dict[str, Any], key: str, default: int) -> tuple[int, str]:
    value = options.get(key)
    if value in (None, "", False, True):
        return default, ""
    try:
        return int(value), ""
    except (TypeError, ValueError):
        return default, f"参数 `--{key.replace('_', '-')}` 必须是整数。"
