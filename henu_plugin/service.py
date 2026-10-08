from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from langbot_plugin.api.entities.builtin.provider import session as provider_session

from henu_mcp import api as henu_api
from henu_mcp.tools import server_impl
from henu_plugin.cache import (
    ACCOUNT_CONTEXT_CACHE,
    LIBRARY_QUERY_CACHE,
    RUNTIME_CONTEXT_CACHE,
    SCHEDULE_CACHE,
    SEMINAR_QUERY_CACHE,
    SERVER_TIME_CACHE,
    get_cache_stats,
    invalidate_account_cache,
    invalidate_schedule_cache,
    invalidate_user_cache,
)
from henu_plugin.cli import (
    CliCommandSpec,
    build_help_payload,
    build_next_commands,
    inspect_cli_command,
    redact_cli_command,
    redact_cli_params,
)
from henu_plugin.storage_adapter import (
    SHARED_PERIOD_TIME_FILE,
    UserStoragePaths,
)
from henu_plugin.runtime_adapter import LangBotStorageRuntime
from henu_plugin import yuketang_config
from henu_plugin import bridge_client


_RUNTIME_STATE_LOCK = threading.RLock()
_CURRENT_IDENTITY: threading.local = threading.local()
_COURSE_MONITOR_MIN_INTERVAL_SECONDS = 60
_PURE_TOOL_NAMES = frozenset(
    spec.name for spec in henu_api.MCP_TOOL_SPECS if spec.execution_mode == "pure"
)
_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class SessionIdentity:
    qq: str
    storage_key: str
    launcher_type: str
    launcher_id: str
    sender_id: str


# Context-local storage for current user paths.
# `asyncio.to_thread()` propagates contextvars, but not thread-local values.
_CURRENT_USER_PATHS: contextvars.ContextVar["UserStoragePaths | None"] = contextvars.ContextVar(
    "henu_current_user_paths",
    default=None,
)


async def _await_task_cancellation_safe(
    worker: asyncio.Future[_ResultT],
) -> _ResultT:
    """Wait for a stateful task to finish before propagating cancellation."""
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Timeout and teardown may both cancel the caller. Neither may
                # detach a thread that still owns runtime or staging state.
                continue
        with contextlib.suppress(Exception):
            worker.result()
        raise


async def _run_sync_cancellation_safe(
    func: Callable[..., _ResultT],
    *args: Any,
) -> _ResultT:
    """Run sync work without detaching its thread on task cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(func, *args))
    return await _await_task_cancellation_safe(worker)


async def _await_cancellation_safe(
    awaitable: Awaitable[_ResultT],
) -> _ResultT:
    """Finish async cleanup even when the caller is repeatedly cancelled."""
    worker = asyncio.ensure_future(awaitable)
    return await _await_task_cancellation_safe(worker)


def _run_in_user_storage(
    paths: "UserStoragePaths | None",
    func: Callable[..., Any],
    *args: Any,
) -> Any:
    """Execute a sync function under user storage paths.

    This reuses HenuPluginService._activate_user_storage for the actual
    global-path activation, including the shared _RUNTIME_STATE_LOCK logic.
    """
    if paths is None:
        return func(*args)

    service = HenuPluginService(Path(__file__).resolve().parent)
    with service._activate_user_storage(paths):
        return func(*args)


def set_current_user_paths(paths: "UserStoragePaths | None") -> None:
    """Set the current user's storage paths for the active execution context."""
    _CURRENT_USER_PATHS.set(paths)


def get_current_user_paths() -> "UserStoragePaths | None":
    """Get the current user's storage paths."""
    return _CURRENT_USER_PATHS.get()


def _text(value: Any, *, strip: bool = True) -> str:
    result = str(value or "")
    return result.strip() if strip else result


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default

    lowered = _text(value).lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_session_id(value: Any) -> str:
    text = _text(value)
    return "" if text in {"", "0", "None", "none"} else text


class HenuPluginService:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        # Storage is managed via LangBot Storage API
        # User paths are set via set_current_user_paths() before tool execution

        self._tool_dispatch: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "henu_cli": self._henu_cli,
            "setup_account": self._setup_account,
            "preferences": self._preferences,
            "sync_schedule": self._sync_schedule,
            "smart_course_selection": self._smart_course_selection,
            "smart_course_select": self._smart_course_select,
            "schedule_query": self._schedule_query,
            "course_selection_query": self._course_selection_query,
            "course_selection_plan": self._course_selection_plan,
            "course_selection_submit": self._course_selection_submit,
            "course_monitor_config": self._course_monitor_config,
            "course_monitor_once": self._course_monitor_once,
            "course_monitor_run": self._course_monitor_run,
            "course_monitor_notify_test": self._course_monitor_notify_test,
            "library_query": self._library_query,
            "library_reserve": self._library_reserve,
            "library_auto_signin": self._library_auto_signin,
            "library_cancel": self._library_cancel,
            "seminar_group": self._seminar_group,
            "seminar_query": self._seminar_query,
            "seminar_reserve": self._seminar_reserve,
            "seminar_signin": self._seminar_signin,
            "seminar_cancel": self._seminar_cancel,
            "set_calibration_source": self._set_calibration_source,
            "system_status": self._system_status,
            "yunfz_leave_query": self._yunfz_leave_query,
            "yunfz_signin_query": self._yunfz_signin_query,
            "yunfz_checksleep_query": self._yunfz_checksleep_query,
            "yunfz_activity_query": self._yunfz_activity_query,
            "yunfz_collection_query": self._yunfz_collection_query,
            "empty_classroom_query": self._empty_classroom_query,
            "empty_classroom_sync": self._empty_classroom_sync,
            "resource_registry_query": self._resource_registry_query,
            "resource_registry_sync": self._resource_registry_sync,
            "yuketang_status": self._yuketang_status,
            "yuketang_config_show": self._yuketang_config_show,
            "yuketang_set_enabled": self._yuketang_set_enabled,
            "yuketang_domain_set": self._yuketang_domain_set,
            "yuketang_lesson_set": self._yuketang_lesson_set,
            "yuketang_exam_set": self._yuketang_exam_set,
            "yuketang_list_update": self._yuketang_list_update,
            "yuketang_start_time": self._yuketang_start_time,
            "yuketang_set_token": self._yuketang_set_token,
            "yuketang_account_set": self._yuketang_account_set,
            "yuketang_login": self._yuketang_login,
            "yuketang_logout": self._yuketang_logout,
        }

    def get_sender_account_context(
        self,
        session: provider_session.Session,
        identity_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self._resolve_identity(session, identity_hint=identity_hint or {})
        cache_key = f"user:{identity.storage_key}:account_context"

        cached = ACCOUNT_CONTEXT_CACHE.get(cache_key)
        if cached is not None:
            return cached

        paths = self._build_storage_paths(identity)

        try:
            with self._activate_user_storage(paths):
                account_wrapper = henu_api.show_account()
        except Exception as exc:
            result = {
                "success": False,
                "msg": f"读取账号绑定失败: {exc}",
                "binding": {
                    "qq": identity.qq,
                    "storage_key": identity.storage_key,
                    "launcher_type": identity.launcher_type,
                    "launcher_id": identity.launcher_id,
                    "sender_id": identity.sender_id,
                },
                "account": {},
            }
            ACCOUNT_CONTEXT_CACHE.set(cache_key, result, ttl_seconds=60.0)
            return result

        account = account_wrapper.get("account") if isinstance(account_wrapper, dict) else {}
        if not isinstance(account, dict):
            account = {}

        student_id = _text(account.get("student_id"))
        login_verification = account.get("login_verification")
        if not isinstance(login_verification, dict):
            login_verification = {}
        last_login_attempt = account.get("last_login_attempt")
        if not isinstance(last_login_attempt, dict):
            last_login_attempt = {}
        result = {
            "success": True,
            "binding": {
                "qq": identity.qq,
                "storage_key": identity.storage_key,
                "launcher_type": identity.launcher_type,
                "launcher_id": identity.launcher_id,
                "sender_id": identity.sender_id,
            },
            "account": {
                "student_id": student_id,
                "is_bound": bool(student_id),
                "has_password": bool(account.get("has_password")),
                "library_default_location": _text(account.get("library_default_location")),
                "library_default_seat_no": _text(account.get("library_default_seat_no")),
                "has_seminar_mobile": bool(account.get("has_seminar_mobile")),
                "login_verification": {
                    "status": _text(login_verification.get("status")) or "unknown",
                    "verified": bool(login_verification.get("verified")),
                    "historically_verified": bool(login_verification.get("historically_verified")),
                    "attempted_at": _text(login_verification.get("attempted_at")),
                    "verified_at": _text(login_verification.get("verified_at")),
                    "subject_student_id": _text(login_verification.get("subject_student_id")),
                    "auth_mode": _text(login_verification.get("auth_mode")),
                    "error_code": _text(login_verification.get("error_code")),
                    "message": _text(login_verification.get("message")),
                },
                "last_login_attempt": {
                    "status": _text(last_login_attempt.get("status")) or "unknown",
                    "verified": bool(last_login_attempt.get("verified")),
                    "attempted_at": _text(last_login_attempt.get("attempted_at")),
                    "subject_student_id": _text(last_login_attempt.get("subject_student_id")),
                    "auth_mode": _text(last_login_attempt.get("auth_mode")),
                    "error_code": _text(last_login_attempt.get("error_code")),
                    "message": _text(last_login_attempt.get("message")),
                },
                "profile_file": _text(account.get("profile_file"), strip=False),
            },
        }
        ACCOUNT_CONTEXT_CACHE.set(cache_key, result)
        return result

    def get_time_snapshot(self, timezone: str = "Asia/Shanghai") -> dict[str, Any]:
        cache_key = f"server_time:{timezone}"

        cached = SERVER_TIME_CACHE.get(cache_key)
        if cached is not None:
            return cached

        snapshot = henu_api.get_server_time(timezone=timezone)
        if not isinstance(snapshot, dict):
            result = {
                "success": False,
                "timezone": timezone,
                "msg": "获取服务器时间失败",
            }
        else:
            result = snapshot

        SERVER_TIME_CACHE.set(cache_key, result)
        return result

    def get_runtime_context(
        self,
        session: provider_session.Session,
        identity_hint: dict[str, Any] | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> dict[str, Any]:
        identity = self._resolve_identity(session, identity_hint=identity_hint or {})
        cache_key = f"user:{identity.storage_key}:runtime_context:{timezone}"

        cached = RUNTIME_CONTEXT_CACHE.get(cache_key)
        if cached is not None:
            return cached

        account_context = self.get_sender_account_context(
            session,
            identity_hint=identity_hint,
        )
        server_time = self.get_time_snapshot(timezone=timezone)
        result = {
            "success": bool(account_context.get("success", True) and server_time.get("success", True)),
            "binding": account_context.get("binding") or {},
            "account": account_context.get("account") or {},
            "server_time": server_time,
        }
        RUNTIME_CONTEXT_CACHE.set(cache_key, result)
        return result

    def get_cache_statistics(self) -> dict[str, Any]:
        """Get cache statistics for monitoring."""
        return get_cache_stats()

    def run_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        session: provider_session.Session,
        query_id: int,
        identity_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handler = self._tool_dispatch.get(tool_name)
        if handler is None:
            return {"success": False, "msg": f"未知工具: {tool_name}"}

        identity = self._resolve_identity(session, identity_hint=identity_hint or {})
        is_pure = self.is_pure_request(tool_name, params)
        paths = None if is_pure else self._build_storage_paths(identity)

        try:
            _CURRENT_IDENTITY.value = identity
            if paths is None:
                result = handler(params or {})
            else:
                with self._activate_user_storage(paths):
                    result = handler(params or {})
        except Exception as exc:
            return {"success": False, "msg": f"{tool_name} 执行异常: {exc}"}
        finally:
            _CURRENT_IDENTITY.value = None

        # Account verification state changes even when authentication fails, so
        # invalidate its context after every setup_account attempt.
        if isinstance(result, dict):
            effective_tool = _text(result.get("_resolved_tool_name")) or tool_name
            if effective_tool == "setup_account":
                if result.get("success") and result.get("binding_changed"):
                    if paths is not None:
                        try:
                            paths.schedule_file.unlink(missing_ok=True)
                        except Exception:
                            paths.schedule_file.write_text("{}", encoding="utf-8")
                    invalidate_user_cache(identity.storage_key)
                else:
                    invalidate_account_cache(identity.storage_key)
            elif result.get("success") and effective_tool == "preferences":
                invalidate_account_cache(identity.storage_key)
            elif result.get("success") and effective_tool in {"sync_schedule"}:
                invalidate_schedule_cache(identity.storage_key)
            elif result.get("success") and effective_tool in {"library_reserve", "library_auto_signin", "library_cancel"}:
                LIBRARY_QUERY_CACHE.invalidate_pattern(f"user:{identity.storage_key}:")
            elif result.get("success") and effective_tool in {"seminar_reserve", "seminar_signin", "seminar_cancel"}:
                SEMINAR_QUERY_CACHE.invalidate_pattern(f"user:{identity.storage_key}:")

        if isinstance(result, dict) and paths is not None:
            self._decorate_result(result, tool_name, identity, paths, query_id)
        return result

    def is_pure_request(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> bool:
        """Return whether a request is guaranteed not to touch runtime state."""
        if tool_name in _PURE_TOOL_NAMES:
            return True
        if tool_name != "henu_cli":
            return False
        raw_command = _text((params or {}).get("command"), strip=False)
        spec = inspect_cli_command(raw_command)
        return bool(
            not spec.error
            and not spec.is_help
            and spec.resolved_tool in _PURE_TOOL_NAMES
        )

    async def run_tool_async(
        self,
        tool_name: str,
        params: dict[str, Any],
        session: provider_session.Session,
        query_id: int,
        identity_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run tools without blocking the LangBot event loop.

        Course monitoring is split into one-check runtime transactions so the
        process-wide runtime lock is released while the coroutine waits.
        """
        monitor_request = self._resolve_async_monitor_request(tool_name, params)
        if monitor_request is None:
            return await _run_sync_cancellation_safe(
                self.run_tool,
                tool_name,
                params,
                session,
                query_id,
                identity_hint,
            )

        monitor_params, cli_spec, raw_command = monitor_request
        identity = self._resolve_identity(session, identity_hint=identity_hint or {})
        paths = self._build_storage_paths(identity)
        limit_error = self._course_monitor_limit_error(monitor_params)
        if limit_error is None:
            result = await self._run_course_monitor_async(
                monitor_params,
                session,
                query_id,
                identity_hint,
            )
        else:
            result = limit_error

        if cli_spec is not None:
            self._decorate_cli_execution_result(result, cli_spec, raw_command)
            result = self._finalize_cli_result(result)
        self._decorate_result(result, tool_name, identity, paths, query_id)
        return result

    def _resolve_async_monitor_request(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], CliCommandSpec | None, str] | None:
        call_params = dict(params or {})
        if tool_name == "course_monitor_run":
            return call_params, None, ""
        if tool_name != "henu_cli":
            return None

        raw_command = _text(call_params.get("command"), strip=False)
        spec = inspect_cli_command(raw_command)
        if spec.error or spec.is_help or spec.resolved_tool != "course_monitor_run":
            return None
        return dict(spec.params), spec, raw_command

    def _course_monitor_limit_error(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        del params
        return None

    async def _run_course_monitor_async(
        self,
        params: dict[str, Any],
        session: provider_session.Session,
        query_id: int,
        identity_hint: dict[str, Any] | None,
    ) -> dict[str, Any]:
        max_checks = _int(params.get("max_checks"), 1)
        duration_seconds = _int(params.get("duration_seconds"), 0)
        if max_checks <= 0 and duration_seconds <= 0:
            max_checks = 1

        round_params = dict(params)
        round_params["max_checks"] = 1
        round_params["duration_seconds"] = 0
        started = asyncio.get_running_loop().time()
        checks = 0
        interval_seconds = 0
        results: list[dict[str, Any]] = []
        successes: list[bool] = []

        while True:
            round_result = await _run_sync_cancellation_safe(
                self.run_tool,
                "course_monitor_run",
                round_params,
                session,
                query_id,
                identity_hint,
            )
            if not isinstance(round_result, dict):
                round_result = {
                    "success": False,
                    "msg": "course_monitor_run 返回了非字典结果。",
                }
            successes.append(bool(round_result.get("success")))
            interval_seconds = max(
                _COURSE_MONITOR_MIN_INTERVAL_SECONDS,
                _int(
                    round_result.get("interval_seconds"),
                    _COURSE_MONITOR_MIN_INTERVAL_SECONDS,
                ),
            )
            round_results = round_result.get("results")
            if isinstance(round_results, list):
                results.extend(item for item in round_results if isinstance(item, dict))
            else:
                results.append(round_result)
            checks += max(1, _int(round_result.get("checks"), 0))

            if max_checks > 0 and checks >= max_checks:
                break

            elapsed = asyncio.get_running_loop().time() - started
            if duration_seconds > 0 and elapsed >= duration_seconds:
                break
            delay = float(interval_seconds)
            if duration_seconds > 0:
                delay = min(delay, max(0.0, duration_seconds - elapsed))
            await asyncio.sleep(delay)
            if (
                duration_seconds > 0
                and asyncio.get_running_loop().time() - started >= duration_seconds
            ):
                break

        return {
            "success": all(successes),
            "msg": "选课余量监控运行完成；未执行任何选课提交。",
            "checks": checks,
            "interval_seconds": interval_seconds,
            "results": results,
        }

    def _resolve_identity(
        self,
        session: provider_session.Session,
        identity_hint: dict[str, Any] | None = None,
    ) -> SessionIdentity:
        hint = identity_hint or {}
        launcher_type = getattr(
            getattr(session, "launcher_type", ""),
            "value",
            getattr(session, "launcher_type", ""),
        )
        launcher_type = str(launcher_type or "").lower()
        sender_id = _clean_session_id(session.sender_id)
        launcher_id = _clean_session_id(session.launcher_id)
        trusted = {
            "sender_id": sender_id,
            "launcher_id": launcher_id,
            "launcher_type": launcher_type,
        }
        for field, trusted_value in trusted.items():
            hinted_value = hint.get(field)
            if hinted_value in (None, ""):
                continue
            normalized_hint = _clean_session_id(
                getattr(hinted_value, "value", hinted_value)
            )
            if field == "launcher_type":
                normalized_hint = normalized_hint.lower()
            if normalized_hint != trusted_value:
                raise RuntimeError("调用身份与请求上下文不一致，拒绝访问 Storage")
        if launcher_type == "group" and not sender_id:
            raise RuntimeError("群聊缺少 sender_id，拒绝访问个人 Storage")
        if not sender_id and not launcher_id:
            raise RuntimeError("无法确认当前用户身份，拒绝访问 Storage")
        qq = sender_id or launcher_id or "unknown"

        storage_key = re.sub(r"[^0-9A-Za-z._-]+", "_", qq).strip("._-")
        if not storage_key:
            storage_key = hashlib.sha1(qq.encode("utf-8")).hexdigest()[:16]

        return SessionIdentity(
            qq=qq,
            storage_key=storage_key,
            launcher_type=launcher_type,
            launcher_id=launcher_id,
            sender_id=sender_id,
        )

    def _build_storage_paths(self, identity: SessionIdentity) -> UserStoragePaths:
        """Build storage paths from thread-local storage.

        The paths must be set by caller via set_current_user_paths() before
        calling run_tool().
        """
        paths = get_current_user_paths()
        if paths is None:
            raise RuntimeError(
                f"User storage paths not set for {identity.storage_key}. "
                "Call set_current_user_paths() before run_tool()."
            )
        return paths

    def _decorate_result(
        self,
        result: dict[str, Any],
        tool_name: str,
        identity: SessionIdentity,
        paths: UserStoragePaths,
        query_id: int,
    ) -> None:
        effective_tool_name = _text(result.get("_resolved_tool_name")) or tool_name

        if effective_tool_name in {"setup_account", "system_status"}:
            result["session_binding"] = {
                "qq": identity.qq,
                "launcher_type": identity.launcher_type,
                "launcher_id": identity.launcher_id,
                "sender_id": identity.sender_id,
                "query_id": query_id,
            }

        if effective_tool_name == "system_status":
            result["storage"] = {
                "mode": "langbot_storage_api",
                "has_schedule_cache": paths.schedule_file.exists(),
                "has_output_dir": paths.output_dir.exists(),
                "has_shared_period_config": (paths.shared_data_dir / SHARED_PERIOD_TIME_FILE).exists(),
            }

        if effective_tool_name == "seminar_reserve":
            result["auto_signin_mode"] = "manual_scan_only"
            if result.get("success"):
                note = "插件版不会启动后台自动签到线程，请在签到时间前后再次调用 seminar_signin(auto_scan=true) 或 seminar_signin(record_id=...)。"
                result["msg"] = f"{_text(result.get('msg'))}；{note}".strip("；")

    def _henu_cli(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._finalize_cli_result(self._execute_henu_cli(params))

    def _execute_henu_cli(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_command = _text(params.get("command"), strip=False)
        spec = inspect_cli_command(raw_command)
        safe_command = redact_cli_command(raw_command)

        if spec.is_help:
            help_payload = build_help_payload(spec.help_topic)
            return {
                "success": True,
                "msg": help_payload["summary"],
                "cli": {
                    "mode": "help",
                    "command": safe_command,
                    "topic": help_payload["topic"],
                },
                "commands": help_payload["commands"],
                "examples": help_payload["examples"],
                "tips": help_payload["tips"],
                "next_commands": build_next_commands(spec),
            }

        if spec.error:
            help_payload = build_help_payload(spec.help_topic)
            error_result = {
                "success": False,
                "msg": spec.error,
                "cli": {
                    "mode": "error",
                    "command": safe_command,
                    "topic": help_payload["topic"],
                },
                "commands": help_payload["commands"],
                "examples": help_payload["examples"],
                "tips": help_payload["tips"],
                "next_commands": build_next_commands(spec),
            }
            if spec.missing_params:
                error_result["error_code"] = "missing_params"
                error_result["missing_params"] = list(spec.missing_params)
            return error_result

        if not spec.resolved_tool:
            return {
                "success": False,
                "msg": "命令未解析到具体动作，请先执行 `help`。",
                "cli": {"mode": "error", "command": safe_command},
                "next_commands": ["help"],
            }

        identity = getattr(_CURRENT_IDENTITY, "value", None)
        if (
            spec.resolved_tool == "setup_account"
            and identity is not None
            and (identity.launcher_type != "person" or not identity.sender_id)
        ):
            return {
                "success": False,
                "error_code": "private_chat_required",
                "msg": "账号密码只允许在可确认发送者身份的私聊中绑定，本次未执行登录。",
                "cli": {
                    "mode": "error",
                    "command": safe_command,
                    "resolved_tool": spec.resolved_tool,
                },
                "next_commands": ["help account set"],
            }

        handler = self._tool_dispatch.get(spec.resolved_tool)
        if handler is None or spec.resolved_tool == "henu_cli":
            return {
                "success": False,
                "msg": f"CLI 路由失败：未找到 `{spec.resolved_tool}`。",
                "cli": {"mode": "error", "command": safe_command, "resolved_tool": spec.resolved_tool},
                "next_commands": ["help"],
            }

        execution_params = dict(spec.params)
        if spec.resolved_tool == "library_reserve" and isinstance(params.get("_library_expected_plan"), dict):
            execution_params["expected_plan"] = params["_library_expected_plan"]
        result = handler(execution_params)
        if not isinstance(result, dict):
            result = {
                "success": False,
                "msg": f"{spec.resolved_tool} 返回了非字典结果。",
                "outcome_unknown": True,
            }

        self._decorate_cli_execution_result(result, spec, raw_command)
        return result

    def _finalize_cli_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return result

    def _decorate_cli_execution_result(
        self,
        result: dict[str, Any],
        spec: CliCommandSpec,
        raw_command: str,
    ) -> None:
        result.setdefault("cli", {})
        if isinstance(result["cli"], dict):
            result["cli"].update(
                {
                    "mode": "exec",
                    "command": redact_cli_command(raw_command),
                    "action": spec.action,
                    "resolved_tool": spec.resolved_tool,
                }
            )
        result["_resolved_tool_name"] = spec.resolved_tool
        result["_effective_params"] = redact_cli_params(spec.params)
        if spec.resolved_tool == "library_query" and spec.params.get("view") == "seats":
            result.setdefault("defaulted_fields", list(spec.params.get("defaulted_fields") or []))
        result["next_commands"] = build_next_commands(spec, result)

    @contextlib.contextmanager
    def _activate_user_storage(self, paths: UserStoragePaths):
        """Activate user storage paths for file operations.

        This sets global variables in the shared campus implementation modules
        to point to the user-specific paths.
        Also sets storage_paths base dir for shared cache (data/shared/).
        """
        paths.user_root.mkdir(parents=True, exist_ok=True)
        paths.output_dir.mkdir(parents=True, exist_ok=True)

        shared_dir = paths.shared_data_dir
        shared_dir.mkdir(parents=True, exist_ok=True)

        with _RUNTIME_STATE_LOCK:
            runtime = LangBotStorageRuntime(self.base_dir, paths)
            with runtime.activate("langbot-storage"):
                yield

    def _setup_account(self, params: dict[str, Any]) -> dict[str, Any]:
        identity = getattr(_CURRENT_IDENTITY, "value", None)
        if identity is not None and (identity.launcher_type != "person" or not identity.sender_id):
            return {
                "success": False,
                "error_code": "private_chat_required",
                "msg": "账号密码只允许在可确认发送者身份的私聊中绑定，本次未执行登录。",
            }
        return henu_api.setup_account(
            student_id=_text(params.get("student_id")),
            password=_text(params.get("password"), strip=False),
            library_location=_text(params.get("library_location")),
            library_seat_no=_text(params.get("library_seat_no")),
            verify_login=_bool(params.get("verify_login"), True),
            calibrate_period_time=_bool(params.get("calibrate_period_time"), True),
        )

    def _preferences(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (_text(params.get("action")) or "get").lower()

        if action == "set":
            fields: dict[str, str] = {}
            location = _text(params.get("library_location"))
            seat_no = _text(params.get("library_seat_no"))
            if location:
                fields["library_location"] = location
            if seat_no:
                fields["library_seat_no"] = seat_no
            if not fields:
                return {
                    "success": False,
                    "error_code": "missing_params",
                    "missing_params": ["library_location", "library_seat_no"],
                    "msg": "请至少提供一个要设置的偏好。",
                }
            server_impl._save_profile_fields(fields)
        elif action == "clear":
            fields = {}
            if _bool(params.get("clear_library_location"), False):
                fields["library_location"] = ""
            if _bool(params.get("clear_library_seat_no"), False):
                fields["library_seat_no"] = ""
                fields["seat_no"] = ""
            if _bool(params.get("clear_library_location"), False):
                fields["location"] = ""
            if not fields:
                fields = {
                    "library_location": "",
                    "library_seat_no": "",
                    "location": "",
                    "seat_no": "",
                }
            server_impl._save_profile_fields(fields)
        elif action != "get":
            return {
                "success": False,
                "error_code": "invalid_action",
                "msg": f"不支持的 preferences 动作: {action}",
            }

        current = server_impl._effective_profile()
        preferences = {
            "library_location": _text(
                current.get("library_location") or current.get("location")
            ),
            "library_seat_no": _text(
                current.get("library_seat_no") or current.get("seat_no")
            ),
        }
        action_text = {"get": "读取", "set": "更新", "clear": "清空"}[action]
        return {
            "success": True,
            "msg": f"偏好已{action_text}",
            "preferences": preferences,
        }


    def _smart_course_selection(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.smart_course_selection(
            source_path=_text(params.get("source_path") or params.get("source") or params.get("excel_path") or params.get("excel")),
            excel_path=_text(params.get("excel_path") or params.get("excel")),
            json_path=_text(params.get("json_path") or params.get("json")),
            user_class=_text(params.get("user_class") or params.get("class")),
            sheet_name=_text(params.get("sheet_name") or params.get("sheet")) or "2026-2027-1学期",
            semester=_text(params.get("semester")),
            mode=_text(params.get("mode")) or "plan",
            like_early8=_bool(params.get("like_early8"), False),
            avoid_early8=_bool(params.get("avoid_early8"), False),
            compact_days=_bool(params.get("compact_days"), False),
            target_days=_int(params.get("target_days"), 3),
            avoid_evening=_bool(params.get("avoid_evening"), False),
            allow_unscheduled=_bool(params.get("allow_unscheduled"), True),
            include_common=_bool(params.get("include_common"), True),
            include_course_options=_bool(params.get("include_course_options"), False),
            top_k=_int(params.get("top_k"), 3),
            max_combinations=_int(params.get("max_combinations"), 200000),
        )

    def _smart_course_select(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._smart_course_selection(params)

    def _sync_schedule(self, params: dict[str, Any]) -> dict[str, Any]:
        result = henu_api.sync_schedule(
            xn=_text(params.get("xn")) or None,
            xq=_text(params.get("xq")) or None,
            auto_calibrate=_bool(params.get("auto_calibrate"), True),
        )
        # Invalidate schedule cache after sync
        identity = getattr(_CURRENT_IDENTITY, "value", None)
        if result.get("success") and identity:
            SCHEDULE_CACHE.invalidate_pattern(f"user:{identity.storage_key}:")
            # Save schedule to Storage after successful sync
            paths = get_current_user_paths()
            if paths and paths.output_dir:
                import json
                schedule_files = list(paths.output_dir.glob("schedule_clean_*.json"))
                if schedule_files:
                    latest = max(schedule_files, key=lambda p: p.stat().st_mtime)
                    try:
                        schedule_data = json.loads(latest.read_text(encoding="utf-8"))
                        schedule_data["_subject_student_id"] = str(
                            server_impl._effective_profile().get("student_id") or ""
                        ).strip()
                        paths.schedule_file.write_text(
                            json.dumps(schedule_data, ensure_ascii=False, indent=2),
                            encoding="utf-8"
                        )
                    except Exception:
                        pass
        return result

    def _schedule_query(self, params: dict[str, Any]) -> dict[str, Any]:
        identity = getattr(_CURRENT_IDENTITY, "value", None)
        view = _text(params.get("view")) or "current"
        timezone = _text(params.get("timezone")) or "Asia/Shanghai"
        target_date = _text(params.get("target_date")) or ""

        # Try to restore schedule from Storage if not in output_dir
        paths = get_current_user_paths()
        if paths and paths.output_dir:
            schedule_files = list(paths.output_dir.glob("schedule_clean_*.json"))
            if not schedule_files and paths.schedule_file.exists():
                try:
                    import json
                    schedule_data = json.loads(paths.schedule_file.read_text(encoding="utf-8"))
                    bound_sid = str(server_impl._effective_profile().get("student_id") or "").strip()
                    if bound_sid and str(schedule_data.get("_subject_student_id") or "").strip() == bound_sid:
                        target = paths.output_dir / "schedule_clean_latest.json"
                        target.write_text(
                            json.dumps(schedule_data, ensure_ascii=False, indent=2),
                            encoding="utf-8"
                        )
                    else:
                        paths.schedule_file.unlink(missing_ok=True)
                except Exception:
                    pass

        # Current/now are wall-clock dependent and must never be reused.
        if identity and view in {"week", "full"}:
            cache_key = f"user:{identity.storage_key}:schedule:{view}:{target_date}"
            cached = SCHEDULE_CACHE.get(cache_key)
            if cached is not None:
                return cached

        result = henu_api.schedule_query(
            view=view,
            timezone=timezone,
            target_date=target_date,
            auto_calibrate=_bool(params.get("auto_calibrate"), True),
        )

        if result.get("success") and identity and view in {"week", "full"}:
            SCHEDULE_CACHE.set(cache_key, result, ttl_seconds=300.0)

        return result

    def _library_query(self, params: dict[str, Any]) -> dict[str, Any]:
        identity = getattr(_CURRENT_IDENTITY, "value", None)
        view = (_text(params.get("view")) or "current").strip().lower()
        record_type = _text(params.get("record_type")) or "1"
        page = _int(params.get("page"), 1)
        limit = _int(params.get("limit"), 20)
        target_date = _text(params.get("target_date"))
        location = _text(params.get("location"))
        area_id = _text(params.get("area_id"))
        preferred_time = _text(params.get("preferred_time")) or "08:00"
        preferred_end_time = _text(params.get("preferred_end_time"))

        # Try cache for read operations
        if identity:
            cache_key = (
                f"user:{identity.storage_key}:library:{view}:{record_type}:{page}:{limit}:"
                f"{target_date}:{location}:{area_id}:{preferred_time}:{preferred_end_time}"
            )
            cached = LIBRARY_QUERY_CACHE.get(cache_key)
            if cached is not None:
                return cached

        result = henu_api.library_query(
            view=view,
            record_type=record_type,
            page=page,
            limit=limit,
            target_date=target_date,
            location=location,
            area_id=area_id,
            preferred_time=preferred_time,
            preferred_end_time=preferred_end_time,
        )

        # Cache successful queries
        if result.get("success") and identity:
            ttl_seconds = 20.0 if view == "seats" else None
            LIBRARY_QUERY_CACHE.set(cache_key, result, ttl_seconds=ttl_seconds)

        return result

    def _course_selection_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.course_selection_query(
            view=_text(params.get("view")) or "status",
            xktype=_text(params.get("xktype")) or "2",
        )

    def _course_selection_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.course_selection_plan(
            candidates_json=_text(params.get("candidates_json")),
            existing_schedule_json=_text(params.get("existing_schedule_json")),
            preferences_json=_text(params.get("preferences_json")),
            top_k=_int(params.get("top_k"), 10),
        )

    def _course_selection_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.course_selection_submit(
            payload_json=_text(params.get("payload_json")),
        )

    def _course_monitor_config(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.course_monitor_config(
            config_json=_text(params.get("config_json")),
            merge=bool(params.get("merge", True)),
        )

    def _course_monitor_once(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.course_monitor_once(
            config_json=_text(params.get("config_json")),
            send_notifications=bool(params.get("send_notifications", True)),
        )

    def _course_monitor_run(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.course_monitor_run(
            config_json=_text(params.get("config_json")),
            max_checks=_int(params.get("max_checks"), 1),
            duration_seconds=_int(params.get("duration_seconds"), 0),
            send_notifications=bool(params.get("send_notifications", True)),
        )

    def _course_monitor_notify_test(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.course_monitor_notify_test(
            config_json=_text(params.get("config_json")),
        )

    def _library_reserve(self, params: dict[str, Any]) -> dict[str, Any]:
        target_date = _text(params.get("target_date"))
        preferred_time = _text(params.get("preferred_time"))
        preferred_end_time = _text(params.get("preferred_end_time"))
        end_policy = _text(params.get("end_policy")).lower()
        if preferred_end_time and not end_policy:
            end_policy = "explicit_end"

        missing_params = []
        if not target_date:
            missing_params.append("target_date")
        if not preferred_time:
            missing_params.append("preferred_time")
        if not end_policy:
            missing_params.append("end_policy")
        if missing_params:
            return {
                "success": False,
                "error_code": "missing_params",
                "missing_params": missing_params,
                "msg": (
                    "图书馆预约缺少用户明确授权的参数："
                    + ", ".join(missing_params)
                    + "。日期和开始时间不得使用查询默认值；结束策略请选择 "
                    "ask 或 server_full_slot。"
                ),
            }

        if end_policy not in {"ask", "server_full_slot", "explicit_end"}:
            return {
                "success": False,
                "error_code": "invalid_end_policy",
                "msg": "end_policy 仅支持 ask、server_full_slot 或 explicit_end。",
            }
        if end_policy in {"ask", "explicit_end"} and not preferred_end_time:
            return {
                "success": False,
                "error_code": "missing_params",
                "missing_params": ["preferred_end_time"],
                "msg": (
                    "end_policy=ask/explicit_end 时必须先取得用户明确的 "
                    "--end-time。"
                ),
            }

        result = henu_api.library_reserve(
            expected_plan=params.get("expected_plan"),
            location=_text(params.get("location")),
            seat_no=_text(params.get("seat_no")),
            target_date=target_date,
            preferred_time=preferred_time,
            preferred_end_time=(
                "" if end_policy == "server_full_slot" else preferred_end_time
            ),
            retry_until=_text(params.get("retry_until")),
            retry_interval_seconds=_int(params.get("retry_interval_seconds"), 2),
            max_attempts=_int(params.get("max_attempts"), 1),
        )
        if isinstance(result, dict):
            result.setdefault("end_policy", end_policy)
        return result

    def _library_auto_signin(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.library_auto_signin(
            record_id=_text(params.get("record_id")),
        )

    def _library_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.library_cancel(
            record_id=_text(params.get("record_id")),
            record_type=_text(params.get("record_type")) or "auto",
        )

    def _seminar_group(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.seminar_group(
            action=_text(params.get("action")) or "list",
            group_name=_text(params.get("group_name")),
            member_ids=_text(params.get("member_ids")),
            note=_text(params.get("note")),
        )

    def _seminar_query(self, params: dict[str, Any]) -> dict[str, Any]:
        identity = getattr(_CURRENT_IDENTITY, "value", None)
        view = _text(params.get("view")) or "rooms"
        target_date = _text(params.get("target_date")) or ""
        page = _int(params.get("page"), 1)

        # Try cache for read operations
        if identity and view in {"rooms", "filters", "detail"}:
            cache_key = f"user:{identity.storage_key}:seminar:{view}:{target_date}:{page}"
            cached = SEMINAR_QUERY_CACHE.get(cache_key)
            if cached is not None:
                return cached

        result = henu_api.seminar_query(
            view=view,
            target_date=target_date,
            members=_int(params.get("members"), 0),
            name=_text(params.get("name")),
            room=_text(params.get("room")),
            start_time=_text(params.get("start_time")),
            end_time=_text(params.get("end_time")),
            library_ids=_text(params.get("library_ids")),
            library_names=_text(params.get("library_names")),
            floor_ids=_text(params.get("floor_ids")),
            floor_names=_text(params.get("floor_names")),
            category_ids=_text(params.get("category_ids")),
            category_names=_text(params.get("category_names")),
            boutique_ids=_text(params.get("boutique_ids")),
            boutique_names=_text(params.get("boutique_names")),
            page=page,
            area_id=_text(params.get("area_id")),
            record_type=_text(params.get("record_type")) or "1",
            limit=_int(params.get("limit"), 20),
            mode=_text(params.get("mode")) or "books",
            status=_text(params.get("status")),
        )

        # Cache successful queries
        if result.get("success") and identity and view in {"rooms", "filters", "detail"}:
            SEMINAR_QUERY_CACHE.set(cache_key, result)

        return result

    def _seminar_reserve(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.seminar_reserve(
            area_id=_text(params.get("area_id")),
            target_date=_text(params.get("target_date")),
            start_time=_text(params.get("start_time")),
            end_time=_text(params.get("end_time")),
            end_date=_text(params.get("end_date")),
            title=_text(params.get("title")),
            title_id=_text(params.get("title_id")),
            content=_text(params.get("content")),
            mobile=_text(params.get("mobile")),
            group_name=_text(params.get("group_name")),
            member_ids=_text(params.get("member_ids")),
            is_open=_int(params.get("is_open"), 0),
            cate_id=_text(params.get("cate_id")),
            time_ranges_json=_text(params.get("time_ranges_json")),
        )

    def _seminar_signin(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.seminar_signin(
            record_id=_text(params.get("record_id")),
            auto_scan=_bool(params.get("auto_scan"), False),
        )

    def _seminar_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.seminar_cancel(
            record_id=_text(params.get("record_id")),
        )

    def _set_calibration_source(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.set_calibration_source(
            data=_text(params.get("data"), strip=False),
            cookie=_text(params.get("cookie"), strip=False),
            user_agent=_text(params.get("user_agent"), strip=False)
            or "KingoPalm/2.6.449 (iPhone; iOS 26.3; Scale/3.00)",
        )

    def _system_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.system_status(
            timezone=_text(params.get("timezone")) or "Asia/Shanghai",
        )

    def _yunfz_leave_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.yunfz_leave_query(
            view=_text(params.get("view")) or "list",
            leave_id=_text(params.get("leave_id")),
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_signin_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.yunfz_signin_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_checksleep_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.yunfz_checksleep_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_activity_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.yunfz_activity_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_collection_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.yunfz_collection_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _empty_classroom_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.empty_classroom_query(
            view=_text(params.get("view")) or "free",
            term_code=_text(params.get("term_code")),
            week=_int(params.get("week"), 0),
            day_of_week=_int(params.get("day_of_week"), 0),
            period=_int(params.get("period"), 0),
            campus_code=_text(params.get("campus_code")),
            building_code=_text(params.get("building_code")),
            campus_text=_text(params.get("campus_text")),
            building_text=_text(params.get("building_text")),
            classroom_text=_text(params.get("classroom_text")),
            type_code=_text(params.get("type_code")),
            min_capacity=_int(params.get("min_capacity"), 0),
            keyword=_text(params.get("keyword")),
            room_id=_text(params.get("room_id")),
            freshness=_text(params.get("freshness")) or "cache_first",
            force_refresh=_bool(params.get("force_refresh"), False),
            ttl_seconds=_int(params.get("ttl_seconds"), 300),
            max_stale_seconds=_int(params.get("max_stale_seconds"), 86400),
        )

    def _empty_classroom_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.empty_classroom_sync(
            term_code=_text(params.get("term_code")),
            campus_code=_text(params.get("campus_code")),
            building_code=_text(params.get("building_code")),
            type_code=_text(params.get("type_code")),
            force_refresh=_bool(params.get("force_refresh"), False),
        )

    def _resource_registry_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.resource_registry_query(
            view=_text(params.get("view")) or "search",
            query=_text(params.get("query")),
            resource_type=_text(params.get("resource_type")),
            campus_code=_text(params.get("campus_code")),
            building_code=_text(params.get("building_code")),
            limit=_int(params.get("limit"), 20),
        )

    def _resource_registry_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        return henu_api.resource_registry_sync(
            scope=_text(params.get("scope")) or "all",
            force_refresh=_bool(params.get("force_refresh"), False),
        )

    def _yuketang_config_path(self) -> Path:
        paths = get_current_user_paths()
        if paths is None or not getattr(paths, "yuketang_config_file", None):
            raise RuntimeError("User storage paths not set for yuketang config.")
        return paths.yuketang_config_file

    def _current_openid(self) -> str:
        identity = getattr(_CURRENT_IDENTITY, "value", None)
        return (identity.qq if identity else "") or ""

    def _bridge_push(self, config: dict[str, Any]) -> dict[str, Any]:
        """配置写成功后的尽力推送。桥不可达不影响本地成功语义。"""
        try:
            if bridge_client.bridge_settings() is None:
                return {"status": "disabled"}
            openid = self._current_openid()
            if not openid:
                return {"status": "no_identity"}
            result = bridge_client.push_config(
                openid, yuketang_config.sanitize_config(config)
            )
            if result.get("ok"):
                return {"status": "ok", "hash": _text(result.get("hash"))}
            return {"status": "rejected", "detail": _text(result.get("msg"))}
        except bridge_client.BridgeError as exc:
            return {"status": "unreachable", "detail": str(exc)}

    def _bridge_merge_status(self, result: dict[str, Any]) -> dict[str, Any]:
        """status 查询时合并守护进程实时状态；哈希不一致顺手重推自愈。"""
        try:
            if bridge_client.bridge_settings() is None:
                return result
            openid = self._current_openid()
            if not openid:
                return result
            live = bridge_client.fetch_status(openid)
            if not live.get("ok"):
                return result

            notes: list[str] = []
            cookie = live.get("cookie") if isinstance(live.get("cookie"), dict) else None
            if cookie:
                who = _text(cookie.get("username")) or openid[-6:]
                expires = _text(cookie.get("expires_at"))
                notes.append(
                    f"登录状态：已登录（{who}，有效期至 {expires or '未知'}）"
                    if expires else f"登录状态：已登录（{who}，有效期未知）"
                )
            else:
                notes.append("登录状态：未登录（请私聊执行 yuketang login，使用已保存的雨课堂账号密码）")

            local_config = yuketang_config.load_config(self._yuketang_config_path())
            local_hash = bridge_client.canonical_hash(
                yuketang_config.sanitize_config(local_config)
            )
            if not live.get("config_present") or _text(live.get("config_hash")) != local_hash:
                result['_yuketang_sync_required'] = True
                result['_yuketang_openid'] = openid
                notes.append("桥端配置缺失或不同，将在本地持久化完成后同步。")

            uptime = _int(live.get("uptime_s"), 0)
            wired = live.get('daemon_wired') is True
            notes.append(f"桥服务：已连接（在线 {uptime} 秒）")
            notes.append("任务执行器：已接入" if wired else "任务执行器：桥端报告尚未接入，不能确认自动化会执行。")
            result["bridge"] = {"status": "ok", "uptime_s": uptime, "daemon_wired": wired}

            lines = _text(result.get("reply_text"), strip=False).split("\n")
            merged = [notes.pop(0) if line.startswith("登录状态：") else line for line in lines]
            merged = ["桥服务：已连接" if line.startswith("守护进程：") else line for line in merged]
            for note in notes:
                merged.append(note)
            result["reply_text"] = "\n".join(merged)
        except bridge_client.BridgeError:
            lines = _text(result.get("reply_text"), strip=False).split("\n")
            result["reply_text"] = "\n".join(
                "守护进程：不可达（无法确认远端运行状态，下次查询将重试）"
                if line.startswith("守护进程：") else line
                for line in lines
            )
            result["bridge"] = {"status": "unreachable"}
            result['_yuketang_sync_required'] = True
            result['_yuketang_openid'] = self._current_openid()
        except Exception:
            pass
        return result

    def _yuketang_read(self) -> dict[str, Any]:
        return yuketang_config.load_config(self._yuketang_config_path())

    def _yuketang_apply(
        self, applier: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        config_file = self._yuketang_config_path()
        config = yuketang_config.load_config(config_file)
        result = applier(config)
        if isinstance(result, dict) and result.get("success"):
            yuketang_config.save_config(config_file, config)
            result['_yuketang_sync_required'] = True
            result['_yuketang_openid'] = self._current_openid()
        return result

    def _yuketang_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._bridge_merge_status(
            yuketang_config.build_status_result(self._yuketang_read())
        )

    def _yuketang_config_show(self, params: dict[str, Any]) -> dict[str, Any]:
        return yuketang_config.build_config_show_result(
            self._yuketang_read(), _text(params.get("topic"))
        )

    def _yuketang_set_enabled(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_enabled(config, params.get("enabled"))
        )

    def _yuketang_domain_set(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_domain(config, params.get("domain"))
        )

    def _yuketang_lesson_set(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_lesson_set(config, params)
        )

    def _yuketang_exam_set(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_exam_set(config, params)
        )

    def _yuketang_list_update(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_list_update(
                config,
                _text(params.get("scope")),
                _text(params.get("op")),
                params.get("items") or [],
            )
        )

    def _yuketang_start_time(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_start_time(
                config,
                _text(params.get("op")),
                params.get("course"),
                params.get("slots"),
            )
        )

    def _yuketang_set_token(self, params: dict[str, Any]) -> dict[str, Any]:
        token = params.get("x_access_token")
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_token(config, token)
        )

    def _yuketang_account_set(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._yuketang_apply(
            lambda config: yuketang_config.apply_account_set(
                config, params.get("account"), params.get("password")
            )
        )

    def _yuketang_login(self, params: dict[str, Any]) -> dict[str, Any]:
        """仅启动登录。监听器在 Storage 事务结束后异步等待结果。"""
        openid = self._current_openid()
        config = yuketang_config.load_config(self._yuketang_config_path())
        account = _text(config.get("credentials", {}).get("account"))
        password = str(config.get("credentials", {}).get("password") or "")
        if not account or not password:
            return {
                "success": False,
                "msg": "未绑定雨课堂账号",
                "error_code": "account_unbound",
                "reply_text": "尚未绑定雨课堂账号。请先在雨课堂设置密码，使用雨课堂绑定手机号和该密码（不是学校 IDS 密码）。然后私聊发送：yuketang account set --account <手机号> --password '<密码>'，按提示确认后开始验证。尖括号仅为占位说明，请勿原样填写。",
            }
        force = bool(params.get("force"))
        if not force:
            try:
                live = bridge_client.fetch_status(openid)
                cookie = live.get("cookie") if isinstance(live.get("cookie"), dict) else None
                expires_ms = _int((cookie or {}).get("expires_ms"), 0)
                if live.get("ok") and cookie and expires_ms > int(time.time() * 1000) + 3600_000:
                    who = _text(cookie.get("username")) or "已登录"
                    expires = _text(cookie.get("expires_at"))
                    return {
                        "success": True,
                        "msg": "雨课堂已登录，未重新登录",
                        "reply_text": (
                            f"当前已登录：{who}，cookie 有效期至 {expires or '未知'}；本次未重复登录。\n"
                            "如需强制重新登录（例如换了密码）：yuketang login --force"
                        ),
                    }
            except bridge_client.BridgeError:
                pass  # 桥不可达时照旧走登录流程，由登录请求报错
        try:
            started = bridge_client.login_password(openid, account, password)
        except bridge_client.BridgeError as exc:
            return {
                "success": False,
                "msg": f"桥不可达: {exc}",
                "reply_text": "守护进程桥不可达，稍后再试 `yuketang login`（配置不受影响）。",
            }
        if not started.get("ok"):
            return {"success": False, "error_code": "bridge_login_rejected", "msg": "桥端未接受登录请求",
                    "reply_text": "桥端未接受登录请求，请稍后查询状态。"}
        session_id = _text(started.get("session_id"))
        if not session_id:
            return {'success': False, 'msg': '桥端未返回有效登录会话。'}
        return {'success': True, 'login_session_id': session_id, 'listening_enabled': bool(config.get('enabled')),
                'msg': '雨课堂登录已启动，尚未验证成功。'}

    def _yuketang_logout(self, params: dict[str, Any]) -> dict[str, Any]:
        """Persist stop first; the transaction owner contacts the bridge later."""
        result = self._yuketang_apply(yuketang_config.apply_logout)
        if result.get('success'):
            result['_yuketang_logout_required'] = True
            result['_yuketang_logout_uncertain'] = bool(params.get('login_inflight_unknown'))
            result['reply_text'] = '本地监听已停用，正在请求桥端清除登录态；绑定账号与配置保留。'
        return result


def _run_in_user_storage(storage_paths: UserStoragePaths, func: Callable[..., Any], *args: Any) -> Any:
    base_dir = Path(__file__).resolve().parents[1]
    service = HenuPluginService(base_dir)
    with service._activate_user_storage(storage_paths):
        return func(*args)
