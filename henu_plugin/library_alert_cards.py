"""Shared readable layouts for QQ alert replies and plain-text notifications."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from henu_plugin.qq_cards import Card, escape

TITLE = "图书馆空位提醒"
CONTROLS = "查看：空位提醒状态\n停止：取消空位提醒"
ZONE = ZoneInfo("Asia/Shanghai")
SEAT_PREVIEW = 6


def help_card():
    return Card(
        TITLE,
        "",
        (
            "先查“图书馆座位”选区域，再复制下面一条设置。",
            "",
            "**整个区域 · 每10分钟**",
            '设置空位提醒 明天\n"701 七层南自习室" 08:00 23:00 全部 10',
            "",
            "**只看001号座位 · 每5分钟**",
            '设置空位提醒 明天\n"701 七层南自习室" 08:00 23:00 001 5',
            "",
            "替换区域、日期和时段；时段以学校开放时间为准。",
            "有新空位会继续提醒；每人1项，使用开始时结束。",
            "登录失效会暂停，手动查座后重新设置。",
        ),
        CONTROLS,
    )


def _where_when(plan):
    area = plan.get("area") or {}
    if not area:
        return []
    location = str(area.get("location") or "区域待核对").translate(
        str.maketrans("()", "（）")
    )
    place = (
        escape(str(area.get("campus") or "校区待确认"), 40)
        + "\n**"
        + escape(location, 120)
        + "**"
    )
    target = str(plan.get("target_date") or "")
    try:
        day = date.fromisoformat(target)
        target = f"{day.month}月{day.day}日 · 周{'一二三四五六日'[day.weekday()]}"
    except ValueError:
        pass
    window = (
        escape(target, 40)
        + "\n"
        + escape(plan.get("start"), 8)
        + "–"
        + escape(plan.get("end"), 8)
    )
    seat = plan.get("seat_no")
    scope = "监控：" + (escape(seat, 16) + "号座位" if seat else "整个区域")
    return [place, "", window, scope]


def _last_check(plan):
    result = str(plan.get("last_result") or "")
    if not result:
        return []
    if result.startswith("当前空闲："):
        # Legacy plans retain the creation preview as text. This prefix is only
        # written with the same successful initial observation, never on errors.
        count = sum(state == "free" for state in (plan.get("observed") or {}).values())
        seats = result.split("：", 1)[1].split("、")[:SEAT_PREVIEW]
        result = (
            (f"空闲 {count} 座" if count else "已发现空位")
            + "\n座位示例："
            + "、".join(seats)
        )
    elif result.startswith("已查询 · "):
        result = result.removeprefix("已查询 · ")
    heading = "最近检查"
    try:
        stamp = datetime.fromisoformat(plan.get("last_checked") or "")
        heading += " · " + stamp.astimezone(ZONE).strftime("%H:%M")
    except ValueError:
        pass
    return ["", "**" + heading + "**", escape(result, 240)]


def status_card(plan):
    state = plan.get("state")
    status = {
        "none": "暂无空位提醒",
        "active": "已开启",
        "cancelled": "已取消",
        "expired": "已结束 · 已到使用时间",
        "paused": "已暂停",
        "preparing": "正在设置",
    }.get(state, "状态待核对")
    if state == "active":
        status += f" · 每{plan['interval_minutes']}分钟检查"
    lines = _where_when(plan) if state != "preparing" else []
    next_step = "发送“空位提醒帮助”查看设置方法。"
    if state == "active":
        lines += _last_check(plan)
        next_step = CONTROLS
    elif state == "none":
        lines = ["可以监控整个区域，也可以只看一个座位。"]
    elif state == "paused":
        lines += ["", escape(plan.get("last_result") or "登录状态需要重新核对。", 240)]
        next_step = "先发送“图书馆座位”手动恢复，再重新设置提醒。"
    elif state == "cancelled":
        lines += ["", "提醒已停止。"]
    elif state == "expired":
        lines += ["", "本次监控已结束。"]
    elif state == "preparing":
        lines = ["稍后发送“空位提醒状态”查看结果。"]
        next_step = "如需停止，发送“取消空位提醒”。"
    else:
        lines = ["请先查询当前提醒状态，再决定是否重新设置。"]
    if state == "active" and plan.get("last_delivery") == "unknown":
        lines += ["", "上次通知结果未确认，本批空位不会自动重发。"]
    return Card(TITLE, status, tuple(lines), next_step)


def new_seats_card(plan, seats):
    lines = _where_when(plan) + [
        "",
        "**新空位**",
        escape("、".join(seats[:SEAT_PREVIEW]), 160),
    ]
    if len(seats) > SEAT_PREVIEW:
        lines.append(f"本条列出{SEAT_PREVIEW}座，共新增{len(seats)}座。")
    return Card(
        "图书馆有新空位",
        f"新增 {len(seats)} 座 · 继续监控",
        tuple(lines),
        "空位可能变化，请及时查座。发送“取消空位提醒”可停止。",
    )


def paused_card(plan, reason):
    return Card(
        "空位提醒已暂停",
        "",
        tuple(_where_when(plan) + ["", escape(reason, 240)]),
        "先发送“图书馆座位”手动恢复，再重新设置提醒。",
    )


def error_card(message, *, uncertain=False):
    return Card(
        TITLE,
        "状态待核对" if uncertain else "本次操作未完成",
        (escape(message, 360),),
        "先发送“空位提醒状态”核对。"
        if uncertain
        else "发送“空位提醒帮助”查看设置方法。",
    )
