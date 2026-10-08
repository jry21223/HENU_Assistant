"""Strict grammar for the private one-shot booking namespace."""

from __future__ import annotations

import re
import shlex

HELP = [
    "library schedule add --run-at 'YYYY-MM-DDTHH:MM:SS+08:00' --location <区域> --seat-no <座位> --date YYYY-MM-DD --time HH:MM --end-time HH:MM",
    "library schedule list",
    "library schedule status --id <任务号>",
    "library schedule cancel --id <任务号>",
    "执行时刻为东八区，需在未来 30 天内；--date/--time/--end-time 为座位使用日期和时段。",
    "仅一次执行，非每日循环；本人私聊下一条“确认定时预约”后保存，五分钟有效。",
    "会员费用已包含。结果未知不会自动重试，请先查学校预约记录。",
    "清除尚未确认的预览：library schedule cancel --id awaiting。",
]


def is_schedule_command(command):
    try:
        argv = shlex.split(command)
    except ValueError:
        return bool(re.match(r"^\s*library\s+schedule\b", command, re.I))
    return len(argv) >= 2 and [v.lower() for v in argv[:2]] == ["library", "schedule"]


def parse(raw, argv):
    from henu_plugin.cli import CliCommandSpec

    action = argv[2].lower() if len(argv) > 2 else "help"
    error = ""
    params = {}
    tool = None
    if action in {"help", "--help", "-h"} and len(argv) <= 3:
        return CliCommandSpec(
            raw, argv, None, {}, "help", False, True, "library_schedule"
        )
    allowed = (
        {"--run-at", "--location", "--seat-no", "--date", "--time", "--end-time"}
        if action == "add"
        else {"--id"}
        if action in {"status", "cancel"}
        else set()
    )
    rest = list(argv[3:])
    while rest and not error:
        key = rest.pop(0)
        option, sep, value = key.partition("=")
        if option not in allowed or option in params:
            error = "定时预约参数未知或重复。"
            break
        if not sep:
            value = rest.pop(0) if rest else ""
        if (
            not value
            or value.startswith("--")
            or len(value) > 128
            or any(ord(c) < 32 for c in value)
        ):
            error = "定时预约参数值无效。"
            break
        params[option] = value
    if action not in {"add", "list", "status", "cancel"} or (
        action == "list" and len(argv) != 3
    ):
        error = "定时预约命令格式无效。"
    if set(params) != allowed:
        error = error or "定时预约需要明确指定所有日期、时间和座位参数。"
    if not error:
        tool = "library_schedule_" + action
        mapping = {
            "--run-at": "run_at",
            "--location": "location",
            "--seat-no": "seat_no",
            "--date": "target_date",
            "--time": "preferred_time",
            "--end-time": "preferred_end_time",
            "--id": "id",
        }
        params = {mapping[k]: v for k, v in params.items()}
    return CliCommandSpec(
        raw,
        argv,
        tool,
        params,
        "library schedule " + action,
        False,
        help_topic="library_schedule",
        error=error,
    )
