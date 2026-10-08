"""Persistent one-shot bookings, owned by one active plugin instance.

Storage provides no CAS. A single encrypted record atomically stores the bounded
index and encrypted per-subject task payloads; every state transition is serialized.
A persisted running claim precedes campus calls. A restart converts it to unknown,
never pending. Install exactly one active plugin instance for this Storage namespace.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import re
import secrets
import weakref
from zoneinfo import ZoneInfo

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes

from henu_mcp.core.secure_storage import (
    decrypt_value,
    encrypt_value,
    load_encrypted_profile,
)
from henu_plugin.cli import inspect_cli_command
from henu_plugin.confirmation import (
    conversation_context,
    create_pending_operation,
    validate_pending_operation,
)
from henu_plugin.library_schedule_cli import HELP, is_schedule_command
from henu_plugin.membership import (
    MembershipError,
    require_benefit,
    require_benefit_async,
)
from henu_plugin.service import set_current_user_paths
from henu_plugin.storage_adapter import PluginStorageAdapter
from henu_plugin.storage_reads import read_optional_storage

logger = logging.getLogger(__name__)

KEY = "library-schedule:ledger:v1"
SCHEMA = "henu.library-schedule.v1"
ZONE = ZoneInfo("Asia/Shanghai")
MAX_USERS = 512
MAX_JOBS = 2048
MAX_PER_USER = 20
MISSED_WINDOW = 90
STATUSES = {
    "pending",
    "running",
    "succeeded",
    "cancelled",
    "missed",
    "blocked",
    "unknown",
}


def result(text, *, success=True, **values):
    return {"success": success, "msg": text, "reply_text": text, **values}


def get_scheduler(plugin):
    scheduler = getattr(plugin, "_henu_library_scheduler", None)
    if scheduler is None:
        scheduler = LibraryScheduler(plugin)
        plugin._henu_library_scheduler = scheduler
    return scheduler


class LibraryScheduler:
    """Public private-message/CLI and lifecycle worker boundary."""

    def __init__(self, plugin, *, clock=None, poll_seconds=5):
        self.plugin = weakref.proxy(plugin)
        self.clock = clock or (lambda: datetime.now(ZONE))
        self.poll_seconds = max(0.05, min(30, poll_seconds))
        self.lock = getattr(plugin, "_library_schedule_lock", None)
        if self.lock is None:
            self.lock = asyncio.Lock()
            plugin._library_schedule_lock = self.lock
        self.worker = None
        self.execution_tasks = set()
        self.ready = False
        self.stopping = False
        self.seen = set()

    def _now(self):
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("invalid worker clock")
        return now.astimezone(ZONE)

    @staticmethod
    def _identity(sender, conversation):
        return (
            isinstance(sender, str)
            and sender not in {"0", "None", "none", "unknown"}
            and bool(re.fullmatch(r"[0-9A-Za-z._-]{1,128}", sender))
            and conversation.get("launcher_type") == "person"
            and bool(conversation.get("launcher_id"))
        )

    @staticmethod
    def _parameters(params, *, now=None):
        if set(params) != {
            "run_at",
            "location",
            "seat_no",
            "target_date",
            "preferred_time",
            "preferred_end_time",
        }:
            raise ValueError("参数不完整")
        if any(
            not isinstance(v, str)
            or not v
            or len(v) > 128
            or any(ord(c) < 32 for c in v)
            for v in params.values()
        ):
            raise ValueError("参数无效")
        stamp = params["run_at"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00", stamp):
            raise ValueError("执行时间须为 YYYY-MM-DDTHH:MM:SS+08:00")
        run = datetime.fromisoformat(stamp)
        usage = date.fromisoformat(params["target_date"])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", params["target_date"]):
            raise ValueError("使用日期无效")
        start, end = params["preferred_time"], params["preferred_end_time"]
        if (
            any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", v) for v in (start, end))
            or end <= start
        ):
            raise ValueError("座位时段无效，结束须晚于开始")
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,32}", params["seat_no"]):
            raise ValueError("座位号无效")
        if (
            datetime.combine(usage, datetime.strptime(start, "%H:%M").time(), ZONE)
            < run
        ):
            raise ValueError("执行时刻须早于座位使用时刻")
        if now is not None and not now < run <= now + timedelta(days=30):
            raise ValueError("执行时刻须在未来 30 天内")
        return run

    async def _read(self):
        raw = await read_optional_storage(self.plugin, KEY)
        if raw is None:
            return {"schema": SCHEMA, "users": {}}
        if not isinstance(raw, (bytes, str)) or len(raw) > 4_000_000:
            raise ValueError("invalid ledger")
        text = raw.decode() if isinstance(raw, bytes) else raw
        if not text.startswith("enc:v2:"):
            raise ValueError("unencrypted ledger")
        data = json.loads(decrypt_value(text))
        if (
            not isinstance(data, dict)
            or set(data) != {"schema", "users"}
            or data["schema"] != SCHEMA
            or not isinstance(data["users"], dict)
            or len(data["users"]) > MAX_USERS
        ):
            raise ValueError("invalid ledger")
        total = 0
        for sender, cipher in data["users"].items():
            if (
                not self._identity(
                    sender, {"launcher_type": "person", "launcher_id": sender}
                )
                or not isinstance(cipher, str)
                or not cipher.startswith("enc:v2:")
            ):
                raise ValueError("invalid subject")
            user = self._decode(cipher)
            total += len(user["jobs"])
        if total > MAX_JOBS:
            raise ValueError("ledger full")
        return data

    def _decode(self, cipher):
        user = json.loads(decrypt_value(cipher))
        if (
            not isinstance(user, dict)
            or set(user) != {"jobs", "pending"}
            or not isinstance(user["jobs"], dict)
            or len(user["jobs"]) > MAX_PER_USER
        ):
            raise ValueError("invalid user tasks")
        for task_id, job in user["jobs"].items():
            if (
                not re.fullmatch(r"[a-f0-9]{20}", task_id)
                or not isinstance(job, dict)
                or set(job)
                != {
                    "params",
                    "profile",
                    "account",
                    "status",
                    "created_at",
                    "updated_at",
                    "reason",
                }
                or job["status"] not in STATUSES
                or not re.fullmatch(r"[a-f0-9]{64}", job["profile"])
                or not re.fullmatch(r"[a-f0-9]{64}", job["account"])
                or not isinstance(job["reason"], str)
                or len(job["reason"]) > 64
            ):
                raise ValueError("invalid job")
            self._parameters(job["params"])
            for field in ("created_at", "updated_at"):
                datetime.fromisoformat(job[field])
        pending = user["pending"]
        if pending is not None:
            if (
                not isinstance(pending, dict)
                or not isinstance(pending.get("conversation"), dict)
                or not isinstance(pending.get("profile"), str)
            ):
                raise ValueError("invalid confirmation")
            self._parameters(pending["params"])
        return user

    def _user(self, data, sender):
        cipher = data["users"].get(sender)
        return self._decode(cipher) if cipher else {"jobs": {}, "pending": None}

    @staticmethod
    def _duplicate(user, params, account):
        booking = {k: v for k, v in params.items() if k != "run_at"}
        return any(
            job["account"] == account
            and job["status"] in {"pending", "running", "succeeded", "unknown"}
            and {k: v for k, v in job["params"].items() if k != "run_at"} == booking
            for job in user["jobs"].values()
        )

    async def _save(self, data, sender, user):
        data["users"][sender] = encrypt_value(json.dumps(user, ensure_ascii=False))
        if (
            len(data["users"]) > MAX_USERS
            or sum(len(self._decode(v)["jobs"]) for v in data["users"].values())
            > MAX_JOBS
        ):
            raise ValueError("ledger full")
        payload = encrypt_value(json.dumps(data, ensure_ascii=False)).encode()
        if len(payload) > 4_000_000:
            raise ValueError("ledger full")
        await self.plugin.set_plugin_storage(KEY, payload)

    async def _profile(self, sender):
        raw = await read_optional_storage(self.plugin, "user:" + sender + ":profile")
        if not isinstance(raw, (bytes, str)) or not raw:
            raise ValueError("account missing")
        raw = raw.encode() if isinstance(raw, str) else raw
        profile = json.loads(raw)
        if (
            not isinstance(profile, dict)
            or not profile.get("student_id")
            or not profile.get("password")
        ):
            raise ValueError("account missing")
        # Verify current credentials are decryptable without retaining them in tasks.
        decrypt_value(str(profile["password"]))
        return hashlib.sha256(raw).hexdigest(), hashlib.sha256(
            str(profile["student_id"]).strip().encode()
        ).hexdigest()

    async def start(self):
        async with self.lock:
            owner = getattr(self.plugin, "_library_schedule_owner", None)
            owner = owner() if owner else None
            if (
                owner is not None
                and owner is not self
                and (owner.ready or owner.execution_tasks)
            ):
                raise RuntimeError("another worker owns this plugin")
            if self.worker and not self.worker.done():
                return
            if self.execution_tasks:
                raise RuntimeError("previous execution must finish before restart")
            self.plugin._library_schedule_owner = weakref.ref(self)
            self.stopping = False
            self.ready = False
            data = await self._read()
            for sender in list(data["users"]):
                user = self._user(data, sender)
                changed = False
                for job in user["jobs"].values():
                    if job["status"] == "running":
                        job.update(
                            status="unknown",
                            reason="restart_during_execution",
                            updated_at=self._now().isoformat(),
                        )
                        changed = True
                if changed:
                    await self._save(data, sender, user)
            self.ready = True
            self.worker = asyncio.create_task(self._poll())

    async def _poll(self):
        while not self.stopping:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Fixed error code only; never log SDK exceptions/credential payloads.
                logger.warning("library_schedule_worker_unavailable")
            await asyncio.sleep(self.poll_seconds)

    async def shutdown(self):
        async with self.lock:
            self.stopping = True
            self.ready = False
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        # A to_thread campus call cannot be cancelled safely; wait for its receipt
        # and persistence before releasing ownership to a replacement worker.
        if self.execution_tasks:
            await asyncio.gather(*self.execution_tasks, return_exceptions=True)
        self.worker = None

    def cancel_worker(self):
        """Synchronous SDK destructor hook; running durable claims remain fenced."""
        self.stopping = True
        self.ready = False
        if self.worker and not self.worker.done():
            try:
                self.worker.cancel()
            except RuntimeError:
                pass

    async def valid_pending(self, sender, conversation, query_id):
        if not self._identity(sender, conversation):
            return None
        async with self.lock:
            pending = self._user(await self._read(), sender)["pending"]
            if not pending or pending["conversation"] != conversation:
                return None
            check = validate_pending_operation(
                pending,
                token=pending["token"],
                storage_key=sender,
                canonical_command=pending["command"],
                query_id=query_id,
                now=self._now().timestamp(),
            )
            return pending if check.ok else None

    async def handle(self, ctx, *, is_group, command=None, confirmation_token=None):
        text = (
            command
            if command is not None
            else str(getattr(ctx.event, "text_message", "") or "").strip()
        )
        if not is_schedule_command(text):
            return False
        ctx.prevent_default()
        ctx.prevent_postorder()
        sender = str(getattr(ctx.event, "sender_id", "") or "").strip()
        conversation = conversation_context(ctx.event)
        if is_group or not self._identity(sender, conversation):
            response = result(
                "图书馆定时预约仅允许身份明确的本人私聊操作。", success=False
            )
        else:
            identity = (sender, conversation["launcher_id"], ctx.query_id)
            if identity in self.seen:
                return True
            self.seen.add(identity)
            if len(self.seen) > 1024:
                self.seen = {identity}
            response = await self.execute(
                text,
                sender,
                conversation,
                ctx.query_id,
                confirmation_token=confirmation_token,
            )
        await ctx.reply(MessageChain([Plain(text=response["reply_text"][:1600])]))
        return True

    @staticmethod
    def _public(task_id, job):
        return {
            "id": task_id,
            "status": job["status"],
            **job["params"],
            "reason": job["reason"],
        }

    async def execute(
        self, command, sender, conversation, query_id, *, confirmation_token=None
    ):
        if not self._identity(sender, conversation):
            return result(
                "仅允许本人私聊操作。",
                success=False,
                error_code="private_identity_required",
            )
        try:
            spec = inspect_cli_command(command)
            if spec.is_help:
                return result("\n".join(HELP))
            if not confirmation_token and (
                spec.error
                or not (spec.resolved_tool or "").startswith("library_schedule_")
            ):
                return result(
                    "定时预约格式无效，请发送 library schedule help。",
                    success=False,
                    error_code="invalid_parameters",
                )
            write = bool(
                confirmation_token or spec.resolved_tool == "library_schedule_add"
            )
            if write:
                if not self.ready or self.stopping:
                    return result(
                        "定时预约 worker 尚未就绪，本次未保存任务。",
                        success=False,
                        error_code="worker_unavailable",
                    )
                await require_benefit_async(sender, "library_schedule")
                profile, account = await self._profile(sender)
            async with self.lock:
                if write and (not self.ready or self.stopping):
                    return result(
                        "定时预约 worker 已停止，本次未保存任务。",
                        success=False,
                        error_code="worker_unavailable",
                    )
                data = await self._read()
                user = self._user(data, sender)
                if confirmation_token:
                    pending = user["pending"]
                    if not pending or pending["conversation"] != conversation:
                        return result("本私聊没有有效定时预约待确认。", success=False)
                    check = validate_pending_operation(
                        pending,
                        token=confirmation_token,
                        storage_key=sender,
                        canonical_command=pending["command"],
                        query_id=query_id,
                        now=self._now().timestamp(),
                    )
                    if not check.ok:
                        return result(check.message, success=False)
                    if profile != pending["profile"]:
                        return result(
                            "学校账号已变化，请重新创建定时预约。",
                            success=False,
                            error_code="account_changed",
                        )
                    if self._duplicate(user, pending["params"], account):
                        return result(
                            "相同账号和座位时段已有任务或未确认回执，请先查询，禁止重复提交。",
                            success=False,
                            error_code="duplicate_booking",
                        )
                    self._parameters(pending["params"], now=self._now())
                    if len(user["jobs"]) >= MAX_PER_USER:
                        return result(
                            "本人任务记录已达上限，请联系运营者归档后再创建。",
                            success=False,
                        )
                    task_id = secrets.token_hex(10)
                    now = self._now().isoformat()
                    user["jobs"][task_id] = {
                        "params": pending["params"],
                        "profile": profile,
                        "account": account,
                        "status": "pending",
                        "created_at": now,
                        "updated_at": now,
                        "reason": "",
                    }
                    user["pending"] = None
                    await self._save(data, sender, user)
                    return result(
                        "定时预约任务已保存；到期重检资格后至多提交一次。任务号："
                        + task_id,
                        id=task_id,
                        status="pending",
                    )
                action = spec.resolved_tool.rsplit("_", 1)[-1]
                if action == "add":
                    if self._duplicate(user, spec.params, account):
                        return result(
                            "相同账号和座位时段已有任务或未确认回执，请先查询，禁止重复提交。",
                            success=False,
                            error_code="duplicate_booking",
                        )
                    self._parameters(spec.params, now=self._now())
                    pending = create_pending_operation(
                        storage_key=sender,
                        canonical_command=command,
                        query_id=query_id,
                        now=self._now().timestamp(),
                    )
                    pending.update(
                        conversation=conversation, profile=profile, params=spec.params
                    )
                    user["pending"] = pending
                    await self._save(data, sender, user)
                    p = spec.params
                    return result(
                        f"执行时刻：{p['run_at']}（Asia/Shanghai）；座位使用：{p['target_date']} {p['preferred_time']}–{p['preferred_end_time']}，{p['location']} {p['seat_no']}。会员费用已包含，仅一次执行。请在下一条本人私聊回复“确认定时预约”（五分钟有效）。",
                        success=False,
                        confirmation_required=True,
                    )
                if action == "list":
                    jobs = [self._public(k, v) for k, v in user["jobs"].items()]
                    text = "\n".join(
                        f"{j['id']} {j['status']} 执行 {j['run_at']}；使用 {j['target_date']} {j['preferred_time']}–{j['preferred_end_time']}"
                        for j in jobs
                    )
                    return result(text or "本人没有定时预约任务。", jobs=jobs)
                task_id = spec.params["id"]
                if action == "cancel" and task_id == "awaiting":
                    user["pending"] = None
                    await self._save(data, sender, user)
                    return result("已清除本人尚未确认的定时预约预览。")
                job = user["jobs"].get(task_id)
                if not job:
                    return result(
                        "本人没有此任务。", success=False, error_code="not_found"
                    )
                if action == "cancel":
                    if job["status"] != "pending":
                        return result(
                            "仅待执行任务可取消；已开始或结果未知的任务请先查学校记录。",
                            success=False,
                        )
                    job.update(
                        status="cancelled",
                        reason="",
                        updated_at=self._now().isoformat(),
                    )
                    await self._save(data, sender, user)
                public = self._public(task_id, job)
                return result(
                    f"任务 {task_id}：{job['status']}；执行 {job['params']['run_at']}；座位使用 {job['params']['target_date']} {job['params']['preferred_time']}–{job['params']['preferred_end_time']}。结果未知不会自动重试。",
                    job=public,
                )
        except MembershipError as exc:
            return exc.public_result()
        except ValueError:
            return result(
                "参数、学校账号或任务记录无效。执行时刻须为未来30天内东八区ISO时刻，早于座位使用；本次未执行。",
                success=False,
                error_code="invalid_parameters_or_account",
            )
        except Exception:
            return result(
                "任务 Storage 暂不可用，本次结果未确认；请先查询任务，勿重复创建。",
                success=False,
                error_code="storage_unavailable",
            )

    async def run_due(self):
        if not self.ready or self.stopping:
            return
        claimed = []
        async with self.lock:
            if self.stopping:
                return
            data = await self._read()
            transitioned = False
            for sender in data["users"]:
                user = self._user(data, sender)
                for task_id, job in user["jobs"].items():
                    if job["status"] != "pending":
                        continue
                    age = (
                        self._now() - self._parameters(job["params"])
                    ).total_seconds()
                    if age < 0:
                        continue
                    job.update(
                        status="missed" if age > MISSED_WINDOW else "running",
                        reason="missed_window" if age > MISSED_WINDOW else "",
                        updated_at=self._now().isoformat(),
                    )
                    await self._save(data, sender, user)
                    transitioned = True
                    if job["status"] == "running":
                        claimed.append((sender, task_id, job.copy()))
                    # One claim per poll keeps SDK/network work bounded.
                    break
                if transitioned:
                    break
        for args in claimed:
            task = asyncio.create_task(self._execute_claim(*args))
            self.execution_tasks.add(task)
            task.add_done_callback(self.execution_tasks.discard)
            await asyncio.shield(task)

    async def _finish(self, sender, task_id, status, reason=""):
        async with self.lock:
            data = await self._read()
            user = self._user(data, sender)
            job = user["jobs"][task_id]
            if job["status"] != "running":
                return
            job.update(status=status, reason=reason, updated_at=self._now().isoformat())
            await self._save(data, sender, user)

    async def _execute_claim(self, sender, task_id, job):
        adapter = None
        invoked = False
        status, reason = "blocked", "dependency_unavailable"
        try:
            adapter = PluginStorageAdapter(self.plugin, sender)
            paths = await adapter.load_all()
            profile_hash = hashlib.sha256(paths.profile_file.read_bytes()).hexdigest()
            profile = load_encrypted_profile(paths.profile_file)
            if (
                profile_hash != job["profile"]
                or not profile.get("student_id")
                or not profile.get("password")
            ):
                reason = "account_changed"
            elif (
                self._now() - self._parameters(job["params"])
            ).total_seconds() > MISSED_WINDOW:
                status, reason = "missed", "missed_window"
            elif self.stopping:
                reason = "worker_stopped"
            else:
                session = Session(
                    sender_id=sender,
                    launcher_id=sender,
                    launcher_type=LauncherTypes.PERSON,
                )
                params = {k: v for k, v in job["params"].items() if k != "run_at"}
                params.update(max_attempts=1, retry_until="", retry_interval_seconds=1)

                def submit():
                    nonlocal invoked
                    from henu_plugin.service import _RUNTIME_STATE_LOCK

                    # Requalify after any thread-pool queue, before acquiring any
                    # global runtime context/lock. A prior async grant is not reused.
                    qualification = require_benefit(sender, "library_schedule")
                    # Never queue behind another QQ's long campus operation after
                    # qualification. RLock is reentrant for the same service thread.
                    if not _RUNTIME_STATE_LOCK.acquire(blocking=False):
                        return {"scheduler_refused": "executor_busy"}
                    try:
                        if self.stopping or not self.ready:
                            return {"scheduler_refused": "worker_stopped"}
                        if (
                            self._now() - self._parameters(job["params"])
                        ).total_seconds() > MISSED_WINDOW:
                            return {"scheduler_refused": "missed_window"}
                        checked = datetime.fromisoformat(
                            qualification["checked_at"].replace("Z", "+00:00")
                        )
                        if (
                            not 0
                            <= (datetime.now(timezone.utc) - checked).total_seconds()
                            <= 30
                        ):
                            return {"scheduler_refused": "qualification_expired"}
                        if (
                            hashlib.sha256(paths.profile_file.read_bytes()).hexdigest()
                            != job["profile"]
                        ):
                            return {"scheduler_refused": "account_changed"}
                        set_current_user_paths(paths)
                        invoked = True
                        try:
                            return self.plugin.service.run_tool(
                                "library_reserve", params, session, 0
                            )
                        finally:
                            set_current_user_paths(None)
                    finally:
                        _RUNTIME_STATE_LOCK.release()

                response = await asyncio.to_thread(submit)
                if not invoked:
                    reason = response.get("scheduler_refused", "dependency_unavailable")
                    status = "missed" if reason == "missed_window" else "blocked"
                else:
                    # Upstream negative/ambiguous replies may follow a committed
                    # request; never render or persist raw errors or credentials.
                    status = (
                        "succeeded"
                        if isinstance(response, dict)
                        and response.get("success") is True
                        else "unknown"
                    )
                    reason = "" if status == "succeeded" else "receipt_unconfirmed"
                    await adapter.save_all()
                    adapter = None
        except MembershipError as exc:
            reason = exc.code
        except Exception:
            if invoked:
                status, reason = "unknown", "receipt_unconfirmed"
        finally:
            if adapter:
                await adapter.abort()
        try:
            await self._finish(sender, task_id, status, reason)
        except Exception:
            # Durable running claim remains an at-most-once fence on SDK outage.
            logger.warning("library_schedule_receipt_storage_unavailable")
