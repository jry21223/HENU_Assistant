"""Durable, low-frequency QQ alerts for newly available library seats."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import shlex
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from henu_plugin.background import create_background_task
from henu_plugin.library_alert_query import (
    AlertQueryError,
    query_snapshot,
    saved_session,
    seat_identity,
)
from henu_plugin.qq_cards import Card, card_chain, escape

ZONE = ZoneInfo("Asia/Shanghai")
KEY = "library_seat_alerts:v1"
logger = logging.getLogger(__name__)


class LibrarySeatAlerts:
    def __init__(self, plugin):
        self.plugin = plugin
        if not hasattr(plugin, "_library_alert_lock"):
            plugin._library_alert_lock = asyncio.Lock()
        self.lock = plugin._library_alert_lock
        if not hasattr(plugin, "_library_alert_workers"):
            plugin._library_alert_workers = {}
            plugin._library_alert_query_slots = asyncio.Semaphore(4)
        self.workers = plugin._library_alert_workers
        self.query_slots = plugin._library_alert_query_slots
        self.task = None

    @staticmethod
    def scope(bot, sender):
        return hashlib.sha256(json.dumps([bot, sender]).encode()).hexdigest()

    async def read(self):
        from langbot_plugin.entities.io.errors import ActionCallError
        import re

        try:
            raw = await self.plugin.get_plugin_storage(KEY)
        except (KeyError, FileNotFoundError):
            raw = None
        except ActionCallError as exc:
            if not re.fullmatch(
                r"(?:ActionCallError: )*Storage with key " + KEY + r" not found",
                str(exc),
            ):
                raise
            raw = None
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            raise ValueError("invalid alert registry")
        return data

    async def write(self, data):
        await self.plugin.set_plugin_storage(
            KEY, json.dumps(data, ensure_ascii=False).encode()
        )

    async def status(self, bot, sender):
        async with self.lock:
            return (await self.read()).get(self.scope(bot, sender), {"state": "none"})

    async def handle(self, ctx, text, *, now=None):
        controls = {"空位提醒", "空位提醒帮助", "空位提醒状态", "取消空位提醒"}
        if text not in controls and not text.startswith("设置空位提醒"):
            return False
        if getattr(ctx, "is_prevent_postorder", False):
            return True
        now = now or datetime.now(ZONE)
        ctx.prevent_default()
        ctx.prevent_postorder()
        try:
            kind = getattr(
                getattr(ctx.event, "launcher_type", ""),
                "value",
                getattr(ctx.event, "launcher_type", ""),
            )
            sender = str(getattr(ctx.event, "sender_id", "") or "").strip()
            launcher = str(getattr(ctx.event, "launcher_id", "") or "").strip()
            bot = str(await ctx.get_bot_uuid() or "").strip()
            if kind != "person":
                raise ValueError("请在机器人私聊中设置或查看个人空位提醒。")
            if (
                not bot
                or not re.fullmatch(r"[0-9A-Za-z._-]{1,128}", sender)
                or sender in {"0", "unknown", "None"}
                or launcher != sender
            ):
                raise ValueError("无法确认当前私聊身份，请重新进入机器人私聊。")
            if text in {"空位提醒", "空位提醒帮助"}:
                message = (
                    '设置空位提醒 日期 "区域名称或ID" 开始 结束 [座位号或全部] [5或10]\n'
                    '例如：设置空位提醒 明天 "701 七层南自习室" 14:00 16:00 全部 10\n'
                    "只看一个座位时，把“全部”换成座位号，如001。日期也可填今天或YYYY-MM-DD。\n"
                    "默认每10分钟，可选每5分钟；同一批空位不重复提醒，出现新空位继续提醒。\n"
                    "每人每个机器人可开一个提醒，到使用开始时停止。发“空位提醒状态”查看，发“取消空位提醒”停止。\n"
                    "请先手动查询图书馆座位。后台复用已有登录态，失效后暂停；手动查座后重新设置。"
                )
            elif text == "空位提醒状态":
                message = self.describe(await self.status(bot, sender))
            else:
                source = self.message_source(ctx, now)
                if text == "取消空位提醒":
                    async with self.lock:
                        data = await self.read()
                        key = self.scope(bot, sender)
                        plan = data.get(key, {"state": "none"})
                        if source not in plan.get("sources", []):
                            plan.update(
                                state="cancelled",
                                sources=(plan.get("sources", []) + [source])[-16:],
                            )
                            # A tombstone also cancels settings whose first
                            # school query is still running and not saved yet.
                            data[key] = plan
                            await self.write(data)
                    message = self.describe(plan)
                else:
                    plan = await self.create(bot, sender, text, source, now)
                    message = self.describe(plan)
        except ValueError as exc:
            message = str(exc)
        except Exception:
            message = "暂时无法确认提醒状态，请发送“空位提醒状态”核对后再试。"
        await ctx.reply(
            card_chain(Card("图书馆空位提醒", "", (escape(message),)).pages()[0])
        )
        return True

    @staticmethod
    def message_source(ctx, now):
        from langbot_plugin.api.entities.builtin.platform.message import Source

        sources = [
            item for item in ctx.event.message_chain.root if isinstance(item, Source)
        ]
        if (
            len(sources) != 1
            or not sources[0].id
            # The SDK rebuilds Source.time from epoch as a naive local datetime.
            or not 0 <= now.timestamp() - sources[0].time.timestamp() < 120
        ):
            raise ValueError("消息来源无法核验或已过期，请重新发送。")
        return str(sources[0].id)

    async def create(self, bot, sender, text, source, now):
        words = shlex.split(text)
        if not 5 <= len(words) <= 7 or words[0] != "设置空位提醒":
            raise ValueError(
                '格式：设置空位提醒 日期 "区域" 开始 结束 [座位号或全部] [5或10]'
            )
        day, location, start, end = words[1:5]
        target = (
            (now + timedelta(days=int(day == "明天"))).date().isoformat()
            if day in {"今天", "明天"}
            else day
        )
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target) or not all(
            re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in (start, end)
        ):
            raise ValueError("请填写日期及HH:MM格式的开始、结束时间。")
        beginning = datetime.fromisoformat(target + "T" + start).replace(tzinfo=ZONE)
        if (
            not now < beginning <= now + timedelta(days=30)
            or start >= end
            or not 1 <= len(location) <= 120
        ):
            raise ValueError("请填写区域和未来30天内的使用时段，结束须晚于开始。")
        seat = words[5] if len(words) >= 6 and words[5] != "全部" else ""
        if seat:
            seat_identity(seat)
        interval = int(words[6]) if len(words) == 7 else 10
        if interval not in {5, 10}:
            raise ValueError("检查间隔仅支持5或10分钟。")
        key = self.scope(bot, sender)
        async with self.lock:
            data = await self.read()
            previous = data.get(key, {})
            if source in previous.get("sources", []):
                return previous
            if previous.get("state") == "active" and not self.expired(previous, now):
                raise ValueError(
                    "已有空位提醒，请先发送“取消空位提醒”，再设置新的提醒。"
                )
            # Claim the setting's Source before I/O. Cancellation keeps this
            # receipt, including across restarts, so a replay cannot reactivate it.
            preparing = dict(
                previous,
                state="preparing",
                version=secrets.token_hex(8),
                sources=(previous.get("sources", []) + [source])[-16:],
            )
            data[key] = preparing
            await self.write(data)
        plan = dict(
            target_date=target,
            location=location,
            start=start,
            end=end,
            seat_no=seat,
            interval_minutes=interval,
        )
        started = time.monotonic()
        try:
            async with saved_session(self.plugin, sender) as snapshot:
                result = await self.query(snapshot, plan, resolve=True)
                checked_at = now + timedelta(seconds=time.monotonic() - started)
                if self.expired(plan, checked_at):
                    raise ValueError("已到使用开始时间，请重新选择未来的使用时段。")
                if seat:
                    plan["seat_no"] = next(iter(result["observed"].values()))["no"]
                plan.update(
                    bot=bot,
                    sender=sender,
                    area=result["area"],
                    session_pin=snapshot.pin,
                    state="active",
                    version=secrets.token_hex(8),
                    observed={
                        identity: row["state"]
                        for identity, row in result["observed"].items()
                    },
                    sources=(previous.get("sources", []) + [source])[-16:],
                    next_check=(checked_at + timedelta(minutes=interval)).isoformat(),
                    last_checked=checked_at.isoformat(),
                )
                free = [
                    row["no"]
                    for row in result["observed"].values()
                    if row["state"] == "free"
                ]
                plan["last_result"] = (
                    "当前空闲：" + "、".join(free[:20])
                    if free
                    else "部分座位状态未知"
                    if any(
                        row["state"] == "unknown" for row in result["observed"].values()
                    )
                    else "当前没有空位"
                )
                async with self.lock:
                    data = await self.read()
                    if data.get(key, {}) != preparing:
                        raise ValueError("提醒设置已变化，请查询状态后再设置。")
                    data[key] = plan
                    await self.write(data)
        except BaseException:
            async with self.lock:
                data = await self.read()
                if data.get(key, {}) == preparing:
                    data[key] = dict(
                        previous,
                        state=previous.get("state", "none"),
                        sources=preparing["sources"],
                    )
                    await self.write(data)
            raise
        return plan

    @staticmethod
    def expired(plan, now):
        return now >= datetime.fromisoformat(
            plan["target_date"] + "T" + plan["start"]
        ).replace(tzinfo=ZONE)

    @staticmethod
    def describe(plan):
        text = {
            "none": "暂无空位提醒",
            "active": "已开启",
            "cancelled": "已取消",
            "expired": "已到使用开始时间，提醒已停止",
            "paused": "已暂停",
            "preparing": "正在设置，可稍后查询；若已中断请重新设置或取消",
        }.get(plan.get("state"), "状态未知")
        if plan.get("area"):
            text += (
                f" · 每{plan['interval_minutes']}分钟检查\n"
                f"{plan['area']['campus']} · {plan['area']['location']}\n"
                f"范围：{plan['seat_no'] + '号座位' if plan['seat_no'] else '整个区域'}\n"
                f"{plan['target_date']} {plan['start']}–{plan['end']}\n"
                "新空位继续提醒，同一批空位不重复提醒；到使用开始时停止。"
            )
        if plan.get("last_result"):
            text += "\n最近检查：" + plan["last_result"]
        if plan.get("last_delivery"):
            text += (
                "\n最近通知："
                + {
                    "sent": "接口已受理，请留意QQ通知",
                    "unknown": "发送结果未确认，该批空位不自动重发",
                }[plan["last_delivery"]]
            )
        return text

    async def tick(self, now=None, *, wait=True):
        started = time.monotonic()
        now = now or datetime.now(ZONE)
        async with self.lock:
            data = await self.read()
            checked_at = now + timedelta(seconds=time.monotonic() - started)
            due = []
            changed = False
            for key, plan in sorted(
                data.items(), key=lambda item: item[1].get("next_check", "")
            ):
                if plan.get("state") == "active" and self.expired(plan, checked_at):
                    plan["state"] = "expired"
                    changed = True
                if (
                    plan.get("state") == "active"
                    and key not in self.workers
                    and len(self.workers) + len(due) < 4
                    and checked_at >= datetime.fromisoformat(plan["next_check"])
                ):
                    plan["next_check"] = (
                        checked_at + timedelta(minutes=plan["interval_minutes"])
                    ).isoformat()
                    due.append((key, dict(plan)))
            if due or changed:
                await self.write(data)
            tasks = []
            for key, plan in due:
                task = create_background_task(
                    self.plugin, self.check(key, plan, now, started)
                )
                self.workers[key] = task
                task.add_done_callback(
                    lambda done, key=key: self.worker_done(key, done)
                )
                tasks.append(task)
        if wait and tasks:
            await asyncio.shield(asyncio.gather(*tasks))

    def worker_done(self, key, task):
        if self.workers.get(key) is task:
            self.workers.pop(key, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Library alert worker failed; next check remains scheduled")

    async def query(self, snapshot, plan, *, resolve=False):
        async with self.query_slots:
            worker = create_background_task(
                self.plugin,
                asyncio.to_thread(query_snapshot, snapshot, plan, resolve=resolve),
            )
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                await worker
                raise

    async def check(self, key, plan, now, started):
        try:
            if self.expired(plan, now + timedelta(seconds=time.monotonic() - started)):
                await self.record_result(key, plan, now, started)
                return
            async with saved_session(
                self.plugin, plan["sender"], plan["session_pin"]
            ) as snapshot:
                if self.expired(
                    plan, now + timedelta(seconds=time.monotonic() - started)
                ):
                    await self.record_result(key, plan, now, started)
                    return
                try:
                    result = await self.query(snapshot, plan)
                except AlertQueryError:
                    raise
                except Exception:
                    await self.record_result(
                        key, plan, now, started, error="查询失败，座位状态未知"
                    )
                    return
                await self.record_result(
                    key, plan, now, started, observed=result["observed"]
                )
        except AlertQueryError as exc:
            await self.record_result(
                key,
                plan,
                now,
                started,
                error=str(exc),
                pause=exc.code in {"auth_required", "session_changed", "campus_paused"},
            )
        except Exception:
            logger.warning(
                "Library alert check unavailable; existing observation retained"
            )

    async def record_result(
        self, key, plan, now, started, *, observed=None, error="", pause=False
    ):
        card = None
        async with self.lock:
            data = await self.read()
            effective_now = now + timedelta(seconds=time.monotonic() - started)
            current = data.get(key, {})
            if (
                current.get("state") != "active"
                or current.get("version") != plan["version"]
            ):
                return
            if self.expired(current, effective_now):
                current["state"] = "expired"
            elif pause:
                current.update(state="paused", last_result=error)
                card = Card(
                    "空位提醒已暂停",
                    "",
                    (escape(error), "请手动查询图书馆座位后重新设置提醒。"),
                )
            elif error:
                current["last_result"] = "状态未知 · " + error
            else:
                previous = current["observed"]
                fresh = [
                    row["no"]
                    for identity, row in observed.items()
                    if row["state"] == "free" and previous.get(identity) != "free"
                ]
                previous.update(
                    {
                        identity: row["state"]
                        for identity, row in observed.items()
                        if row["state"] != "unknown"
                    }
                )
                current["last_result"] = (
                    "部分座位状态未知"
                    if any(r["state"] == "unknown" for r in observed.values())
                    else f"已查询 · 空闲{sum(r['state'] == 'free' for r in observed.values())}座"
                )
                if fresh:
                    card = Card(
                        "图书馆有新空位",
                        "持续监控",
                        (
                            escape(
                                plan["area"]["campus"]
                                + " · "
                                + plan["area"]["location"]
                            ),
                            escape(
                                plan["target_date"]
                                + " "
                                + plan["start"]
                                + "–"
                                + plan["end"]
                            ),
                            "新空位："
                            + escape("、".join(fresh[:20]))
                            + (f" 等{len(fresh)}座" if len(fresh) > 20 else ""),
                            "请手动查座并预约，空位可能变化。",
                        ),
                    )
            current["last_checked"] = effective_now.isoformat()
            if current["state"] == "active":
                current["next_check"] = (
                    effective_now + timedelta(minutes=current["interval_minutes"])
                ).isoformat()
            if card:
                # Persist observations and the delivery claim before any send.
                # A lost receipt is never replayed for the same observed batch.
                current.update(
                    last_delivery="unknown", delivery_id=secrets.token_hex(8)
                )
                delivery_id = current["delivery_id"]
            await self.write(data)
        if card:
            # Storage can take longer than the remaining watch window. A claim
            # does not authorize initiating a notification after that window.
            if self.expired(plan, now + timedelta(seconds=time.monotonic() - started)):
                async with self.lock:
                    data = await self.read()
                    current = data.get(key, {})
                    if current.get("delivery_id") == delivery_id and current.get(
                        "state"
                    ) in {"active", "paused"}:
                        current["state"] = "expired"
                        current.pop("last_delivery", None)
                        await self.write(data)
                return
            try:
                await asyncio.wait_for(
                    self.plugin.send_message(
                        plan["bot"],
                        "person",
                        plan["sender"],
                        card_chain(card.pages()[0]),
                    ),
                    timeout=15,
                )
            except Exception:
                logger.warning(
                    "Library alert delivery unconfirmed; this batch will not be resent"
                )
                return
            async with self.lock:
                data = await self.read()
                current = data.get(key, {})
                if current.get("delivery_id") == delivery_id:
                    current["last_delivery"] = "sent"
                    await self.write(data)

    def start(self):
        if self.task is None or self.task.done():
            self.task = create_background_task(self.plugin, self.run())

    async def run(self):
        while True:
            try:
                await self.tick(wait=False)
            except Exception:
                logger.warning("Library alert scan unavailable")
            await asyncio.sleep(30)
