"""Per-user confirmation and durable unknown-write fences for student quizzes."""
from __future__ import annotations

import asyncio
import json
import shlex
from typing import Any

from henu_plugin.yuketang_quiz import QUIZ_WRITE_AUTH


class QuizFlow:
    def __init__(self, tool: Any, owner: str, session: Any, query_id: int):
        self.tool, self.owner, self.session, self.query_id = tool, owner, session, query_id
        self.plugin = tool.plugin
        self.key = f"user:{owner}:quiz_write_journal"
        locks = getattr(self.plugin, "_quiz_operation_locks", None)
        if locks is None:
            locks = self.plugin._quiz_operation_locks = {}
        self.lock = locks.setdefault(owner, asyncio.Lock())

    @staticmethod
    def failure(code: str, message: str) -> dict[str, Any]:
        return {"success": False, "error_code": code, "msg": message, "reply_text": message, "retry_safe": False}

    async def execute(self, command: str, *, internal: bool = False) -> dict[str, Any]:
        return await self.tool._quiz_execute_command(command, self.session, self.query_id, internal=internal)

    async def journal(self) -> dict[str, Any]:
        getter = getattr(self.plugin, "get_plugin_storage_keys", None)
        if callable(getter) and self.key not in await getter():
            return {}
        raw = await self.plugin.get_plugin_storage(self.key)
        if not raw:
            return {}
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Invalid quiz journal")
        return value

    async def store(self, value: dict[str, Any]) -> None:
        await self.plugin.set_plugin_storage(self.key, json.dumps(value, ensure_ascii=False).encode())

    async def reconcile(self, quiz: dict[str, Any]) -> bool:
        record = await self.journal()
        if not record:
            return True
        if record.get("binding_revision") != quiz.get("binding_revision"):
            return False
        operation = record.get("operation")
        resolved = (operation == "start" and quiz.get("status") in {1, 2, 3}
                    or operation == "save" and quiz.get("cache_revision") == record.get("expected_result_revision")
                    or operation == "submit" and quiz.get("status") == 2)
        if resolved:
            await self.store({})
        return bool(resolved)

    @staticmethod
    def preview_command(spec: Any) -> str:
        argv = ["yuketang", "quiz", "preview", "--operation", spec.params["operation"]]
        for key in ("course", "quiz_id", "revision", "answers_json"):
            if key in spec.params:
                argv.extend(["--" + key.replace("_", "-"), str(spec.params[key])])
        return shlex.join(argv)

    async def read(self, command: str) -> dict[str, Any]:
        async with self.lock:
            result = await self.execute(command)
            try:
                if result.get("success") and isinstance(result.get("quiz"), dict):
                    if not await self.reconcile(result["quiz"]):
                        result["write_pending"] = True
                        result["retry_safe"] = False
                        result["warning"] = "此前测验写入仍未核对，暂不允许新的写入。"
            except Exception:
                return self.failure("quiz_journal_unavailable", "测验写入记录无法读取；本次未进行写操作。")
            return result

    async def prepare(self, spec: Any) -> dict[str, Any]:
        from henu_plugin.confirmation import conversation_context
        async with self.lock:
            preview = await self.execute(self.preview_command(spec), internal=True)
            if not preview.get("success"):
                return preview
            try:
                if not await self.reconcile(preview["quiz"]):
                    return self.failure("quiz_write_pending", "存在尚未核对的测验写入；请先查询原测验状态，不要重复提交。")
            except Exception:
                return self.failure("quiz_journal_unavailable", "测验写入记录无法读取，未创建写入任务。")
            if preview.get("already_completed"):
                return preview
            operation = preview["quiz_write"]
            command = spec.raw
            if not spec.params.get("revision"):
                command += " --revision " + operation["revision"]
            quiz = preview["quiz"]
            action = {"start": "开始测验（不交卷）", "save": "保存测验答案（不交卷）", "submit": "正式交卷"}[operation["operation"]]
            summary = f"{quiz['title']}；班级 {quiz['classroom']['classroom_label']}；{quiz['saved_count']}/{quiz['question_count']} 题已保存"
            if operation["operation"] == "start":
                summary += f"；限时 {quiz.get('limit_seconds', '未提供')} 秒（0 为不限时）"
            elif operation["operation"] == "save":
                summary += f"；本次拟保存 {preview['planned_count']} 题，不交卷"
            return await self.tool._request_confirmation(
                storage_key=self.owner, canonical_command=command, action=action,
                parameter_summary=summary, query_id=self.query_id,
                conversation=conversation_context(self.session), extra_pending={"quiz_operation": operation})

    async def confirmed(self, spec: Any, pending: dict[str, Any]) -> dict[str, Any]:
        from henu_plugin.confirmation import conversation_context, validate_pending_operation
        async with self.lock:
            # Another request may have consumed or replaced this confirmation
            # while this request was waiting for the per-user lock.
            current = await self.tool._load_pending(self.owner)
            check = validate_pending_operation(current, token=pending.get("token", ""),
                storage_key=self.owner, canonical_command=spec.raw, query_id=self.query_id)
            if not check.ok:
                return self.failure("quiz_confirmation_invalid", check.message)
            pending = current
            if pending.get("conversation") != conversation_context(self.session):
                return self.failure("quiz_confirmation_invalid", "请回到发起操作的原私聊确认。")
            operation = pending.get("quiz_operation")
            if not isinstance(operation, dict):
                return self.failure("quiz_confirmation_invalid", "测验确认缺少目标快照，请重新发起。")
            preview = await self.execute(self.preview_command(spec), internal=True)
            if not preview.get("success"):
                return preview
            if operation != preview.get("quiz_write"):
                return self.failure("quiz_revision_changed", "测验、登录主体或已保存答案已变化，请重新核对并确认。")
            try:
                if not await self.reconcile(preview["quiz"]):
                    return self.failure("quiz_write_pending", "此前测验写入尚未核对，已拒绝重复执行。")
                # Persist the fence and consume this confirmation BEFORE any POST.
                await self.store(operation)
                await self.plugin.set_plugin_storage(f"user:{self.owner}:pending_operation", b"{}")
            except Exception:
                return self.failure("quiz_journal_unavailable", "确认或写入记录无法持久保存，本次未进行写操作。")
            token = QUIZ_WRITE_AUTH.set((self.owner, operation["operation"], operation["quiz_id"], operation["revision"]))
            try:
                result = await self.execute(spec.raw)
            finally:
                QUIZ_WRITE_AUTH.reset(token)
            actual = result.get("tool_result", result)
            try:
                if actual.get("success") or actual.get("external_attempted") is False:
                    await self.store({})
            except Exception:
                result["quiz_journal_cleanup_pending"] = True
                result["retry_safe"] = False
            return result
