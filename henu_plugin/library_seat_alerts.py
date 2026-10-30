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
from henu_plugin.qq_cards import card_chain
from henu_plugin.library_alert_cards import (
    help_card,
    status_card,
    new_seats_card,
    paused_card,
    error_card,
    booking_confirmation_card,
)

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

    async def handle(
        self, ctx, text, *, now=None, expected_version="", from_button=False
    ):
        controls = {
            "空位提醒",
            "空位提醒帮助",
            "空位提醒状态",
            "取消空位提醒",
            "有位自动预约",
            "保持仅提醒",
        }
        if text not in controls and not text.startswith(
            ("设置空位提醒", "确认有位自动预约")
        ):
            return False
        if getattr(ctx, "is_prevent_postorder", False) and not from_button:
            return True
        started = time.monotonic()
        now = now or datetime.now(ZONE)
        specs = []
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
                card = help_card()
            elif text == "空位提醒状态":
                card = status_card(await self.status(bot, sender))
            else:
                source = self.message_source(ctx, now)
                if text == "取消空位提醒":
                    async with self.lock:
                        data = await self.read()
                        key = self.scope(bot, sender)
                        plan = data.get(key, {"state": "none"})
                        if source not in plan.get("sources", []):
                            plan["sources"] = (plan.get("sources", []) + [source])[-16:]
                            plan.pop("booking_pending", None)
                            plan.pop("auto_reserve", None)
                            if plan.get("state") in {
                                "booking",
                                "reserved",
                                "submitted_unverified",
                                "reservation_unknown",
                                "reservation_failed",
                            }:
                                plan["cancel_requested"] = True
                            else:
                                plan["state"] = "cancelled"
                            # A tombstone also cancels settings whose first
                            # school query is still running and not saved yet.
                            data[key] = plan
                            await self.write(data)
                    card = status_card(plan)
                elif text == "有位自动预约":
                    plan = await self.prepare_booking(
                        bot, sender, source, now, started, expected_version
                    )
                    card = booking_confirmation_card(plan)
                    specs = self.booking_actions(plan, confirming=True)
                elif text.startswith("确认有位自动预约"):
                    card = status_card(
                        await self.confirm_booking(
                            bot, sender, text, source, now, started, expected_version
                        )
                    )
                elif text == "保持仅提醒":
                    async with self.lock:
                        data = await self.read()
                        plan = data.get(self.scope(bot, sender), {"state": "none"})
                        if expected_version and plan.get("version") != expected_version:
                            raise ValueError("这张设置卡已失效，请重新查询提醒状态。")
                        if plan.get("state") == "active":
                            plan.pop("booking_pending", None)
                            plan.pop("auto_reserve", None)
                            plan["version"] = secrets.token_hex(8)
                            plan["sources"] = (plan.get("sources", []) + [source])[-16:]
                            data[self.scope(bot, sender)] = plan
                            await self.write(data)
                    card = status_card(plan)
                else:
                    plan = await self.create(bot, sender, text, source, now, started)
                    card = status_card(plan, offer_booking=True)
                    if plan.get("state") == "active" and not plan.get("auto_reserve"):
                        specs = self.booking_actions(plan)
        except ValueError as exc:
            card = error_card(str(exc))
        except Exception:
            card = error_card("暂时无法确认提醒状态，请先核对后再试。", uncertain=True)
        page = card.pages()[0]
        if specs and getattr(ctx, "query_id", None) is not None:
            from henu_plugin.qq_buttons import actions, envelope

            page = envelope(page, await actions(self.plugin, ctx, specs))
        await ctx.reply(card_chain(page))
        return True

    @staticmethod
    def booking_actions(plan, *, confirming=False):
        first = (
            {"kind": "alert_booking_confirm", "token": plan["booking_pending"]["token"]}
            if confirming
            else {"kind": "alert_booking_prepare"}
        )
        return [
            (
                "确认自动预约" if confirming else "有位自动约",
                {**first, "version": plan["version"]},
            ),
            ("仅提醒", {"kind": "alert_booking_off", "version": plan["version"]}),
        ]

    async def handle_action(self, ctx, payload):
        command = {
            "alert_booking_prepare": "有位自动预约",
            "alert_booking_off": "保持仅提醒",
            "alert_booking_confirm": "确认有位自动预约 "
            + str(payload.get("token", "")),
        }.get(payload.get("kind"))
        if command:
            await self.handle(
                ctx,
                command,
                expected_version=payload.get("version", ""),
                from_button=True,
            )

    async def booking_member(self, bot, sender):
        from henu_plugin.library_automation import lifetime_account
        from henu_plugin.kit_client import KitError

        try:
            return await lifetime_account(bot, sender)
        except KitError as exc:
            raise ValueError(
                {
                    "NOT_BOUND": "请先绑定Kit；有位自动预约仅限 HENU-Kit 终身会员。",
                    "LIFETIME_REQUIRED": "有位自动预约仅限 HENU-Kit 终身会员，当前仍仅提醒。",
                }.get(exc.code, "会员状态暂时无法核验，未开启自动预约。")
            ) from exc

    async def prepare_booking(
        self, bot, sender, source, now, started, expected_version=""
    ):
        from henu_plugin.library_alert_booking import require_owned_session

        plan = await self.status(bot, sender)
        if plan.get("state") != "active" or self.expired(plan, now):
            raise ValueError("请先设置未来时段的空位提醒。")
        if plan.get("auto_reserve"):
            raise ValueError("已授权有位自动预约，发送“空位提醒状态”查看。")
        if expected_version and plan["version"] != expected_version:
            raise ValueError("这张设置卡已失效，请重新查询提醒状态。")
        uid = await self.booking_member(bot, sender)
        async with saved_session(self.plugin, sender, plan["session_pin"]) as snapshot:
            require_owned_session(snapshot)
            async with self.lock:
                data = await self.read()
                current = data.get(self.scope(bot, sender), {})
                if (
                    current.get("state") != "active"
                    or current.get("version") != plan["version"]
                ):
                    raise ValueError("提醒设置已变化，请重新查询状态。")
                effective_now = now + timedelta(seconds=time.monotonic() - started)
                if self.expired(current, effective_now):
                    raise ValueError("已到使用开始时间，不能再授权预约。")
                if source in current.get("sources", []):
                    raise ValueError("请在新的消息中选择有位自动预约。")
                current["booking_pending"] = dict(
                    token=secrets.token_hex(8),
                    source=source,
                    user_id=uid,
                    campus_subject=snapshot.subject,
                    session_pin=snapshot.pin,
                    expires=(effective_now + timedelta(minutes=5)).isoformat(),
                )
                current["sources"] = (current.get("sources", []) + [source])[-16:]
                await self.write(data)
                return dict(current)

    async def confirm_booking(
        self, bot, sender, text, source, now, started, expected_version=""
    ):
        from henu_plugin.library_alert_booking import require_owned_session

        plan = await self.status(bot, sender)
        pending = plan.get("booking_pending", {})
        if (
            plan.get("state") != "active"
            or not pending
            or text != "确认有位自动预约 " + pending["token"]
            or source in plan.get("sources", [])
            or now >= datetime.fromisoformat(pending["expires"])
            or (expected_version and plan.get("version") != expected_version)
            or self.expired(plan, now)
        ):
            raise ValueError("确认已失效，请重新发送“有位自动预约”并核对。")
        uid = await self.booking_member(bot, sender)
        if uid != pending["user_id"]:
            raise ValueError("Kit绑定已变化，请重新设置提醒并授权。")
        async with saved_session(
            self.plugin, sender, pending["session_pin"]
        ) as snapshot:
            require_owned_session(snapshot)
            async with self.lock:
                data = await self.read()
                current = data.get(self.scope(bot, sender), {})
                if (
                    current.get("state") != "active"
                    or current.get("version") != plan["version"]
                    or current.get("booking_pending") != pending
                ):
                    raise ValueError("提醒设置已变化，本次未授权预约。")
                effective_now = now + timedelta(seconds=time.monotonic() - started)
                if self.expired(
                    current, effective_now
                ) or effective_now >= datetime.fromisoformat(pending["expires"]):
                    raise ValueError("确认已过期，本次未授权预约。")
                current["auto_reserve"] = dict(
                    user_id=uid, campus_subject=snapshot.subject
                )
                current.pop("booking_pending", None)
                current["sources"] = (current.get("sources", []) + [source])[-16:]
                current["version"] = secrets.token_hex(8)
                await self.write(data)
                return dict(current)

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

    async def create(self, bot, sender, text, source, now, started):
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
            if previous.get("state") == "booking":
                raise ValueError("上次预约已发起且结果待核对，请先查询学校预约记录。")
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
        try:
            async with saved_session(self.plugin, sender) as snapshot:
                result = await self.query(snapshot, plan, now, started, resolve=True)
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
                    if self.expired(
                        plan, now + timedelta(seconds=time.monotonic() - started)
                    ):
                        raise ValueError("已到使用开始时间，请重新选择未来的使用时段。")
                    data[key] = plan
                    await self.write(data)
                    if self.expired(
                        plan, now + timedelta(seconds=time.monotonic() - started)
                    ):
                        plan["state"] = "expired"
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

    async def query(self, snapshot, plan, now, started, *, resolve=False):
        def expired():
            return self.expired(
                plan, now + timedelta(seconds=time.monotonic() - started)
            )

        async with self.query_slots:
            if expired():
                raise AlertQueryError("expired", "已到使用开始时间，提醒已停止。")
            worker = create_background_task(
                self.plugin,
                asyncio.to_thread(
                    query_snapshot, snapshot, plan, resolve=resolve, expired=expired
                ),
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
                    result = await self.query(snapshot, plan, now, started)
                except AlertQueryError:
                    raise
                except Exception:
                    await self.record_result(
                        key, plan, now, started, error="查询失败，座位状态未知"
                    )
                    return
                if plan.get("auto_reserve") and await self.book_available(
                    key, plan, snapshot, result["observed"], now, started
                ):
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

    async def book_available(self, key, plan, snapshot, observed, now, started):
        from henu_plugin.library_alert_booking import (
            require_owned_session,
            reserve_snapshot,
        )

        free = sorted(row["no"] for row in observed.values() if row["state"] == "free")
        if not free:
            return False
        try:
            require_owned_session(snapshot)
            uid = await self.booking_member(plan["bot"], plan["sender"])
            if (
                uid != plan["auto_reserve"]["user_id"]
                or snapshot.subject != plan["auto_reserve"]["campus_subject"]
            ):
                raise ValueError("Kit绑定或校园账号已变化，自动预约未执行。")
        except (ValueError, AlertQueryError):
            await self.record_result(
                key,
                plan,
                now,
                started,
                error="自动预约资格或登录状态无法核验，未提交；请核对后重新设置。",
                pause=True,
            )
            return True
        effective_now = now + timedelta(seconds=time.monotonic() - started)
        deadline = min(
            effective_now + timedelta(seconds=90),
            datetime.fromisoformat(plan["target_date"] + "T" + plan["start"]).replace(
                tzinfo=ZONE
            ),
        )
        async with self.lock:
            data = await self.read()
            current = data.get(key, {})
            if (
                current.get("state") != "active"
                or current.get("version") != plan["version"]
                or not current.get("auto_reserve")
            ):
                return True
            if self.expired(
                current, now + timedelta(seconds=time.monotonic() - started)
            ):
                current["state"] = "expired"
                await self.write(data)
                return True
            current.update(
                state="booking",
                booking_seat=free[0],
                booking_claim=secrets.token_hex(8),
                last_result="已发起一次预约，结果待核对。",
            )
            claim = current["booking_claim"]
            # A lost/failed durable claim never authorizes a school request.
            await self.write(data)
        try:
            worker = create_background_task(
                self.plugin,
                asyncio.to_thread(
                    reserve_snapshot,
                    snapshot,
                    plan,
                    free[0],
                    deadline,
                    lambda: (
                        now + timedelta(seconds=time.monotonic() - started) >= deadline
                    ),
                ),
            )
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Keep the user lock until the underlying single request ends.
                await worker
                raise
            if result.get("success") is True:
                state, message = "reserved", "学校当前预约记录已反查确认。"
            elif result.get("submit_success") is True:
                state, message = (
                    "submitted_unverified",
                    "学校提交接口返回成功，当前预约记录尚未确认；不重试。",
                )
            elif (
                result.get("missed") is True
                or result.get("not_submitted") is True
                or (type(result.get("code")) is int and result["code"] != 0)
            ):
                state, message = (
                    "reservation_failed",
                    "本次预约未完成，任务已停止；请先核对学校预约记录。",
                )
            else:
                state, message = (
                    "reservation_unknown",
                    "无法确认学校预约结果，任务已停止；不会自动重试。",
                )
        except Exception:
            state, message = (
                "reservation_unknown",
                "无法确认学校预约结果，任务已停止；不会自动重试。",
            )
        async with self.lock:
            data = await self.read()
            current = data.get(key, {})
            if (
                current.get("state") != "booking"
                or current.get("booking_claim") != claim
            ):
                return True
            current.update(
                state=state,
                last_result=message,
                last_checked=(
                    now + timedelta(seconds=time.monotonic() - started)
                ).isoformat(),
                last_delivery="unknown",
                delivery_id=secrets.token_hex(8),
            )
            delivery_id = current["delivery_id"]
            await self.write(data)
            card = status_card(current)
        await self.deliver(key, plan, card, delivery_id)
        return True

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
                card = paused_card(plan, error)
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
                    card = new_seats_card(plan, fresh)
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
            await self.deliver(key, plan, card, delivery_id)

    async def deliver(self, key, plan, card, delivery_id):
        try:
            await asyncio.wait_for(
                self.plugin.send_message(
                    plan["bot"], "person", plan["sender"], card_chain(card.pages()[0])
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
