from __future__ import annotations

import contextlib
import contextvars
import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from langbot_plugin.api.entities.builtin.provider import session as provider_session

import course_schedule
import mcp_server
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
)
from henu_plugin.cli import build_help_payload, build_next_commands, inspect_cli_command
from henu_plugin.storage_adapter import (
    SHARED_CALIBRATION_FILE,
    SHARED_PERIOD_TIME_FILE,
    SHARED_XIQUEER_FILE,
)


_RUNTIME_STATE_LOCK = threading.RLock()
_CURRENT_IDENTITY: threading.local = threading.local()


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


@dataclass(frozen=True)
class UserStoragePaths:
    user_root: Path
    profile_file: Path
    xk_cookie_file: Path
    library_cookie_file: Path
    seminar_signin_task_file: Path
    schedule_file: Path
    yunfz_token_file: Path
    cas_cookie_file: Path
    output_dir: Path
    shared_data_dir: Path  # 公共共享缓存目录（data/shared/）


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
                account_wrapper = mcp_server.show_account()
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

        snapshot = mcp_server.get_server_time(timezone=timezone)
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
        paths = self._build_storage_paths(identity)

        try:
            _CURRENT_IDENTITY.value = identity
            with self._activate_user_storage(paths):
                result = handler(params or {})
        except Exception as exc:
            return {"success": False, "msg": f"{tool_name} 执行异常: {exc}"}
        finally:
            _CURRENT_IDENTITY.value = None

        # Cache invalidation after successful writes
        if isinstance(result, dict) and result.get("success"):
            effective_tool = _text(result.get("_resolved_tool_name")) or tool_name
            if effective_tool in {"setup_account"}:
                invalidate_account_cache(identity.storage_key)
            elif effective_tool in {"sync_schedule"}:
                invalidate_schedule_cache(identity.storage_key)
            elif effective_tool in {"library_reserve", "library_auto_signin", "library_cancel"}:
                LIBRARY_QUERY_CACHE.invalidate_pattern(f"user:{identity.storage_key}:")
            elif effective_tool in {"seminar_reserve", "seminar_signin", "seminar_cancel"}:
                SEMINAR_QUERY_CACHE.invalidate_pattern(f"user:{identity.storage_key}:")

        if isinstance(result, dict):
            self._decorate_result(result, tool_name, identity, paths, query_id)
        return result

    def _resolve_identity(
        self,
        session: provider_session.Session,
        identity_hint: dict[str, Any] | None = None,
    ) -> SessionIdentity:
        hint = identity_hint or {}
        sender_id = _clean_session_id(hint.get("sender_id")) or _clean_session_id(session.sender_id)
        launcher_id = _clean_session_id(hint.get("launcher_id")) or _clean_session_id(session.launcher_id)
        qq = sender_id or launcher_id or "unknown"

        storage_key = re.sub(r"[^0-9A-Za-z._-]+", "_", qq).strip("._-")
        if not storage_key:
            storage_key = hashlib.sha1(qq.encode("utf-8")).hexdigest()[:16]

        return SessionIdentity(
            qq=qq,
            storage_key=storage_key,
            launcher_type=session.launcher_type.value,
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
        raw_command = _text(params.get("command"), strip=False)
        spec = inspect_cli_command(raw_command)

        if spec.is_help:
            help_payload = build_help_payload(spec.help_topic)
            return {
                "success": True,
                "msg": help_payload["summary"],
                "cli": {
                    "mode": "help",
                    "command": raw_command,
                    "topic": help_payload["topic"],
                },
                "commands": help_payload["commands"],
                "examples": help_payload["examples"],
                "tips": help_payload["tips"],
                "next_commands": build_next_commands(spec),
            }

        if spec.error:
            help_payload = build_help_payload(spec.help_topic)
            return {
                "success": False,
                "msg": spec.error,
                "cli": {
                    "mode": "error",
                    "command": raw_command,
                    "topic": help_payload["topic"],
                },
                "commands": help_payload["commands"],
                "examples": help_payload["examples"],
                "tips": help_payload["tips"],
                "next_commands": build_next_commands(spec),
            }

        if not spec.resolved_tool:
            return {
                "success": False,
                "msg": "命令未解析到具体动作，请先执行 `help`。",
                "cli": {"mode": "error", "command": raw_command},
                "next_commands": ["help"],
            }

        handler = self._tool_dispatch.get(spec.resolved_tool)
        if handler is None or spec.resolved_tool == "henu_cli":
            return {
                "success": False,
                "msg": f"CLI 路由失败：未找到 `{spec.resolved_tool}`。",
                "cli": {"mode": "error", "command": raw_command, "resolved_tool": spec.resolved_tool},
                "next_commands": ["help"],
            }

        result = handler(spec.params)
        if not isinstance(result, dict):
            result = {
                "success": False,
                "msg": f"{spec.resolved_tool} 返回了非字典结果。",
            }

        result.setdefault("cli", {})
        if isinstance(result["cli"], dict):
            result["cli"].update(
                {
                    "mode": "exec",
                    "command": raw_command,
                    "action": spec.action,
                    "resolved_tool": spec.resolved_tool,
                }
            )
        result["_resolved_tool_name"] = spec.resolved_tool
        result["_effective_params"] = spec.params
        result["next_commands"] = build_next_commands(spec, result)
        return result

    @contextlib.contextmanager
    def _activate_user_storage(self, paths: UserStoragePaths):
        """Activate user storage paths for file operations.

        This sets global variables in course_schedule and mcp_server modules
        to point to the user-specific paths.
        Also sets storage_paths base dir for shared cache (data/shared/).
        """
        paths.user_root.mkdir(parents=True, exist_ok=True)
        paths.output_dir.mkdir(parents=True, exist_ok=True)

        # Shared files: persistent data/shared/ under plugin root (NOT per-user temp)
        import campus_core.storage_paths as _sp
        _sp.set_base_dir(self.base_dir)

        shared_dir = paths.shared_data_dir
        shared_dir.mkdir(parents=True, exist_ok=True)
        period_time_file = shared_dir / SHARED_PERIOD_TIME_FILE
        period_calibration_state_file = shared_dir / SHARED_CALIBRATION_FILE
        xiqueer_request_file = shared_dir / SHARED_XIQUEER_FILE

        with _RUNTIME_STATE_LOCK:
            original_state = {
                "course_schedule": {
                    "COOKIE_FILE": course_schedule.COOKIE_FILE,
                    "PROFILE_FILE": course_schedule.PROFILE_FILE,
                    "OUTPUT_DIR": course_schedule.OUTPUT_DIR,
                },
                "mcp_server": {
                    "COOKIE_FILE": mcp_server.COOKIE_FILE,
                    "PROFILE_FILE": mcp_server.PROFILE_FILE,
                    "OUTPUT_DIR": mcp_server.OUTPUT_DIR,
                    "LIBRARY_COOKIE_FILE": mcp_server.LIBRARY_COOKIE_FILE,
                    "SEMINAR_SIGNIN_TASK_FILE": mcp_server.SEMINAR_SIGNIN_TASK_FILE,
                    "HEBAO_TOKEN_FILE": mcp_server.HEBAO_TOKEN_FILE,
                    "CAS_COOKIE_FILE": mcp_server.CAS_COOKIE_FILE,
                    "PERIOD_TIME_FILE": mcp_server.PERIOD_TIME_FILE,
                    "PERIOD_CALIBRATION_STATE_FILE": mcp_server.PERIOD_CALIBRATION_STATE_FILE,
                    "XIQUEER_REQUEST_FILE": mcp_server.XIQUEER_REQUEST_FILE,
                    "_ensure_seminar_auto_signin_worker": mcp_server._ensure_seminar_auto_signin_worker,
                },
            }

            try:
                course_schedule.COOKIE_FILE = paths.xk_cookie_file
                course_schedule.PROFILE_FILE = paths.profile_file
                course_schedule.OUTPUT_DIR = paths.output_dir

                mcp_server.COOKIE_FILE = paths.xk_cookie_file
                mcp_server.PROFILE_FILE = paths.profile_file
                mcp_server.OUTPUT_DIR = paths.output_dir
                mcp_server.LIBRARY_COOKIE_FILE = paths.library_cookie_file
                mcp_server.SEMINAR_SIGNIN_TASK_FILE = paths.seminar_signin_task_file
                mcp_server.HEBAO_TOKEN_FILE = paths.yunfz_token_file
                mcp_server.CAS_COOKIE_FILE = paths.cas_cookie_file
                mcp_server.PERIOD_TIME_FILE = period_time_file
                mcp_server.PERIOD_CALIBRATION_STATE_FILE = period_calibration_state_file
                mcp_server.XIQUEER_REQUEST_FILE = xiqueer_request_file
                mcp_server._ensure_seminar_auto_signin_worker = self._noop_auto_signin_worker
                yield
            finally:
                course_schedule.COOKIE_FILE = original_state["course_schedule"]["COOKIE_FILE"]
                course_schedule.PROFILE_FILE = original_state["course_schedule"]["PROFILE_FILE"]
                course_schedule.OUTPUT_DIR = original_state["course_schedule"]["OUTPUT_DIR"]

                mcp_server.COOKIE_FILE = original_state["mcp_server"]["COOKIE_FILE"]
                mcp_server.PROFILE_FILE = original_state["mcp_server"]["PROFILE_FILE"]
                mcp_server.OUTPUT_DIR = original_state["mcp_server"]["OUTPUT_DIR"]
                mcp_server.LIBRARY_COOKIE_FILE = original_state["mcp_server"]["LIBRARY_COOKIE_FILE"]
                mcp_server.SEMINAR_SIGNIN_TASK_FILE = original_state["mcp_server"]["SEMINAR_SIGNIN_TASK_FILE"]
                mcp_server.HEBAO_TOKEN_FILE = original_state["mcp_server"]["HEBAO_TOKEN_FILE"]
                mcp_server.CAS_COOKIE_FILE = original_state["mcp_server"]["CAS_COOKIE_FILE"]
                mcp_server.PERIOD_TIME_FILE = original_state["mcp_server"]["PERIOD_TIME_FILE"]
                mcp_server.PERIOD_CALIBRATION_STATE_FILE = original_state["mcp_server"]["PERIOD_CALIBRATION_STATE_FILE"]
                mcp_server.XIQUEER_REQUEST_FILE = original_state["mcp_server"]["XIQUEER_REQUEST_FILE"]
                mcp_server._ensure_seminar_auto_signin_worker = original_state["mcp_server"]["_ensure_seminar_auto_signin_worker"]

    @staticmethod
    def _noop_auto_signin_worker() -> None:
        return None

    def _setup_account(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.setup_account(
            student_id=_text(params.get("student_id")),
            password=_text(params.get("password"), strip=False),
            library_location=_text(params.get("library_location")),
            library_seat_no=_text(params.get("library_seat_no")),
            verify_login=_bool(params.get("verify_login"), True),
            calibrate_period_time=_bool(params.get("calibrate_period_time"), True),
        )


    def _smart_course_selection(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.smart_course_selection(
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
        result = mcp_server.sync_schedule(
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
            import shutil
            schedule_files = list(paths.output_dir.glob("schedule_clean_*.json"))
            if not schedule_files and paths.schedule_file.exists():
                try:
                    import json
                    schedule_data = json.loads(paths.schedule_file.read_text(encoding="utf-8"))
                    target = paths.output_dir / "schedule_clean_latest.json"
                    target.write_text(
                        json.dumps(schedule_data, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
                except Exception:
                    pass

        # Try cache for read-only views
        if identity and view in {"current", "now", "week", "full"}:
            cache_key = f"user:{identity.storage_key}:schedule:{view}:{target_date}"
            cached = SCHEDULE_CACHE.get(cache_key)
            if cached is not None:
                return cached

        result = mcp_server.schedule_query(
            view=view,
            timezone=timezone,
            target_date=target_date,
            auto_calibrate=_bool(params.get("auto_calibrate"), True),
        )

        # Cache successful read-only queries (permanent TTL)
        if result.get("success") and identity and view in {"current", "now", "week", "full"}:
            SCHEDULE_CACHE.set(cache_key, result, ttl_seconds=315360000.0)  # 10 years

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

        result = mcp_server.library_query(
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
        return mcp_server.course_selection_query(
            view=_text(params.get("view")) or "status",
            xktype=_text(params.get("xktype")) or "2",
        )

    def _course_selection_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.course_selection_plan(
            candidates_json=_text(params.get("candidates_json")),
            existing_schedule_json=_text(params.get("existing_schedule_json")),
            preferences_json=_text(params.get("preferences_json")),
            top_k=_int(params.get("top_k"), 10),
        )

    def _course_selection_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.course_selection_submit(
            payload_json=_text(params.get("payload_json")),
        )

    def _course_monitor_config(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.course_monitor_config(
            config_json=_text(params.get("config_json")),
            merge=bool(params.get("merge", True)),
        )

    def _course_monitor_once(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.course_monitor_once(
            config_json=_text(params.get("config_json")),
            send_notifications=bool(params.get("send_notifications", True)),
        )

    def _course_monitor_run(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.course_monitor_run(
            config_json=_text(params.get("config_json")),
            max_checks=_int(params.get("max_checks"), 1),
            duration_seconds=_int(params.get("duration_seconds"), 0),
            send_notifications=bool(params.get("send_notifications", True)),
        )

    def _course_monitor_notify_test(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.course_monitor_notify_test(
            config_json=_text(params.get("config_json")),
        )

    def _library_reserve(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.library_reserve(
            location=_text(params.get("location")),
            seat_no=_text(params.get("seat_no")),
            target_date=_text(params.get("target_date")),
            preferred_time=_text(params.get("preferred_time")) or "08:00",
            preferred_end_time=_text(params.get("preferred_end_time")),
            retry_until=_text(params.get("retry_until")),
            retry_interval_seconds=_int(params.get("retry_interval_seconds"), 2),
            max_attempts=_int(params.get("max_attempts"), 1),
        )

    def _library_auto_signin(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.library_auto_signin(
            record_id=_text(params.get("record_id")),
        )

    def _library_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.library_cancel(
            record_id=_text(params.get("record_id")),
            record_type=_text(params.get("record_type")) or "auto",
        )

    def _seminar_group(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.seminar_group(
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

        result = mcp_server.seminar_query(
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
        return mcp_server.seminar_reserve(
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
        return mcp_server.seminar_signin(
            record_id=_text(params.get("record_id")),
            auto_scan=_bool(params.get("auto_scan"), False),
        )

    def _seminar_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.seminar_cancel(
            record_id=_text(params.get("record_id")),
        )

    def _set_calibration_source(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.set_calibration_source(
            data=_text(params.get("data"), strip=False),
            cookie=_text(params.get("cookie"), strip=False),
            user_agent=_text(params.get("user_agent"), strip=False)
            or "KingoPalm/2.6.449 (iPhone; iOS 26.3; Scale/3.00)",
        )

    def _system_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.system_status(
            timezone=_text(params.get("timezone")) or "Asia/Shanghai",
        )

    def _yunfz_leave_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.yunfz_leave_query(
            view=_text(params.get("view")) or "list",
            leave_id=_text(params.get("leave_id")),
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_signin_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.yunfz_signin_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_checksleep_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.yunfz_checksleep_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_activity_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.yunfz_activity_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _yunfz_collection_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.yunfz_collection_query(
            view=_text(params.get("view")) or "list",
            page=_int(params.get("page"), 1),
            page_size=_int(params.get("page_size"), 20),
        )

    def _empty_classroom_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.empty_classroom_query(
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
        return mcp_server.empty_classroom_sync(
            term_code=_text(params.get("term_code")),
            campus_code=_text(params.get("campus_code")),
            building_code=_text(params.get("building_code")),
            type_code=_text(params.get("type_code")),
            force_refresh=_bool(params.get("force_refresh"), False),
        )

    def _resource_registry_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.resource_registry_query(
            view=_text(params.get("view")) or "search",
            query=_text(params.get("query")),
            resource_type=_text(params.get("resource_type")),
            campus_code=_text(params.get("campus_code")),
            building_code=_text(params.get("building_code")),
            limit=_int(params.get("limit"), 20),
        )

    def _resource_registry_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.resource_registry_sync(
            scope=_text(params.get("scope")) or "all",
            force_refresh=_bool(params.get("force_refresh"), False),
        )
