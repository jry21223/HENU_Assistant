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
            "设置后可选有位自动预约，仅限 HENU-Kit 终身会员。",
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
    scope = "监控：" + (escape(seat, 64) + "号座位" if seat else "整个区域")
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


def status_card(plan, *, offer_booking=False):
    state = plan.get("state")
    status = {
        "none": "暂无空位提醒",
        "active": "已开启",
        "cancelled": "已取消",
        "expired": "已结束 · 已到使用时间",
        "paused": "已暂停",
        "preparing": "正在设置",
        "booking": "预约已发起 · 结果待核对",
        "reserved": "预约成功 · 本次任务已结束",
        "submitted_unverified": "已提交 · 预约记录待核对",
        "reservation_unknown": "预约结果未确认 · 已停止",
        "reservation_failed": "预约未完成 · 已停止",
    }.get(state, "状态待核对")
    if state == "active":
        status += f" · 每{plan['interval_minutes']}分钟检查"
    lines = _where_when(plan) if state != "preparing" else []
    next_step = "发送“空位提醒帮助”查看设置方法。"
    if state == "active":
        lines += [
            "",
            "模式：有位自动预约（终身会员）"
            if plan.get("auto_reserve")
            else "模式：仅提醒",
        ]
        lines += _last_check(plan)
        next_step = CONTROLS
        if offer_booking and not plan.get("auto_reserve"):
            lines += [
                "",
                "有空位后要直接预约吗？",
                "仅提醒：不用操作。",
                "自动预约：发送“有位自动预约”，核对后授权。",
            ]
        elif plan.get("auto_reserve"):
            lines += ["成功后结束；结果不确定时停止，不自动重试。"]
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
    elif state in {
        "booking",
        "reserved",
        "submitted_unverified",
        "reservation_unknown",
        "reservation_failed",
    }:
        if plan.get("booking_seat"):
            lines += ["", "本次座位：" + escape(plan["booking_seat"], 64)]
        lines += [
            "",
            escape(plan.get("last_result") or "请先核对学校当前预约记录。", 240),
        ]
        next_step = (
            "请先查询学校预约记录，不要重复预约。"
            if state != "reserved"
            else "请按学校要求本人到馆签到。"
        )
        if plan.get("cancel_requested"):
            lines += ["监控已停止；取消提醒不会撤回已发起的学校预约。"]
    else:
        lines = ["请先查询当前提醒状态，再决定是否重新设置。"]
    if plan.get("last_delivery") == "unknown":
        lines += ["", "上次通知的发送结果未确认，不自动重发。"]
    return Card(TITLE, status, tuple(lines), next_step)


def booking_confirmation_card(plan):
    pending = plan["booking_pending"]
    lines = _where_when(plan) + [
        "",
        "模式：有位后自动预约（终身会员）",
        "只预约上述区域内一个可用座位。"
        if not plan.get("seat_no")
        else "只预约这个座位，不替换其他座位。",
        "确认后，后台按原间隔检查；有位时直接提交一次，不再询问。",
        "成功后结束；失败或结果不确定时停止，不重试。",
    ]
    return Card(
        "确认有位自动预约",
        "当前仍仅提醒 · 尚未授权预约",
        tuple(lines),
        "确认：确认有位自动预约 "
        + pending["token"]
        + "\n取消：保持仅提醒\n5分钟内有效。",
    )


def new_seats_card(plan, seats):
    lines = _where_when(plan) + [
        "",
        "**新空位**",
        "、".join(escape(seat, 64) for seat in seats[:SEAT_PREVIEW]),
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
