from __future__ import annotations

import contextlib
import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from langbot_plugin.api.entities.builtin.provider import session as provider_session

import course_schedule
import mcp_server


_RUNTIME_STATE_LOCK = threading.RLock()


@dataclass(frozen=True)
class SessionIdentity:
    qq: str
    storage_key: str
    launcher_type: str
    launcher_id: str
    sender_id: str


@dataclass(frozen=True)
class UserStoragePaths:
    user_root: Path
    profile_file: Path
    xk_cookie_file: Path
    library_cookie_file: Path
    seminar_signin_task_file: Path
    output_dir: Path


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
        self.data_dir = self.base_dir / "data"
        self.shared_dir = self.data_dir / "shared"
        self.users_dir = self.data_dir / "users"

        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)

        self.period_time_file = self.shared_dir / "period_time_config.json"
        self.period_calibration_state_file = self.shared_dir / "period_time_calibration_state.json"
        self.xiqueer_request_file = self.shared_dir / "xiqueer_period_time_request.json"

        self._tool_dispatch: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "setup_account": self._setup_account,
            "sync_schedule": self._sync_schedule,
            "schedule_query": self._schedule_query,
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
        }

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
            with self._activate_user_storage(paths):
                result = handler(params or {})
        except Exception as exc:
            return {"success": False, "msg": f"{tool_name} 执行异常: {exc}"}

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
        user_root = self.users_dir / identity.storage_key
        return UserStoragePaths(
            user_root=user_root,
            profile_file=user_root / "profile.json",
            xk_cookie_file=user_root / "xk_cookies.json",
            library_cookie_file=user_root / "library_cookies.json",
            seminar_signin_task_file=user_root / "seminar_signin_tasks.json",
            output_dir=user_root / "output",
        )

    def _decorate_result(
        self,
        result: dict[str, Any],
        tool_name: str,
        identity: SessionIdentity,
        paths: UserStoragePaths,
        query_id: int,
    ) -> None:
        if tool_name in {"setup_account", "system_status"}:
            result["session_binding"] = {
                "qq": identity.qq,
                "storage_key": identity.storage_key,
                "launcher_type": identity.launcher_type,
                "launcher_id": identity.launcher_id,
                "sender_id": identity.sender_id,
                "query_id": query_id,
            }

        if tool_name == "system_status":
            result["storage_paths"] = {
                "user_root": str(paths.user_root),
                "profile_file": str(paths.profile_file),
                "xk_cookie_file": str(paths.xk_cookie_file),
                "library_cookie_file": str(paths.library_cookie_file),
                "seminar_signin_task_file": str(paths.seminar_signin_task_file),
                "output_dir": str(paths.output_dir),
                "shared_dir": str(self.shared_dir),
            }

        if tool_name == "seminar_reserve":
            result["auto_signin_mode"] = "manual_scan_only"
            if result.get("success"):
                note = "插件版不会启动后台自动签到线程，请在签到时间前后再次调用 seminar_signin(auto_scan=true) 或 seminar_signin(record_id=...)。"
                result["msg"] = f"{_text(result.get('msg'))}；{note}".strip("；")

    @contextlib.contextmanager
    def _activate_user_storage(self, paths: UserStoragePaths):
        paths.user_root.mkdir(parents=True, exist_ok=True)
        paths.output_dir.mkdir(parents=True, exist_ok=True)
        self.shared_dir.mkdir(parents=True, exist_ok=True)

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
                mcp_server.PERIOD_TIME_FILE = self.period_time_file
                mcp_server.PERIOD_CALIBRATION_STATE_FILE = self.period_calibration_state_file
                mcp_server.XIQUEER_REQUEST_FILE = self.xiqueer_request_file
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

    def _sync_schedule(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.sync_schedule(
            xn=_text(params.get("xn")) or None,
            xq=_text(params.get("xq")) or None,
            auto_calibrate=_bool(params.get("auto_calibrate"), True),
        )

    def _schedule_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.schedule_query(
            view=_text(params.get("view")) or "current",
            timezone=_text(params.get("timezone")) or "Asia/Shanghai",
            target_date=_text(params.get("target_date")),
            auto_calibrate=_bool(params.get("auto_calibrate"), True),
        )

    def _library_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.library_query(
            view=_text(params.get("view")) or "current",
            record_type=_text(params.get("record_type")) or "1",
            page=_int(params.get("page"), 1),
            limit=_int(params.get("limit"), 20),
        )

    def _library_reserve(self, params: dict[str, Any]) -> dict[str, Any]:
        return mcp_server.library_reserve(
            location=_text(params.get("location")),
            seat_no=_text(params.get("seat_no")),
            target_date=_text(params.get("target_date")),
            preferred_time=_text(params.get("preferred_time")) or "08:00",
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
        return mcp_server.seminar_query(
            view=_text(params.get("view")) or "rooms",
            target_date=_text(params.get("target_date")),
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
            page=_int(params.get("page"), 1),
            area_id=_text(params.get("area_id")),
            record_type=_text(params.get("record_type")) or "1",
            limit=_int(params.get("limit"), 20),
            mode=_text(params.get("mode")) or "books",
            status=_text(params.get("status")),
        )

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
