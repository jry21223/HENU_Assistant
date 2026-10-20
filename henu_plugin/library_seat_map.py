"""Private library selection: school metadata -> time -> seats -> preview only."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import shlex
import time

from henu_plugin.library_availability import campus_now, parse_window
from henu_plugin.qq_cards import Card, card_chain, escape, scope
from henu_plugin.qq_buttons import envelope, confirmation_actions
from henu_plugin.seat_map_image import seat_state, render_seat_map

_QUERY_SLOTS = asyncio.Semaphore(4)


async def owner(ctx):
    if not callable(getattr(ctx, "get_bot_uuid", None)):
        return None
    try:
        key = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
    except Exception:
        return None
    return key if key and key[1] == "person" else None


def states(plugin):
    if not hasattr(plugin, "_library_maps"):
        plugin._library_maps = {}
    current = plugin._library_maps
    for key, value in list(current.items()):
        if value["expires"] <= time.monotonic():
            current.pop(key, None)
    return current


def selection_guard(plugin, key):
    current = states(plugin).get(key)
    return current, current.get('version') if current else None


def install_selection(plugin, key, selection, guard):
    current = states(plugin)
    before, version = guard
    if current.get(key) is not before or before and before.get('version') != version:
        return False
    current[key] = {**selection, 'stage': 'seats', 'choices': [], 'version': secrets.token_urlsafe(12), 'expires': time.monotonic()+300}
    while len(current) > 128:
        current.pop(next(iter(current)))
    return True


async def say(ctx, title, lines, wire=None):
    blocks, block = [], ""
    for line in lines:
        if len((block + "\n" + line).encode()) > 1800:
            blocks.append(block)
            block = ""
        block += ("\n" if block else "") + line
    if block:
        blocks.append(block)
    pages = Card(title, "", tuple(blocks)).pages()
    for index, page in enumerate(pages):
        await ctx.reply(
            card_chain(envelope(page, wire if index == len(pages) - 1 else None))
        )


async def start_selection(plugin, ctx, result):
    key = await owner(ctx)
    rows = [
        r
        for r in result.get("locations", [])
        if re.fullmatch(r"\d{1,12}", str(r.get("area_id") or ""))
        and r.get("campus") in {"金明校区", "明伦校区", "郑州校区", "龙子湖校区"}
    ]
    if not key or not rows:
        return None
    current = states(plugin)
    version = secrets.token_urlsafe(12)
    current[key] = {
        "rows": rows,
        "version": version,
        "expires": time.monotonic() + 300,
        "stage": "campus",
        "window": parse_window(
            str(result.get("date") or "")
            + " "
            + str(result.get("start") or "")
            + "-"
            + str(result.get("end") or ""),
            campus_now(),
        ),
    }
    while len(current) > 128:
        current.pop(next(iter(current)))
    current[key]["choices"] = [
        (
            c,
            {"kind": "library_campus", "campus": c, "version": version},
        )
        for c in sorted(
            set(r["campus"] for r in rows),
            key=lambda c: ({"金明校区": 0, "明伦校区": 1}.get(c, 2), c),
        )
    ]


def selection_context(plugin, key):
    """Read-only menu context for natural followups, without booking authority."""
    state = states(plugin).get(key) if key and key[1] == "person" else None
    if not state:
        return ""
    area = state.get("area", {})
    window = state.get("window") or ("", "", "")
    return json.dumps({
        "source": "recent_library_menu",
        "campus": area.get("campus") or state.get("campus", ""),
        "location": area.get("location", ""),
        "date": window[0], "start": window[1], "end": window[2],
        "choices": [str(label)[:100] for label, _ in state.get("choices", [])[:12]],
    }, ensure_ascii=False)


def _fetch_seats(cookies, area, target, start, end):
    import requests
    from urllib3.util import Timeout
    from campus_core import HenuCampusBot

    deadline = time.monotonic() + 20

    class BoundedSession(requests.Session):
        def request(self, method, url, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("library deadline")
            kwargs.update(
                timeout=Timeout(
                    total=remaining, connect=min(3, remaining), read=min(6, remaining)
                ),
                allow_redirects=False,
            )
            return super().request(method, url, **kwargs)

    bot = HenuCampusBot("", "", cookies)
    headers = dict(bot.session.headers)
    bot.session.close()
    bot.session = BoundedSession()
    bot.session.headers.update(headers)
    bot.session.cookies.update(
        {k: v for k, v in cookies.items() if not k.startswith("_")}
    )
    bot._set_auth_header()
    post = bot._post_json

    def read_post(path, payload, **kwargs):
        kwargs["allow_reauth"] = False
        return post(path, payload, **kwargs)

    bot._post_json = read_post
    try:
        space = bot._get_space_map(area["area_id"])
        windows = []
        for day in (space.get("date") or {}).get("list") or []:
            date = str(day.get("day") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                continue
            for slot in day.get("times") or []:
                if not isinstance(slot, dict) or str(slot.get("status", "1")) != "1":
                    continue
                first, last = (
                    bot._to_hhmm(slot.get("start")),
                    bot._to_hhmm(slot.get("end")),
                )
                if first and last and first < last:
                    windows.append((date, first, last))
            first = bot._to_hhmm(day.get("def_start_time") or day.get("start_time"))
            last = bot._to_hhmm(day.get("def_end_time") or day.get("end_time"))
            if first and last and first < last and not day.get("times"):
                windows.append((date, first, last))
        if not target:
            return {"success": True, "windows": windows[:20]}
        plan = bot._build_reservation_plan(
            area["area_id"], space, target, preferred_time=start, preferred_end_time=end
        )
        payload = plan["seat_query"]
        if (payload.get("day"), payload.get("start_time"), payload.get("end_time")) != (
            target,
            start,
            end,
        ):
            return {"success": False, "windows": windows[:20]}
        response = bot._post_json("/v4/Space/seat", payload)
        rows = (response.get("data") or {}).get("list")
        if response.get("code") != 0 or not isinstance(rows, list) or len(rows) > 2000:
            return {"success": False}
        safe = [
            {
                k: r.get(k)
                for k in (
                    "no",
                    "point_x",
                    "point_y",
                    "width",
                    "height",
                    "status",
                    "status_name",
                )
            }
            for r in rows
            if isinstance(r, dict)
        ]
        free = [
            str(r["no"])
            for r in safe
            if seat_state(r) == "free"
            and re.fullmatch(r"[A-Za-z0-9_-]{1,16}", str(r.get("no") or ""))
        ]
        image = None
        try:
            image = render_seat_map(
                area["campus"] + " · " + str(space.get("name") or area["location"]),
                target + " " + start + "–" + end,
                safe,
                campus_now().strftime("%H:%M:%S"),
            )
        except (ValueError, KeyError, TypeError, OSError):
            pass
        return {
            "success": True,
            "seats": free,
            "total_count": len(rows),
            "reserved_count": sum(seat_state(r) == "reserved" for r in safe),
            "in_use_count": sum(seat_state(r) == "in_use" for r in safe),
            "unknown_count": sum(seat_state(r) == "unknown" for r in safe),
            "image": base64.b64encode(image).decode() if image else None,
            "windows": windows[:20],
        }
    except RuntimeError:
        return {"success": False, "windows": locals().get("windows", [])[:20]}
    finally:
        bot.session.close()


async def query_seats(plugin, sender, area, target="", start="", end=""):
    from henu_plugin.storage_adapter import PluginStorageAdapter

    async with _QUERY_SLOTS:
        adapter = PluginStorageAdapter(plugin, sender)
        worker = None
        try:
            paths = await adapter.load_all()
            cookies = json.loads(paths.library_cookie_file.read_text())
            profile = json.loads(paths.profile_file.read_text())
            subject = cookies.get("_subject_student_id")
            if (
                not cookies.get("_v4_token")
                or subject
                and str(subject) != str(profile.get("student_id") or "")
            ):
                return {"success": False}
            worker = asyncio.create_task(
                asyncio.to_thread(_fetch_seats, cookies, area, target, start, end)
            )
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                await worker
                raise
        except Exception:
            return {"success": False}
        finally:
            await adapter.abort()


async def show_windows(plugin, ctx, state, result):
    specs = []
    lines = [
        escape(state["area"]["campus"] + " · " + state["area"]["location"]),
        "选择学校开放的完整时段，或发送日期和起止时间。",
    ]
    for day, start, end in result.get("windows") or []:
        label = day[5:] + " " + start + "-" + end
        specs.append(
            (
                label,
                {
                    "kind": "library_window",
                    "window": (day, start, end),
                    "version": state["version"],
                },
            )
        )
    if not specs:
        lines.append("例如“今天14:00-16:00”；如果学校未开放这个时段，会提示重新选择。")
    await show_choices(ctx, state, "选择使用时段", lines, specs)


async def show_map(plugin, ctx, state, window):
    key = await owner(ctx)
    area = dict(state["area"])
    generation = secrets.token_urlsafe(12)
    state["version"] = generation
    await say(ctx, "图书馆选座", ["正在查所选时段的座位。"])
    result = await query_seats(plugin, ctx.event.sender_id, area, *window)
    if (
        states(plugin).get(key) is not state
        or state["version"] != generation
        or state["area"] != area
    ):
        return
    if not result.get("success"):
        state["stage"] = "window"
        await say(
            ctx,
            "这个时段暂时无法选座",
            [
                "学校没有返回这个完整时段的可用座位，请选择当前开放时段；不会自动扩大预约时间。"
            ],
        )
        await show_windows(plugin, ctx, state, result)
        return
    state.update(
        stage="seats",
        window=window,
        seats=result["seats"],
        choices=[],
        expires=time.monotonic() + 300,
    )
    image_ok = False
    if result.get("image"):
        from langbot_plugin.api.entities.builtin.platform.message import (
            MessageChain,
            Image,
        )

        try:
            await ctx.reply(MessageChain([Image(base64=result["image"])]))
            image_ok = True
        except Exception:
            pass  # Unknown image delivery: never resend automatically.
    if (
        states(plugin).get(key) is not state
        or state["version"] != generation
        or state["area"] != area
    ):
        return
    for page in seat_card(area, window, result, image_ok).pages():
        await ctx.reply(card_chain(page))


def seat_card(area, window, result, image_ok=False):
    """Use the same seat facts/layout for menu and natural-language queries."""
    day, start, end = window
    seats = [str(row.get('no') or row.get('seat_no') or '') if isinstance(row, dict) else str(row) for row in result.get('seats', [])]
    name = ' · '.join(str(v) for v in (area.get('campus'), area.get('location') or area.get('name')) if v)
    available = result.get('available_count', len(seats))
    reserved = result.get('reserved_count')
    counts = result.get('status_counts')
    if type(reserved) is not int and isinstance(counts, dict) and counts:
        reserved = counts.get('2', 0)
    lines = [
        escape(name),
        day + " " + start + "–" + end,
        f"剩余 {available} 座" + (f" · 已预约 {reserved} 座" if type(reserved) is int else " · 已预约数量未返回"),
        "高清图按学校坐标重绘，颜色对应本次查询时段。"
        if image_ok
        else "座位图没能显示，下面仍可按座位号选择。",
    ]
    # The original school raster is reference only; do not present its green
    # pixels as availability. A no-geometry fallback can link it explicitly.
    if result.get("map_url"):
        lines.append("学校位置底图颜色不代表当前空闲。")
        url = result["map_url"]
        if re.fullmatch(
            r"https://zwyy\.henu\.edu\.cn/home/images/web/area/\d+/seat-free\.(jpg|png)",
            url,
        ):
            lines.append("![学校座位位置图](" + url + ")")
    if seats:
        lines.append(
            "空闲座位：" + "、".join(escape(no) for no in seats[:24])
        )
    lines.append("看图回复座位号，如“002”；回复“刷新座位”可重查。选中后再核对确认。")
    return Card("选择座位", "", tuple(lines))


async def preview(plugin, ctx, state, seat):
    if seat not in state.get("seats", []):
        await say(ctx, "这个座位暂不可选", ["请刷新座位，查看所选时段的空闲座位。"])
        return
    # Refresh before creating any pending write. Do not silently substitute.
    key = await owner(ctx)
    area, window, generation = (
        dict(state["area"]),
        tuple(state["window"]),
        state["version"],
    )
    result = await query_seats(plugin, ctx.event.sender_id, area, *window)
    if (
        states(plugin).get(key) is not state
        or state["version"] != generation
        or state["area"] != area
    ):
        return
    if not result.get("success") or seat not in result["seats"]:
        await say(ctx, "座位状态变了", ["这个座位目前无法确认可用，请刷新座位后重选。"])
        return
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from langbot_plugin.api.entities.builtin.provider.session import (
        Session,
        LauncherTypes,
    )

    tool = HenuCliSafe()
    tool.plugin = plugin
    day, start, end = window
    location = area["location"]
    command = (
        "library reserve --location "
        + shlex.quote(location)
        + " --seat-no "
        + shlex.quote(seat)
        + " --date "
        + day
        + " --time "
        + start
        + " --end-time "
        + end
    )
    session = Session(
        sender_id=ctx.event.sender_id,
        launcher_id=ctx.event.launcher_id,
        launcher_type=LauncherTypes.PERSON,
    )
    result = await tool.call({"command": command}, session, ctx.query_id)
    await ctx.set_query_var("_henu_card", None)
    if (
        states(plugin).get(key) is not state
        or state["version"] != generation
        or state["area"] != area
    ):
        return
    if not result.get("confirmation_required"):
        await say(
            ctx,
            "预约尚未提交",
            [
                escape(
                    result.get("reply_text")
                    or result.get("msg")
                    or "预约信息暂时无法核对，请稍后重试。"
                )
            ],
        )
        return
    state["stage"] = "preview"
    wire = await confirmation_actions(
        plugin,
        str(await ctx.get_bot_uuid()),
        session,
        ctx.query_id,
        expected_command=command,
    )
    if not wire:
        await say(ctx, "预览已失效", ["待确认内容已更新，请重新选座。本次没有提交。"])
        return
    await say(
        ctx,
        "确认预约",
        [
            escape(area["campus"] + " · " + location),
            day + " " + start + "–" + end,
            "座位 " + escape(seat) + "号",
            "尚未提交。请核对日期、时段和座位；确认后才会预约。",
        ],
        wire,
    )


async def dispatch_library(plugin, ctx, payload):
    key = await owner(ctx)
    state = states(plugin).get(key)
    if not state or state["version"] != payload["version"]:
        await say(ctx, "选座信息已过期", ["请重新发送“图书馆座位”。"])
        return
    kind = payload["kind"]
    if kind == "library_campus":
        state["version"] = secrets.token_urlsafe(12)
        state["campus"] = payload["campus"]
        rows = [r for r in state["rows"] if r["campus"] == payload["campus"]]
        floors = list(dict.fromkeys(r.get("floor") or "楼层待确认" for r in rows))
        if len(floors) == 1:
            await show_regions(plugin, ctx, state, rows)
            return
        specs = [
            (
                floor,
                {"kind": "library_floor", "floor": floor, "version": state["version"]},
            )
            for floor in floors
        ]
        state["stage"] = "floor"
        await show_choices(
            ctx,
            state,
            "选择楼层",
            [escape(payload["campus"])],
            specs,
        )
    elif kind == "library_floor":
        rows = [
            r
            for r in state["rows"]
            if r["campus"] == state["campus"]
            and (r.get("floor") or "楼层待确认") == payload["floor"]
        ]
        await show_regions(plugin, ctx, state, rows)
    elif kind == "library_regions":
        await show_regions(plugin, ctx, state, state["region_rows"], payload["offset"])
    elif kind == "library_area":
        state["version"] = secrets.token_urlsafe(12)
        state["area"] = next(
            r for r in state["rows"] if r["area_id"] == payload["area"]
        )
        state["stage"] = "window"
        if state.get("window"):
            await show_map(plugin, ctx, state, state["window"])
            return
        area, generation = dict(state["area"]), state["version"]
        result = await query_seats(plugin, ctx.event.sender_id, area)
        if (
            states(plugin).get(key) is not state
            or state["version"] != generation
            or state["area"] != area
        ):
            return
        await show_windows(plugin, ctx, state, result)
    elif kind == "library_window":
        await show_map(plugin, ctx, state, tuple(payload["window"]))


async def show_regions(plugin, ctx, state, rows, offset=0):
    state.update(
        region_rows=rows,
        version=secrets.token_urlsafe(12),
        expires=time.monotonic() + 300,
        stage="region",
    )
    selected = rows[offset : offset + 8]
    specs = [
        (
            str(r["location"]),
            {"kind": "library_area", "area": r["area_id"], "version": state["version"]},
        )
        for r in selected
    ]
    if offset + 8 < len(rows):
        specs.append(
            (
                "更多区域",
                {
                    "kind": "library_regions",
                    "offset": offset + 8,
                    "version": state["version"],
                },
            )
        )
    await show_choices(
        ctx,
        state,
        "选择区域",
        [escape(state["campus"])],
        specs,
    )


async def show_choices(ctx, state, title, lines, choices):
    state["choices"] = choices
    lines += [str(i) + ". " + escape(label) for i, (label, _) in enumerate(choices, 1)]
    if choices:
        lines.append("回复序号或名称。")
    await say(ctx, title, lines)


async def handle_library_text(plugin, ctx, text):
    from langbot_plugin.api.entities.builtin.platform.message import Plain, At, Source

    chain = getattr(getattr(ctx.event, "message_chain", None), "root", [])
    if not chain or any(not isinstance(part, (Plain, At, Source)) for part in chain):
        return False
    if not getattr(plugin, "_library_maps", None):
        return False
    key = await owner(ctx)
    state = states(plugin).get(key)
    if not state:
        return False
    window = parse_window(text, campus_now()) if state["stage"] == "window" else None
    text = text.strip()
    seat = (
        re.fullmatch(r"选座\s*([A-Za-z0-9_-]{1,16})|([0-9]{1,16})", text)
        if state["stage"] == "seats"
        else None
    )
    refresh = text == "刷新座位" and state["stage"] == "seats"
    choices = state.get("choices", []) if state["stage"] != "preview" else []
    selected = None
    if re.fullmatch(r"[0-9]{1,3}", text) and 1 <= int(text) <= len(choices):
        selected = choices[int(text) - 1][1]
    else:
        for label, payload in choices:
            if text == label or (
                payload["kind"] == "library_campus"
                and text == label.removesuffix("校区")
            ):
                selected = payload
                break
    if not window and not seat and not refresh and not selected:
        return False
    if getattr(ctx, "is_prevent_postorder", False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    if selected:
        await dispatch_library(plugin, ctx, selected)
    elif window:
        await show_map(plugin, ctx, state, window)
    elif refresh:
        await show_map(plugin, ctx, state, state["window"])
    else:
        await preview(plugin, ctx, state, seat[1] or seat[2])
    return True
