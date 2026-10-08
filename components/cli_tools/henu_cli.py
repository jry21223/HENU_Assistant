from __future__ import annotations

import asyncio
import json
import time
import shlex
from typing import Any

from components.cli_tools.base import BaseHenuTool, _resolve_storage_key
from henu_plugin.cli import inspect_cli_command
from henu_plugin.confirmation import (
    conversation_context,
    WRITE_TOOL_NAMES,
    create_pending_operation,
    pending_storage_key,
    split_confirm_token,
    validate_pending_operation,
)


class HenuCli(BaseHenuTool):
    tool_name = "henu_cli"
    _confirmation_locks: dict[tuple[int, str], asyncio.Lock] = {}

    @classmethod
    def _confirmation_lock(cls, storage_key):
        return cls._confirmation_locks.setdefault(
            (id(asyncio.get_running_loop()), storage_key), asyncio.Lock()
        )

    def should_preload_runtime_context(self, params):
        command = params.get("command") if isinstance(params, dict) else ""
        clean_command, _ = split_confirm_token(command)
        if self._is_confirmation_command(clean_command):
            return True
        spec = inspect_cli_command(clean_command)
        return spec.should_preload_runtime_context

    async def call(self, params, session, query_id):
        if not isinstance(params, dict):
            return {"success": False, "msg": "参数必须是对象"}

        identity_hint = await self._load_identity_hint(query_id)
        identity_error = self._validate_identity(session, identity_hint)
        if identity_error:
            return {"success": False, "error_code": "identity_missing", "msg": identity_error}

        raw_command = str(params.get("command") or "").strip()
        if not raw_command:
            return await super().call(params, session, query_id)

        storage_key = _resolve_storage_key(session, identity_hint)
        if raw_command.strip().lower() == "library operation-status":
            return await self.operation_status(
                storage_key, conversation_context(session)
            )
        if self._is_confirmation_command(raw_command):
            return await self._execute_confirmation(
                raw_command=raw_command,
                storage_key=storage_key,
                session=session,
                query_id=query_id,
            )

        clean_command, inline_token = split_confirm_token(raw_command)
        spec = inspect_cli_command(clean_command)
        if spec.action == "library operation-status":
            return await self.operation_status(storage_key, conversation_context(session))

        if spec.error and clean_command.lower().startswith(("library reserve", "library book")):
            result = {"success": False, "error_code": "invalid_parameters", "msg": spec.error, "reply_text": spec.error}
            await self._supersede_library_pending(storage_key, conversation_context(session), query_id, result)
            return result

        # Account passwords and calibration cookies are handled directly by the
        # event listener before the model is called. Fail closed if a provider
        # nevertheless attempts to invoke them through the Tool surface.
        if spec.resolved_tool in {
            "setup_account",
            "set_calibration_source",
            "yuketang_account_set",
            "yuketang_set_token",
            "yuketang_login",
            "yuketang_logout",
        }:
            return {
                "success": False,
                "error_code": "direct_private_command_required",
                "msg": "该敏感命令必须在私聊中直接发送，由插件拦截处理，不能经由模型 Tool 调用。",
                "reply_text": "请在私聊中直接发送绑定或校准命令；插件会在调用模型前处理，不会把密码或 Cookie 发给模型。",
            }

        stopping_yuketang = spec.resolved_tool == 'yuketang_set_enabled' and spec.params.get('enabled') == 'off'
        if spec.resolved_tool in WRITE_TOOL_NAMES and not stopping_yuketang:
            parameter_error = self._write_parameter_error(spec)
            if parameter_error:
                await self._supersede_library_pending(storage_key, conversation_context(session), query_id, {"success": False, "msg": parameter_error})
                return {
                    "success": False,
                    "error_code": "confirmation_parameters_incomplete",
                    "msg": parameter_error,
                    "reply_text": parameter_error,
                }
            if inline_token:
                return await self._execute_pending(
                    token=inline_token,
                    storage_key=storage_key,
                    canonical_command=clean_command,
                    session=session,
                    query_id=query_id,
                )
            summary = self._operation_summary(spec)
            library_plan = None
            if spec.resolved_tool == "library_reserve":
                preview = await self._library_preflight(spec, session, query_id)
                if not preview.get("success"):
                    await self._supersede_library_pending(storage_key, conversation_context(session), query_id, preview)
                    return preview
                summary = preview["reply_text"]
                library_plan = preview["library_plan"]
            return await self._request_confirmation(
                storage_key=storage_key,
                canonical_command=clean_command,
                action=spec.action,
                parameter_summary=summary,
                library_plan=library_plan,
                query_id=query_id,
                conversation=conversation_context(session),
            )

        result = await super().call({"command": clean_command}, session, query_id)
        return self._mark_storage_commit_state(result)

    async def _supersede_library_pending(self, storage_key, conversation, query_id, result):
        async with self._confirmation_lock(storage_key):
            old = await self._load_pending(storage_key)
            if old.get("schema") == "henu.pending-operation.v1" and old.get("conversation") == conversation and str(old.get("command") or "").startswith("library "):
                await self._store_receipt(storage_key, old, query_id, "blocked", result)

    async def _library_preflight(self, spec, session, query_id):
        p = spec.params
        command = shlex.join(
            [
                "library",
                "plan",
                "--location",
                p["location"],
                "--seat-no",
                p["seat_no"],
                "--date",
                p["target_date"],
                "--time",
                p["preferred_time"],
            ]
        )
        if p.get("preferred_end_time"):
            command += " --end-time " + shlex.quote(p["preferred_end_time"])
        result = await super().call({"command": command}, session, query_id)
        if not isinstance(result, dict) or not result.get("success"):
            return result
        rules = result.get("reservation_rules") or {}
        window = rules.get("booking_window") or {}
        if not rules.get("effective_start") or not rules.get("effective_end"):
            return {
                "success": False,
                "error_code": "reservation_rules_missing",
                "msg": "学校未返回明确的实际预约时段，未生成确认请求。",
                "reply_text": "学校未返回明确的实际预约时段，未生成确认请求。",
            }
        if window.get("open_now") is False:
            msg = f"当前不在学校开放预约时间 {window['start']}～{window['end']} 内，未提交；请在开放后重新发起。"
            return {
                "success": False,
                "error_code": "booking_window_closed",
                "msg": msg,
                "reply_text": msg,
                "reservation_rules": rules,
            }
        if not result.get("seats"):
            msg = f"座位 {p['seat_no']} 当前不可预约，未生成确认请求。"
            return {
                "success": False,
                "error_code": "seat_unavailable",
                "msg": msg,
                "reply_text": msg,
            }
        prefix = "学校仅支持完整固定时段；" if rules.get("fixed_segment") else ""
        area = result.get("area") or {}
        msg = f"{area.get('name') or p['location']}，座位 {p['seat_no']}，{p['target_date']}，实际 {rules['effective_start']}～{rules['effective_end']}。{prefix}你选择的 {p['preferred_time']} 用于选时段。"
        signature = {"area_id": area.get("id"), "seat_id": result["seats"][0].get("id"), "date": result.get("target_date") or p["target_date"], "segment": rules.get("segment"), "start": rules["effective_start"], "end": rules["effective_end"], "fixed_segment": rules.get("fixed_segment")}
        return {"success": True, "reply_text": msg, "reservation_rules": rules, "library_plan": signature}

    @staticmethod
    def _write_parameter_error(spec: Any) -> str:
        params = spec.params if isinstance(getattr(spec, "params", None), dict) else {}
        tool = str(getattr(spec, "resolved_tool", "") or "")
        required: dict[str, tuple[str, ...]] = {
            "library_reserve": ("location", "seat_no", "target_date", "preferred_time"),
            "library_auto_signin": ("record_id",),
            "library_cancel": ("record_id",),
            "seminar_reserve": ("area_id", "target_date", "start_time", "end_time", "title"),
            "seminar_signin": ("record_id",),
            "seminar_cancel": ("record_id",),
        }
        missing = [key for key in required.get(tool, ()) if not str(params.get(key) or "").strip()]
        if missing:
            flags = "、".join("--" + key.replace("_", "-") for key in missing)
            return f"外部写操作必须显式提供完整参数，缺少：{flags}。不会使用账号默认值或隐式日期。"
        if tool == "library_reserve":
            if not params.get("end_policy"):
                return (
                    "请提供结束时间，或选择‘到最晚’（--end-policy server_full_slot）。"
                )
            if params["end_policy"] in {"ask", "explicit_end"} and not params.get(
                "preferred_end_time"
            ):
                return "请提供明确结束时间 --end-time，或选择‘到最晚’。"
        if tool == "seminar_signin" and params.get("auto_scan"):
            return "LangBot 写操作确认不允许 seminar signin --auto-scan；请先查询签到任务并指定 --record-id。"
        return ""

    @staticmethod
    def _operation_summary(spec: Any) -> str:
        params = spec.params if isinstance(getattr(spec, "params", None), dict) else {}
        keys_by_tool = {
            "yuketang_set_enabled": ("enabled",),
            "yuketang_domain_set": ("domain",),
            "yuketang_lesson_set": ("auto_enter", "auto_answer", "llm", "subjective", "enter_delay"),
            "yuketang_exam_set": ("auto_answer", "llm", "subjective", "master", "slave"),
            "yuketang_list_update": ("scope", "op", "items"),
            "yuketang_start_time": ("op", "course", "slots"),
            "library_reserve": ("location", "seat_no", "target_date", "preferred_time", "preferred_end_time"),
            "library_auto_signin": ("record_id",),
            "library_cancel": ("record_id", "record_type"),
            "seminar_reserve": ("area_id", "target_date", "start_time", "end_time", "title"),
            "seminar_signin": ("record_id",),
            "seminar_cancel": ("record_id",),
        }
        fields = []
        for key in keys_by_tool.get(str(getattr(spec, "resolved_tool", "") or ""), ()):
            value = params.get(key)
            if value not in (None, "", False):
                fields.append(f"{key}={value}")
        return "参数 " + "，".join(fields) if fields else "参数已规范化"

    async def _request_confirmation(
        self,
        *,
        storage_key: str,
        canonical_command: str,
        action: str,
        parameter_summary: str,
        query_id: int,
        conversation: dict[str, str],
        library_plan=None,
    ) -> dict[str, Any]:
        async with self._confirmation_lock(storage_key):
            return await self._request_confirmation_unlocked(
                storage_key=storage_key,
                canonical_command=canonical_command,
                action=action,
                parameter_summary=parameter_summary,
                query_id=query_id,
                conversation=conversation,
                library_plan=library_plan,
            )

    async def _request_confirmation_unlocked(
        self,
        *,
        storage_key,
        canonical_command,
        action,
        parameter_summary,
        query_id,
        conversation,
        library_plan=None,
    ):
        existing = await self._load_pending(storage_key)
        same = (
            existing.get("schema") == "henu.pending-operation.v1"
            and (existing.get("library_plan") == library_plan if library_plan is not None else existing.get("command") == canonical_command)
            and existing.get("conversation") == conversation
            and float(existing.get("expires_at") or 0) > time.time()
        )
        pending = create_pending_operation(
            storage_key=storage_key,
            canonical_command=canonical_command,
            query_id=query_id,
        )
        pending["library_plan"] = library_plan
        pending["preview"] = parameter_summary
        if same:
            pending = existing
        key = pending_storage_key(storage_key)
        pending['conversation'] = conversation
        try:
            await self.plugin.set_plugin_storage(
                key,
                json.dumps(pending, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as exc:
            return {
                "success": False,
                "error_code": "confirmation_storage_failed",
                "msg": f"保存待确认操作失败: {exc}",
            }

        confirm_command = "确认"
        action_text = action or "外部写操作"
        summary_text = parameter_summary or "参数已规范化"
        return {
            "success": False,
            "confirmation_required": True,
            "error_code": "confirmation_required",
            "msg": f"{action_text} 尚未执行，需要用户在下一条消息中明确确认。",
            "reply_text": (
                f"即将执行：{action_text}；{summary_text}。本次尚未提交。"
                f"请核对后在下一条消息直接回复“{confirm_command}”（5 分钟内有效）。"
            ),
            "confirmation_command": confirm_command,
            "expires_in_seconds": max(0, int(pending["expires_at"] - time.time())),
            "retry_safe": True,
            "llm_hint": "必须等待用户发送新的确认消息；禁止在当前轮次自动调用 confirm。",
            "cli": {"mode": "confirmation", "action": action_text},
        }

    async def _execute_confirmation(
        self,
        *,
        raw_command: str,
        storage_key: str,
        session,
        query_id: int,
    ) -> dict[str, Any]:
        try:
            argv = shlex.split(raw_command)
        except ValueError as exc:
            return {"success": False, "msg": f"确认命令解析失败: {exc}"}
        if len(argv) != 2 or argv[0].lower() not in {"confirm", "确认"}:
            return {"success": False, "msg": "确认命令格式为 `confirm <token>`。"}
        pending = await self._load_pending(storage_key)
        canonical_command = str(pending.get("command") or "") if isinstance(pending, dict) else ""
        return await self._execute_pending(
            token=argv[1],
            storage_key=storage_key,
            canonical_command=canonical_command,
            session=session,
            query_id=query_id,
            pending=pending,
        )

    async def _execute_pending(
        self, *, token, storage_key, canonical_command, session, query_id, pending=None
    ):
        async with self._confirmation_lock(storage_key):
            pending = await self._load_pending(storage_key)
            if pending.get("conversation") != conversation_context(session):
                return {
                    "success": False,
                    "error_code": "confirmation_invalid",
                    "msg": "确认会话与原操作不一致，未执行。",
                }
            command = canonical_command or str(pending.get("command") or "")
            check = validate_pending_operation(
                pending,
                token=token,
                storage_key=storage_key,
                canonical_command=command,
                query_id=query_id,
            )
            if not check.ok:
                return {
                    "success": False,
                    "error_code": "confirmation_invalid",
                    "msg": check.message,
                    "retry_safe": True,
                }
            spec = inspect_cli_command(command)
            if spec.error or spec.resolved_tool not in WRITE_TOOL_NAMES:
                return {
                    "success": False,
                    "error_code": "confirmation_scope_invalid",
                    "msg": "待确认操作无效，未执行。",
                }
            try:
                await self._store_receipt(storage_key, pending, query_id, "executing")
            except Exception:
                return {
                    "success": False,
                    "error_code": "confirmation_storage_failed",
                    "msg": "保存执行状态失败，未提交。",
                }
            try:
                result = None
                if spec.resolved_tool == "library_reserve":
                    fresh = await self._library_preflight(spec, session, query_id)
                    if not fresh.get("success"):
                        result = fresh
                    elif not pending.get("library_plan") or fresh["library_plan"] != pending["library_plan"]:
                        msg = "学校实际预约计划已变化，旧确认已失效，本次未提交；请重新预览并确认。"
                        result = {"success": False, "error_code": "reservation_plan_changed", "msg": msg, "reply_text": msg}
                if result is None:
                    result = self._mark_storage_commit_state(await super().call({"command": command}, session, query_id))
                if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
                    result = {"success": False, "outcome_unknown": True, "msg": "操作结果未明确返回，请查询当前预约，不要重复提交。"}
                if spec.resolved_tool == "library_reserve" and result.get("success") is True:
                    result["reply_text"] = "✅ 预约成功。" + str(result.get("reply_text") or result.get("msg") or "")
                committed = result.get("success") is True or result.get("external_committed") is True
                nested = result.get("tool_result") if isinstance(result.get("tool_result"), dict) else {}
                uncertain = (result.get("outcome_unknown") or result.get("submit_success") or nested.get("submit_success")) and not committed
                status = "committed" if committed else "uncertain" if uncertain else "failed"
            except BaseException:
                await self._store_receipt(storage_key, pending, query_id, "uncertain")
                raise
            try:
                await self._store_receipt(
                    storage_key, pending, query_id, status, result
                )
            except Exception:
                result["confirmation_cleanup_failed"] = True
            result["retry_safe"] = False
        return result

    async def _store_receipt(self, storage_key, pending, query_id, status, result=None):
        result = result if isinstance(result, dict) else {}
        summary = {
            key: result[key]
            for key in (
                "success",
                "msg",
                "external_committed",
                "storage_persisted",
                "submit_success",
            )
            if key in result
        }
        if "msg" in summary:
            summary["msg"] = str(summary["msg"])[:600]
        receipt = {
            "schema": "henu.confirmation-receipt.v1",
            "status": status,
            "command": pending.get("command", ""),
            "conversation": pending.get("conversation"),
            "created_query_id": pending.get("created_query_id"),
            "consumed_query_id": query_id,
            "updated_at": time.time(),
            "result": summary,
        }
        task = asyncio.create_task(
            self.plugin.set_plugin_storage(
                pending_storage_key(storage_key),
                json.dumps(receipt, ensure_ascii=False).encode(),
            )
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def operation_status(self, storage_key, conversation):
        pending = await self._load_pending(storage_key)
        if pending.get("conversation") != conversation or not str(
            pending.get("command") or ""
        ).startswith("library "):
            return {
                "success": False,
                "operation_state": "none",
                "reply_text": "当前聊天没有图书馆操作记录。",
            }
        state = pending.get("status")
        if pending.get("schema") == "henu.pending-operation.v1":
            state = (
                "pending"
                if float(pending.get("expires_at") or 0) > time.time()
                else "expired"
            )
        messages = {
            "pending": "预约尚未提交，请核对已展示的实际时段后回复‘确认’。",
            "expired": "确认已过期，预约未提交；请重新发起。",
            "executing": "确认已收到，操作正在执行，请等待结果，不要重复提交。",
            "committed": "已执行成功；如需查看当前预约，请查询 library current。",
            "blocked": "新的请求未提交，旧待确认请求已撤销：" + str((pending.get("result") or {}).get("msg") or "请重新发起。"),
            "failed": "已执行，但未成功："
            + str(
                (pending.get("result") or {}).get("msg")
                or "学校未返回明确原因，请查询当前预约。"
            ),
            "uncertain": "操作已发起，但结果尚未核实；请查询 library current，不要重复提交。",
        }
        msg = messages.get(state, "操作记录无法识别，请查询当前预约。")
        return {
            "success": state == "committed",
            "operation_state": state or "uncertain",
            "msg": msg,
            "reply_text": msg,
            "confirmation_required": state == "pending",
            "retry_safe": False,
            "next_commands": ["library current"],
        }

    async def _load_pending(self, storage_key: str) -> dict[str, Any]:
        try:
            raw = await self.plugin.get_plugin_storage(pending_storage_key(storage_key))
        except Exception:
            return {}
        if not raw:
            return {}
        try:
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            value = json.loads(bytes(raw).decode("utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _is_confirmation_command(command: str) -> bool:
        text = str(command or "").lstrip().lower()
        return text.startswith("confirm ") or text.startswith("确认 ")

    @staticmethod
    def _validate_identity(session, identity_hint: dict[str, Any]) -> str:
        launcher_type = getattr(getattr(session, "launcher_type", ""), "value", getattr(session, "launcher_type", ""))
        launcher_type = str(launcher_type or "").lower()
        sender_id = str(getattr(session, "sender_id", "") or "").strip()
        launcher_id = str(getattr(session, "launcher_id", "") or "").strip()
        trusted = {
            "sender_id": sender_id,
            "launcher_id": launcher_id,
            "launcher_type": launcher_type,
        }
        for field, trusted_value in trusted.items():
            hinted_value = identity_hint.get(field)
            if hinted_value in (None, ""):
                continue
            normalized_hint = str(
                getattr(hinted_value, "value", hinted_value) or ""
            ).strip()
            if field == "launcher_type":
                normalized_hint = normalized_hint.lower()
            if normalized_hint != trusted_value:
                return "调用身份与请求上下文不一致，已拒绝访问任何个人数据。"
        if launcher_type == "group" and sender_id in {"", "0", "None", "none"}:
            return "群聊缺少发送者身份，已拒绝访问任何个人账号或预约数据。"
        if sender_id in {"", "0", "None", "none"} and launcher_id in {"", "0", "None", "none"}:
            return "无法确认当前用户身份，已拒绝执行。"
        return ""

    @staticmethod
    def _mark_storage_commit_state(result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        tool_result = result.get("tool_result")
        if isinstance(tool_result, dict) and tool_result.get('_yuketang_sync_required'):
            result.update(external_committed=False, storage_persisted=False, retry_safe=True)
            result['msg'] = '本地雨课堂配置保存失败，未推送桥端；请稍后重试。'
            result['reply_text'] = result['msg']
            result.pop('tool_result', None)
            return result
        if (
            result.get("success") is False
            and isinstance(tool_result, dict)
            and tool_result.get("success") is True
        ):
            result["external_committed"] = True
            result["storage_persisted"] = False
            result["retry_safe"] = False
            result["msg"] = (
                "校园系统操作已经成功，但本地 Storage 保存失败。"
                "不要重复执行；请先查询当前记录进行反查。"
            )
        return result

    async def _prime_runtime_context_query_var(self, query_id: int) -> None:
        # Never erase the request snapshot created during PromptPreProcessing.
        return None

    async def _ensure_runtime_context(
        self,
        query_id: int,
        session,
        identity_hint: dict[str, Any],
        service: Any,
    ) -> dict[str, Any] | None:
        handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if handler is None:
            return None

        from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy
        from henu_plugin.service import get_current_user_paths

        proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=handler)
        try:
            query_vars = await proxy.get_query_vars()
        except Exception:
            query_vars = {}

        cached = query_vars.get("_henu_runtime_context") if isinstance(query_vars, dict) else None
        if (
            isinstance(cached, dict)
            and cached.get("server_time")
            and cached.get("request_query_id") == query_id
        ):
            return cached

        timezone = self._resolve_timezone(query_vars if isinstance(query_vars, dict) else {})
        runtime_context = await self._run_with_user_storage(
            get_current_user_paths(),
            service.get_runtime_context,
            session,
            identity_hint,
            timezone,
        )
        if isinstance(runtime_context, dict):
            runtime_context["request_query_id"] = query_id
            try:
                await proxy.set_query_var("_henu_runtime_context", runtime_context)
            except Exception:
                pass
            return runtime_context
        return None
