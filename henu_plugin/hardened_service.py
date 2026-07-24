"""Production hardening overrides for the LangBot plugin service."""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import mcp_server
from henu_mcp import runtime as henu_runtime
from henu_mcp.tools import server_impl
from henu_plugin import service as _base
from henu_plugin.cache import (
    LIBRARY_QUERY_CACHE,
    SCHEDULE_CACHE,
    SEMINAR_QUERY_CACHE,
)
from henu_plugin.storage_adapter import (
    SHARED_CALIBRATION_FILE,
    SHARED_PERIOD_TIME_FILE,
    UserStoragePaths,
)


class HardenedHenuPluginService(_base.HenuPluginService):
    """Keep the legacy business surface while fixing request/runtime boundaries."""

    CAMPUS_TIMEZONE = "Asia/Shanghai"

    def run_tool(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return super().run_tool(*args, **kwargs)
        except (RuntimeError, ValueError) as exc:
            return {
                "success": False,
                "error_code": "identity_or_storage_context_invalid",
                "msg": str(exc),
            }

    def _resolve_identity(self, session, identity_hint=None):
        hint = identity_hint or {}
        launcher_type = getattr(
            getattr(session, "launcher_type", ""),
            "value",
            getattr(session, "launcher_type", ""),
        )
        launcher_type = str(launcher_type or "").lower()
        sender_id = _base._clean_session_id(
            hint.get("sender_id")
        ) or _base._clean_session_id(getattr(session, "sender_id", ""))
        launcher_id = _base._clean_session_id(
            hint.get("launcher_id")
        ) or _base._clean_session_id(getattr(session, "launcher_id", ""))
        if launcher_type == "group" and not sender_id:
            raise RuntimeError("群聊缺少 sender_id，拒绝访问个人 Storage")
        if not sender_id and not launcher_id:
            raise RuntimeError("无法确认当前用户身份，拒绝访问 Storage")
        return super()._resolve_identity(session, identity_hint=hint)

    @classmethod
    def _effective_timezone(cls, requested: str) -> tuple[str, bool]:
        text = str(requested or cls.CAMPUS_TIMEZONE).strip() or cls.CAMPUS_TIMEZONE
        try:
            ZoneInfo(text)
            return text, False
        except Exception:
            return cls.CAMPUS_TIMEZONE, True

    def get_time_snapshot(self, timezone: str = CAMPUS_TIMEZONE) -> dict[str, Any]:
        requested = str(timezone or self.CAMPUS_TIMEZONE).strip() or self.CAMPUS_TIMEZONE
        effective, fallback = self._effective_timezone(requested)
        snapshot = mcp_server.get_server_time(timezone=effective)
        if not isinstance(snapshot, dict):
            return {
                "success": False,
                "timezone_requested": requested,
                "timezone_effective": effective,
                "fallback_used": fallback,
                "msg": "获取服务器时间失败",
            }
        result = dict(snapshot)
        result["timezone_requested"] = requested
        result["timezone_effective"] = effective
        result["fallback_used"] = fallback
        result["timezone"] = effective
        return result

    def get_runtime_context(
        self,
        session,
        identity_hint=None,
        timezone=CAMPUS_TIMEZONE,
    ):
        account_context = self.get_sender_account_context(
            session,
            identity_hint=identity_hint,
        )
        server_time = self.get_time_snapshot(timezone=timezone)
        return {
            "success": bool(
                account_context.get("success", True)
                and server_time.get("success", True)
            ),
            "binding": account_context.get("binding") or {},
            "account": account_context.get("account") or {},
            "server_time": server_time,
        }

    def _henu_cli(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._henu_cli(params)
        return self._sanitize_cli_examples(result)

    @classmethod
    def _sanitize_cli_examples(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._sanitize_cli_examples(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._sanitize_cli_examples(item) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize_cli_examples(item) for item in value]
        if isinstance(value, str):
            return value.replace("2026-03-30", "YYYY-MM-DD")
        return value

    @staticmethod
    def _schedule_revision(paths: Any) -> str:
        candidates = []
        if paths is not None:
            path = getattr(paths, "schedule_file", None)
            if isinstance(path, Path) and path.exists():
                stat = path.stat()
                candidates.append(f"{stat.st_mtime_ns}:{stat.st_size}")
        return "|".join(candidates) or "none"

    def _restore_schedule_from_storage(self) -> Any:
        paths = _base.get_current_user_paths()
        if not paths or not paths.output_dir:
            return paths
        schedule_files = list(paths.output_dir.glob("schedule_clean_*.json"))
        if not schedule_files and paths.schedule_file.exists():
            try:
                schedule_data = json.loads(
                    paths.schedule_file.read_text(encoding="utf-8")
                )
                target = paths.output_dir / "schedule_clean_latest.json"
                target.write_text(
                    json.dumps(schedule_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
        return paths

    def _schedule_query(self, params: dict[str, Any]) -> dict[str, Any]:
        identity = getattr(_base._CURRENT_IDENTITY, "value", None)
        view = _base._text(params.get("view")) or "current"
        requested_timezone = (
            _base._text(params.get("timezone")) or self.CAMPUS_TIMEZONE
        )
        timezone, fallback = self._effective_timezone(requested_timezone)
        target_date = _base._text(params.get("target_date")) or ""
        auto_calibrate = _base._bool(params.get("auto_calibrate"), True)
        paths = self._restore_schedule_from_storage()

        cache_key = ""
        if identity and view in {"week", "full"}:
            cache_key = (
                f"user:{identity.storage_key}:schedule:{view}:"
                f"{timezone}:{int(auto_calibrate)}:{self._schedule_revision(paths)}"
            )
            cached = SCHEDULE_CACHE.get(cache_key)
            if cached is not None:
                return cached

        result = mcp_server.schedule_query(
            view=view,
            timezone=timezone,
            target_date=target_date,
            auto_calibrate=auto_calibrate,
        )
        if isinstance(result, dict):
            result.setdefault("timezone_requested", requested_timezone)
            result.setdefault("timezone_effective", timezone)
            result.setdefault("timezone_fallback_used", fallback)

        if cache_key and isinstance(result, dict) and result.get("success"):
            SCHEDULE_CACHE.set(cache_key, result, ttl_seconds=300.0)
        return result

    @staticmethod
    def _params_digest(params: dict[str, Any]) -> str:
        encoded = json.dumps(
            params,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _library_query(self, params: dict[str, Any]) -> dict[str, Any]:
        identity = getattr(_base._CURRENT_IDENTITY, "value", None)
        normalized = {
            "view": (_base._text(params.get("view")) or "current").strip().lower(),
            "record_type": _base._text(params.get("record_type")) or "1",
            "page": _base._int(params.get("page"), 1),
            "limit": _base._int(params.get("limit"), 20),
            "target_date": _base._text(params.get("target_date")),
            "location": _base._text(params.get("location")),
            "area_id": _base._text(params.get("area_id")),
            "preferred_time": _base._text(params.get("preferred_time")) or "08:00",
            "preferred_end_time": _base._text(params.get("preferred_end_time")),
        }
        view = normalized["view"]
        cache_key = ""
        if identity and view in {"locations", "seats"}:
            cache_key = (
                f"user:{identity.storage_key}:library:{self._params_digest(normalized)}"
            )
            cached = LIBRARY_QUERY_CACHE.get(cache_key)
            if cached is not None:
                return cached

        result = mcp_server.library_query(**normalized)
        if cache_key and isinstance(result, dict) and result.get("success"):
            LIBRARY_QUERY_CACHE.set(
                cache_key,
                result,
                ttl_seconds=20.0 if view == "seats" else 60.0,
            )
        return result

    def _seminar_query(self, params: dict[str, Any]) -> dict[str, Any]:
        identity = getattr(_base._CURRENT_IDENTITY, "value", None)
        normalized = {
            "view": _base._text(params.get("view")) or "rooms",
            "target_date": _base._text(params.get("target_date")) or "",
            "members": _base._int(params.get("members"), 0),
            "name": _base._text(params.get("name")),
            "room": _base._text(params.get("room")),
            "start_time": _base._text(params.get("start_time")),
            "end_time": _base._text(params.get("end_time")),
            "library_ids": _base._text(params.get("library_ids")),
            "library_names": _base._text(params.get("library_names")),
            "floor_ids": _base._text(params.get("floor_ids")),
            "floor_names": _base._text(params.get("floor_names")),
            "category_ids": _base._text(params.get("category_ids")),
            "category_names": _base._text(params.get("category_names")),
            "boutique_ids": _base._text(params.get("boutique_ids")),
            "boutique_names": _base._text(params.get("boutique_names")),
            "page": _base._int(params.get("page"), 1),
            "area_id": _base._text(params.get("area_id")),
            "record_type": _base._text(params.get("record_type")) or "1",
            "limit": _base._int(params.get("limit"), 20),
            "mode": _base._text(params.get("mode")) or "books",
            "status": _base._text(params.get("status")),
        }
        view = str(normalized["view"])
        cache_key = ""
        if identity and view in {"rooms", "filters", "detail"}:
            cache_key = (
                f"user:{identity.storage_key}:seminar:{self._params_digest(normalized)}"
            )
            cached = SEMINAR_QUERY_CACHE.get(cache_key)
            if cached is not None:
                return cached

        result = mcp_server.seminar_query(**normalized)
        if cache_key and isinstance(result, dict) and result.get("success"):
            SEMINAR_QUERY_CACHE.set(cache_key, result, ttl_seconds=30.0)
        return result

    def _smart_course_selection(self, params: dict[str, Any]) -> dict[str, Any]:
        bounded = dict(params or {})
        requested = _base._int(bounded.get("max_combinations"), 200000)
        if requested > 250000:
            return {
                "success": False,
                "error_code": "limit_exceeded",
                "msg": "max_combinations 不能超过 250000。",
            }
        bounded["max_combinations"] = max(1, requested)
        bounded["top_k"] = max(
            1, min(10, _base._int(bounded.get("top_k"), 3))
        )
        return super()._smart_course_selection(bounded)

    def _course_monitor_run(self, params: dict[str, Any]) -> dict[str, Any]:
        max_checks = _base._int(params.get("max_checks"), 1)
        duration = _base._int(params.get("duration_seconds"), 0)
        if max_checks > 3 or duration > 180:
            return {
                "success": False,
                "error_code": "limit_exceeded",
                "msg": "LangBot 同步 Tool 最多检查 3 次或运行 180 秒；长期监控请使用外部任务。",
            }
        return super()._course_monitor_run(params)

    def _library_reserve(self, params: dict[str, Any]) -> dict[str, Any]:
        attempts = _base._int(params.get("max_attempts"), 1)
        interval = _base._int(params.get("retry_interval_seconds"), 2)
        if attempts > 30 or interval > 10:
            return {
                "success": False,
                "error_code": "limit_exceeded",
                "msg": "单次确认最多尝试 30 次，重试间隔最多 10 秒。",
            }
        return super()._library_reserve(params)

    @contextlib.contextmanager
    def _activate_user_storage(self, paths: UserStoragePaths):
        paths.user_root.mkdir(parents=True, exist_ok=True)
        paths.output_dir.mkdir(parents=True, exist_ok=True)

        import campus_core.storage_paths as storage_paths

        storage_paths.set_base_dir(self.base_dir)
        shared_dir = paths.shared_data_dir
        shared_dir.mkdir(parents=True, exist_ok=True)

        with _base._RUNTIME_STATE_LOCK:
            original_state = henu_runtime.snapshot_runtime_paths()
            original_worker = server_impl._ensure_seminar_auto_signin_worker
            try:
                henu_runtime.set_runtime_paths(
                    xk_cookie_file=paths.xk_cookie_file,
                    profile_file=paths.profile_file,
                    output_dir=paths.output_dir,
                    library_cookie_file=paths.library_cookie_file,
                    seminar_signin_task_file=paths.seminar_signin_task_file,
                    hebao_token_file=paths.yunfz_token_file,
                    cas_cookie_file=paths.cas_cookie_file,
                    period_time_file=shared_dir / SHARED_PERIOD_TIME_FILE,
                    period_calibration_state_file=shared_dir / SHARED_CALIBRATION_FILE,
                    xiqueer_request_file=paths.xiqueer_request_file,
                )
                server_impl._ensure_seminar_auto_signin_worker = (
                    self._noop_auto_signin_worker
                )
                yield
            finally:
                henu_runtime.restore_runtime_paths(original_state)
                server_impl._ensure_seminar_auto_signin_worker = original_worker
