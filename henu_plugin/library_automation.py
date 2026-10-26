"""Explicit, durable one-shot library plans; fail closed and never retry a write."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from henu_plugin.kit_client import KitError, kit_request, kit_settings
from henu_plugin.background import create_background_task

ZONE = ZoneInfo("Asia/Shanghai")
KEY = "library_automation:v1"
logger = logging.getLogger(__name__)


async def lifetime_account(bot, sender):
    settings = kit_settings()
    if not settings or bot != settings["bot_uuid"] or not sender:
        raise KitError("UNAVAILABLE")
    # Membership must be supplied by the authenticated Kit service, never a model.
    result = await asyncio.to_thread(
        kit_request, settings, "resolve", {"subject": sender}
    )
    if result.get("bound") is not True:
        raise KitError("NOT_BOUND")
    from uuid import UUID

    try:
        uid = UUID(str(result.get("user_id", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise KitError("UNAVAILABLE") from exc
    from henu_plugin.kit_entitlement import entitlement_settings, membership_request

    if not uid.int or not await asyncio.to_thread(
        membership_request, entitlement_settings(), str(uid)
    ):
        raise KitError("LIFETIME_REQUIRED")
    return str(uid)


def validate_campus_state(profile, attempt, refresh):
    """Explicit failed/uncertain or paused authentication cannot run unattended."""
    records = [attempt] + [
        profile.get(key, {})
        for key in ("last_login_attempt", "login_verification", "saved_login_guard")
    ]
    blocked = "校园登录已暂停或尚未确认，请先私聊手动验证登录并查询图书馆座位。"
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(blocked)
        if (
            record.get("status") not in (None, "", "verified", "success")
            or ("verified" in record and record["verified"] is not True)
            or (
                record.get("subject_student_id")
                and str(record["subject_student_id"]).strip()
                != str(profile.get("student_id", "")).strip()
            )
            or record.get("error_code")
            or record.get("paused")
            or float(record.get("next_at", 0)) > time.time()
        ):
            raise ValueError(blocked)
    if refresh and refresh.get("paused") is not False:
        raise ValueError(
            "校园自动登录已暂停，请先核对学校账号状态，再手动成功同步课表后重试。"
        )


async def check_campus_state(plugin, paths, profile):
    from henu_plugin.reminder_schedule import _read_guard

    path = paths.profile_file.with_name("login_attempt.json")
    attempt = json.loads(path.read_text()) if path.exists() else {}
    subject = str(profile.get("student_id", "")).strip()
    refresh = await _read_guard(
        plugin, "schedule_refresh:v1:" + hashlib.sha256(subject.encode()).hexdigest()
    )
    validate_campus_state(profile, attempt, refresh)


async def campus_account(plugin, sender):
    from henu_mcp.core.secure_storage import load_encrypted_profile
    from henu_plugin.storage_adapter import PluginStorageAdapter

    adapter = PluginStorageAdapter(plugin, sender, read_only=True)
    try:
        paths = await adapter.load_all()
        profile = load_encrypted_profile(paths.profile_file)
        subject = str(profile.get("student_id", "")).strip()
        if not subject or not profile.get("password"):
            raise ValueError("请先绑定校园账号，再设置自动预约。")
        await check_campus_state(plugin, paths, profile)
        return subject
    finally:
        await adapter.abort()


async def reserve_plan(sender, plan):
    from henu_plugin.storage_adapter import PluginStorageAdapter

    plugin = plan.pop("_plugin")
    adapter = PluginStorageAdapter(plugin, sender)
    try:
        paths = await adapter.load_all()
        from henu_mcp.core.secure_storage import load_encrypted_profile

        profile = load_encrypted_profile(paths.profile_file)
        if str(profile.get("student_id", "")).strip() != plan["campus_subject"]:
            return {"success": False, "account_changed": True}
        await check_campus_state(plugin, paths, profile)
        if datetime.now(ZONE) > datetime.fromisoformat(plan["run_at"]) + timedelta(
            seconds=90
        ):
            return {"success": False, "missed": True}
        result = await asyncio.to_thread(_reserve_saved_plan, paths, plan)
        await adapter.save_all()
        return result
    finally:
        await adapter.abort()


def _reserve_saved_plan(paths, plan):
    """Use an isolated client and an ownership-proven saved session.

    Unmarked legacy jars fail closed. This path must never assign an owner to
    an existing token; fresh canonical installs need cookie-provenance parity
    in the trusted login/save path before scheduled writes can be enabled.
    """
    from campus_core.bot import HenuCampusBot

    cookies = json.loads(paths.library_cookie_file.read_text())
    subject = str(plan.get("campus_subject") or "").strip()
    if (not isinstance(cookies, dict) or not subject
            or str(cookies.get("_subject_student_id") or "").strip() != subject):
        return {"success": False, "account_changed": True, "not_submitted": True}
    cookies = {key: value for key, value in cookies.items()
               if key == "_v4_token" or not str(key).startswith("_")}
    bot = HenuCampusBot(subject, "", cookies)
    bot._saved_session_only = True
    try:
        if not bot._is_token_valid():
            return {"success": False}
        result = bot.reserve(
            plan["location"],
            plan["seat_no"],
            plan["target_date"],
            preferred_time=plan["start"],
            preferred_end_time=plan["end"],
            max_attempts=1,
            retry_until="",
            strict_window=True,
            submit_before=(
                datetime.fromisoformat(plan["run_at"]) + timedelta(seconds=90)
            ).isoformat(),
        )
        paths.library_cookie_file.write_text(json.dumps({
            **bot.get_cookies(), "_subject_student_id": subject,
        }))
        return result
    finally:
        bot.session.close()


class LibraryAutomation:
    def __init__(
        self, plugin, *, authorize=lifetime_account, reserve=None, campus_subject=None
    ):
        self.plugin = plugin
        self.authorize = authorize
        self.reserve = reserve or self._reserve
        self.campus_subject = campus_subject or (
            lambda sender: campus_account(plugin, sender)
        )
        if not hasattr(plugin, "_library_automation_lock"):
            plugin._library_automation_lock = asyncio.Lock()
        self.lock = plugin._library_automation_lock
        if not hasattr(plugin, "_library_automation_workers"):
            plugin._library_automation_workers = {}
        self.workers = plugin._library_automation_workers
        self.task = None

    async def _reserve(self, sender, plan):
        return await reserve_plan(sender, dict(plan, _plugin=self.plugin))

    @staticmethod
    def scope(bot, sender):
        if not bot or not sender or sender in {"0", "unknown", "None"}:
            raise ValueError("无法确认聊天身份")
        return hashlib.sha256(json.dumps([bot, sender]).encode()).hexdigest()

    async def read(self):
        from langbot_plugin.entities.io.errors import ActionCallError

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
            raise ValueError("invalid plan registry")
        return data

    async def write(self, data):
        await self.plugin.set_plugin_storage(
            KEY, json.dumps(data, ensure_ascii=False).encode()
        )

    @staticmethod
    def validate(args, now):
        if set(args) != {
            "run_at",
            "target_date",
            "location",
            "seat_no",
            "start",
            "end",
        }:
            raise ValueError("请提供完整执行时间、预约日期、区域、座位、开始和结束时间")
        plan = dict(args)
        if any(
            not isinstance(v, str) or not v.strip() or len(v) > 120
            for v in plan.values()
        ):
            raise ValueError("预约参数无效")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", plan["run_at"]):
            raise ValueError("执行时间格式：2026-10-28T07:00（北京时间）")
        run_at = datetime.fromisoformat(plan["run_at"]).replace(tzinfo=ZONE)
        if not now < run_at <= now + timedelta(days=30):
            raise ValueError("执行时间须在未来30天内")
        for name in ("start", "end"):
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", plan[name]):
                raise ValueError("预约时段格式为 HH:MM")
        start = datetime.fromisoformat(
            plan["target_date"] + "T" + plan["start"]
        ).replace(tzinfo=ZONE)
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan["target_date"])
            or start <= run_at
            or plan["start"] >= plan["end"]
        ):
            raise ValueError("须在使用开始前执行，且结束时间晚于开始时间")
        plan["run_at"] = run_at.isoformat()
        return plan

    async def prepare(self, bot, sender, args, source, now=None):
        now = now or datetime.now(ZONE)
        key = self.scope(bot, sender)
        plan = self.validate(args, now)
        uid = await self.authorize(bot, sender)
        subject = await self.campus_subject(sender)
        async with self.lock:
            data = await self.read()
            old = data.get(key, {})
            if old.get("state") in {"scheduled", "attempting"}:
                raise ValueError("已有活动计划：待执行可取消；结果待核对请先查询自动预约状态并按提示解除")
            if old.get("source") == source:
                return old
            plan.update(
                bot=bot,
                sender=sender,
                user_id=uid,
                campus_subject=subject,
                state="pending",
                source=source,
                token=secrets.token_hex(8),
                expires=(now + timedelta(minutes=10)).isoformat(),
            )
            data[key] = plan
            await self.write(data)
            return plan

    async def confirm(self, bot, sender, token, source, now=None):
        now = now or datetime.now(ZONE)
        key = self.scope(bot, sender)
        uid = await self.authorize(bot, sender)
        subject = await self.campus_subject(sender)
        async with self.lock:
            data = await self.read()
            plan = data.get(key, {})
            if (
                plan.get("state") != "pending"
                or not secrets.compare_digest(str(plan.get("token", "")), token)
                or plan.get("source") == source
                or uid != plan.get("user_id")
                or subject != plan.get("campus_subject")
                or now >= datetime.fromisoformat(plan["expires"])
                or now >= datetime.fromisoformat(plan["run_at"])
            ):
                raise ValueError("计划确认已失效，请重新设置")
            plan.update(state="scheduled")
            plan.pop("token", None)
            await self.write(data)
            return plan

    async def cancel(self, bot, sender):
        async with self.lock:
            data = await self.read()
            plan = data.get(self.scope(bot, sender))
            if plan and plan["state"] in {"pending", "scheduled"}:
                plan["state"] = "cancelled"
                await self.write(data)
            return plan or {"state": "none"}

    async def release(self, bot, sender, token):
        """Acknowledge an uncertain result, never cancel or repeat a school write."""
        key = self.scope(bot, sender)
        async with self.lock:
            data = await self.read()
            plan = data.get(key, {})
            worker = self.workers.get(key)
            if worker is not None and not worker.done():
                raise ValueError("请求仍在执行，暂不能解除，请稍后查询状态。")
            if (
                plan.get("state") != "attempting"
                or not token
                or not secrets.compare_digest(
                    str(plan.get("recovery_token", "")), token
                )
            ):
                raise ValueError("解除确认已失效，请重新查询自动预约状态。")
            plan.update(
                state="released_unknown", released_at=datetime.now(ZONE).isoformat()
            )
            await self.write(data)
            return plan

    async def status(self, bot, sender):
        async with self.lock:
            data = await self.read()
            plan = data.get(self.scope(bot, sender), {"state": "none"})
            # Upgrade legacy crash claims only on explicit status inspection.
            if plan.get("state") == "attempting" and not plan.get("recovery_token"):
                plan["recovery_token"] = secrets.token_hex(8)
                await self.write(data)
            return plan

    async def tick(self, now=None, *, wait=True):
        now = now or datetime.now(ZONE)
        started = time.monotonic()
        tasks = []
        async with self.lock:
            data = await self.read()
            for key, plan in data.items():
                if plan.get("state") != "scheduled":
                    continue
                worker = self.workers.get(key)
                if worker is not None and not worker.done():
                    continue
                if now < datetime.fromisoformat(plan["run_at"]):
                    continue
                task = create_background_task(
                    self.plugin, self._execute(key, dict(plan), now, started)
                )
                self.workers[key] = task
                task.add_done_callback(
                    lambda done, key=key: self._worker_done(key, done)
                )
                tasks.append(task)
        # Cancellation of a caller/scan must not detach a running school write.
        if wait and tasks:
            await asyncio.shield(asyncio.gather(*tasks))

    def _worker_done(self, key, task):
        if self.workers.get(key) is task:
            self.workers.pop(key, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "Library plan worker failed; claimed writes will not be retried"
            )

    async def _execute(self, key, plan, now, started):
        def expired():
            return now + timedelta(seconds=time.monotonic() - started) > (
                datetime.fromisoformat(plan["run_at"]) + timedelta(seconds=90)
            )

        # Per-user authorization and school I/O never hold the registry lock.
        state = "missed"
        if not expired():
            try:
                uid = await self.authorize(plan["bot"], plan["sender"])
                subject = await self.campus_subject(plan["sender"])
                if uid != plan["user_id"] or subject != plan["campus_subject"]:
                    raise KitError("ACCOUNT_CHANGED")
                state = "missed" if expired() else "attempting"
            except Exception:
                state = "authorization_failed"
        async with self.lock:
            data = await self.read()
            current = data.get(key, {})
            if current.get("state") != "scheduled" or current != plan:
                return  # Cancelled/replaced while authorization was pending.
            current["state"] = state
            if state == "attempting":
                current["recovery_token"] = claim_token = secrets.token_hex(8)
            # No school request on a failed or ambiguous claim commit.
            await self.write(data)
        if state != "attempting":
            return
        try:
            result = (
                {"missed": True}
                if expired()
                else await self.reserve(plan["sender"], dict(plan))
            )
            if result.get("account_changed") is True:
                state = "authorization_failed"
            elif result.get("missed") is True:
                state = "missed"
            elif result.get("success") is True:
                state = "success"
            elif result.get("submit_success") is True:
                state = "submitted_unverified"
            else:
                state = "failed_or_unknown"
        except Exception:
            state = "failed_or_unknown"
        async with self.lock:
            # Reload: other users may have changed the registry during school I/O.
            data = await self.read()
            current = data.get(key, {})
            if (current.get("state") == "attempting"
                    and current.get("recovery_token") == claim_token):
                current["state"] = state
                await self.write(data)

    async def handle(self, ctx, *, is_group=False):
        import shlex

        from langbot_plugin.api.entities.builtin.platform.message import Plain, Source

        from henu_plugin.qq_cards import reply_card

        chain = getattr(getattr(ctx.event, "message_chain", None), "root", [])
        text = str(
            getattr(ctx.event, "text_message", "")
            or "".join(item.text for item in chain if isinstance(item, Plain))
        ).strip()
        if not text.startswith(
            (
                "设置自动预约",
                "确认自动预约",
                "取消自动预约",
                "自动预约状态",
                "自动预约帮助",
                "解除自动预约",
            )
        ):
            return False
        ctx.prevent_default()
        ctx.prevent_postorder()
        try:
            if is_group:
                raise ValueError("请在机器人私聊中设置或查询自动预约。")
            bot = str(await ctx.get_bot_uuid() or "")
            sender = str(getattr(ctx.event, "sender_id", "") or "")
            if str(getattr(ctx.event, "launcher_id", "") or "") != sender:
                raise ValueError("无法确认私聊身份，请重新发送。")
            self.scope(bot, sender)
            words = shlex.split(text)
            if words == ["自动预约帮助"]:
                message = (
                    "仅限已绑定 Kit 的终身会员。每个用户可设置一个单次计划。\n"
                    "请先手动查询图书馆座位。定时执行仅复用有效登录态；失效或登录已暂停时停止，不会自动提交校园密码。\n"
                    '设置自动预约 执行时间 预约日期 "区域" 座位 开始 结束\n'
                    "格式：执行时间 YYYY-MM-DDTHH:MM；预约日期 YYYY-MM-DD；时段 HH:MM（均为北京时间）。\n"
                    "核对后发送“确认自动预约 确认码”。可发送“自动预约状态”或“取消自动预约”。\n"
                    "结果待核对的计划：先查询学校预约记录，再按状态提示发送解除命令。解除不会取消学校预约，也不会重试原计划。"
                )
            elif words == ["自动预约状态"] or words == ["取消自动预约"]:
                plan = await (
                    self.cancel(bot, sender)
                    if words[0] == "取消自动预约"
                    else self.status(bot, sender)
                )
                message = self.describe(plan)
            else:
                sources = [
                    item
                    for item in getattr(
                        getattr(ctx.event, "message_chain", None), "root", []
                    )
                    if isinstance(item, Source)
                ]
                if len(sources) != 1:
                    raise ValueError("无法核验消息来源，请重新发送。")
                source = str(sources[0].id or "")
                stamp = sources[0].time
                if isinstance(stamp, datetime):
                    stamp = stamp.timestamp()
                now = datetime.now(ZONE)
                if (
                    not source
                    or isinstance(stamp, bool)
                    or not isinstance(stamp, (int, float))
                    or not 0 <= now.timestamp() - stamp < 120
                ):
                    raise ValueError("消息已过期，请重新发送。")
                if words[0] == "设置自动预约" and len(words) == 7:
                    args = dict(
                        zip(
                            (
                                "run_at",
                                "target_date",
                                "location",
                                "seat_no",
                                "start",
                                "end",
                            ),
                            words[1:],
                        )
                    )
                    plan = await self.prepare(bot, sender, args, source, now)
                    message = (
                        self.describe(plan)
                        + "\n确认后将按此计划提交一次预约。10分钟内另发“确认自动预约 "
                        + plan["token"]
                        + "”。"
                    )
                elif (
                    words[0] == "解除自动预约"
                    and len(words) == 3
                    and words[2] == "已核对"
                ):
                    plan = await self.release(bot, sender, words[1])
                    message = self.describe(plan)
                elif words[0] == "确认自动预约" and len(words) == 2:
                    plan = await self.confirm(bot, sender, words[1], source, now)
                    message = self.describe(plan)
                else:
                    raise ValueError("命令格式不正确，请发送“自动预约帮助”。")
        except KitError as exc:
            message = {
                "NOT_BOUND": "请先发送“绑定 HENU KIT”完成账号绑定。",
                "LIFETIME_REQUIRED": "定时自动预约仅向 Kit 终身会员开放。",
            }.get(exc.code, "暂时无法核验 Kit 终身会员资格，未启用预约，请稍后重试。")
        except ValueError as exc:
            message = str(exc)
        except Exception:
            message = (
                "暂时无法确认自动预约状态，请先发送“自动预约状态”核对，勿重复设置。"
            )
        await reply_card(ctx, "图书馆自动预约", message, plugin=self.plugin)
        return True

    @staticmethod
    def describe(plan):
        states = {
            "none": "暂无计划",
            "pending": "待确认",
            "scheduled": "已启用",
            "cancelled": "已取消",
            "missed": "已错过执行时间，请重新设置",
            "authorization_failed": "会员、绑定或校园登录状态核验失败，未执行，请重新核验后设置",
            "attempting": "执行结果待核对，请查询图书馆预约记录，勿重复提交",
            "released_unknown": "已解除结果不确定的计划；未取消学校预约，未重试原计划。新计划须另行设置并确认",
            "success": "预约成功",
            "submitted_unverified": "学校已接受提交，结果待核对，请查询预约记录",
            "failed_or_unknown": "未确认预约成功，请查询预约记录，系统不会自动重试",
        }
        text = states.get(plan.get("state"), "状态未知，请查询预约记录")
        if plan.get("run_at"):
            text += (
                "\n执行："
                + plan["run_at"].replace("T", " ")
                + "\n预约："
                + plan["target_date"]
                + " "
                + plan["start"]
                + "–"
                + plan["end"]
                + "\n区域："
                + plan["location"]
                + " · 座位："
                + plan["seat_no"]
            )
        if plan.get("state") == "attempting" and plan.get("recovery_token"):
            text += (
                "\n请先查询学校预约记录，确认后发送“解除自动预约 "
                + plan["recovery_token"]
                + " 已核对”。请求仍在执行时不能解除。"
                "解除不代表预约失败，不会取消学校预约或重试原计划。"
            )
        return text

    def start(self):
        if self.task is None or self.task.done():
            self.task = create_background_task(self.plugin, self.run())

    async def run(self):
        while True:
            try:
                await self.tick(wait=False)
            except Exception:
                logger.warning(
                    "Library plan scan failed; no automatic retry of claimed writes"
                )
            await asyncio.sleep(15)
