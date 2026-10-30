"""Exact-window, saved-session-only school reads for QQ seat alerts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import re
import time

from henu_plugin.seat_map_image import seat_state


class AlertQueryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SavedSession:
    pin: str
    cookies: dict = field(repr=False)


@asynccontextmanager
async def saved_session(plugin, sender, expected_pin=""):
    from henu_plugin.library_automation import check_campus_state
    from henu_plugin.storage_adapter import PluginStorageAdapter

    # Keep the user's existing transaction lock through query/notification so a
    # concurrent account rebind cannot change the owner midway. Never save.
    adapter = PluginStorageAdapter(plugin, sender)
    try:
        paths = await adapter.load_all()
        profile = json.loads(paths.profile_file.read_text())
        cookies = json.loads(paths.library_cookie_file.read_text())
        subject = str(profile.get("student_id") or "").strip()
        if (
            not subject
            or not isinstance(cookies.get("_v4_token"), str)
            or not cookies["_v4_token"]
        ):
            raise AlertQueryError(
                "auth_required", "请先私聊查询图书馆座位，恢复登录后再设置提醒。"
            )
        if (
            cookies.get("_subject_student_id")
            and str(cookies["_subject_student_id"]) != subject
        ):
            raise AlertQueryError(
                "session_changed",
                "校园账号与图书馆登录态不一致，请先手动查询图书馆座位。",
            )
        try:
            await check_campus_state(plugin, paths, profile)
        except ValueError as exc:
            raise AlertQueryError("campus_paused", str(exc)) from exc
        pin = hashlib.sha256(
            json.dumps([subject, cookies], sort_keys=True).encode()
        ).hexdigest()
        if expected_pin and pin != expected_pin:
            raise AlertQueryError(
                "session_changed", "校园账号或登录态已变化，请手动查座后重新设置提醒。"
            )
        yield SavedSession(pin, cookies)
    finally:
        await adapter.abort()


def seat_identity(value):
    if type(value) not in (str, int) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,16}", str(value)
    ):
        raise AlertQueryError("query_unknown", "学校返回的座位编号不完整，请稍后重试。")
    text = str(value)
    return str(int(text)) if text.isdigit() else text.casefold()


def _resolve_area(rows, requested):
    from campus_core.locations import library_region_metadata

    def normalized(value):
        return re.sub(r"\s+", "", str(value)).casefold()

    choices = {}
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(
            r"\d{1,12}", str(row.get("id") or "")
        ):
            continue
        name = str(row.get("name") or "").strip()
        if not name or len(name) > 120:
            continue
        area = {
            "area_id": str(row["id"]),
            "location": name,
            **library_region_metadata(row),
        }
        choices[area["area_id"]] = (
            area,
            {
                normalized(v)
                for v in (
                    row["id"],
                    name,
                    row.get("nameMerge") or "",
                    area["campus"] + name,
                )
            },
        )
    wanted = normalized(requested)
    matches = [area for area, names in choices.values() if wanted in names]
    if not matches and not wanted.isdigit():
        matches = [
            area
            for area, names in choices.values()
            if any(wanted in name for name in names if name)
        ]
    if len(matches) != 1:
        raise AlertQueryError(
            "area_not_found",
            "没有找到唯一区域，请先查“图书馆座位”，再填写完整区域名称或ID。",
        )
    return matches[0]


def query_snapshot(snapshot, request, *, resolve=False):
    """Only these three read endpoints are allowed; no login or reservation I/O."""
    import requests
    from urllib3.util import Timeout
    from campus_core import HenuCampusBot

    deadline = time.monotonic() + 20

    class BoundedSession(requests.Session):
        def request(self, method, url, **kwargs):
            from urllib.parse import urlsplit

            parsed = urlsplit(url)
            if (
                method.upper() != "POST"
                or parsed.scheme != "https"
                or parsed.netloc != "zwyy.henu.edu.cn"
                or parsed.path
                not in {
                    "/v4/space/pick",
                    "/v4/Space/map",
                    "/v4/Space/seat",
                }
            ):
                raise ValueError("Unexpected library alert endpoint")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Library alert query deadline exceeded")
            kwargs.update(
                timeout=Timeout(
                    total=remaining, connect=min(3, remaining), read=min(6, remaining)
                ),
                allow_redirects=False,
            )
            response = super().request(method, url, **kwargs)
            if response.status_code in {401, 403}:
                raise AlertQueryError(
                    "auth_required", "图书馆登录已失效，请手动查座后重新设置提醒。"
                )
            response.raise_for_status()
            return response

    cookies = {
        k: v
        for k, v in snapshot.cookies.items()
        if not k.startswith("_") or k == "_v4_token"
    }
    client = HenuCampusBot("", "", cookies)
    client._saved_session_only = True
    headers = dict(client.session.headers)
    client.session.close()
    client.session = BoundedSession()
    client.session.headers.update(headers)
    client.session.cookies.update(
        {k: v for k, v in cookies.items() if not k.startswith("_")}
    )
    client._set_auth_header()

    def read(path, payload):
        response = client._post_json(path, payload, allow_reauth=False)
        if isinstance(response, dict) and response.get("code") == 10001:
            raise AlertQueryError(
                "auth_required", "图书馆登录已失效，请手动查座后重新设置提醒。"
            )
        if (
            not isinstance(response, dict)
            or type(response.get("code")) is not int
            or response["code"] != 0
            or not isinstance(response.get("data"), dict)
        ):
            raise AlertQueryError(
                "query_unknown", "暂时没查到可靠的座位状态，下一轮再检查。"
            )
        return response["data"]

    try:
        area = request.get("area")
        if resolve:
            rows = read("/v4/space/pick", {"date": request["target_date"]}).get("area")
            if not isinstance(rows, list) or len(rows) > 64:
                raise AlertQueryError(
                    "query_unknown", "暂时没查到区域列表，请稍后重试。"
                )
            area = _resolve_area(rows, request["location"])
        space = read("/v4/Space/map", {"id": area["area_id"]})
        try:
            payload = client._build_reservation_plan(
                area["area_id"],
                space,
                request["target_date"],
                preferred_time=request["start"],
                preferred_end_time=request["end"],
            )["seat_query"]
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise AlertQueryError(
                "window_unavailable",
                "学校尚未提供该完整时段，不能确定余座，请稍后再设置。",
            ) from exc
        if (payload.get("day"), payload.get("start_time"), payload.get("end_time")) != (
            request["target_date"],
            request["start"],
            request["end"],
        ):
            raise AlertQueryError(
                "window_unavailable", "学校返回的日期或时段与设置不一致，未判断为空位。"
            )
        rows = read("/v4/Space/seat", payload).get("list")
        if not isinstance(rows, list) or not rows or len(rows) > 2000:
            raise AlertQueryError(
                "query_unknown", "学校没有返回完整座位列表，状态未知。"
            )
        observed = {}
        for row in rows:
            if not isinstance(row, dict):
                raise AlertQueryError("query_unknown", "学校返回的座位状态不完整。")
            identity = seat_identity(row.get("no"))
            if identity in observed:
                raise AlertQueryError(
                    "query_unknown", "学校返回了重复座位编号，状态未知。"
                )
            state = (
                seat_state(row) if type(row.get("status")) in (str, int) else "unknown"
            )
            observed[identity] = {"no": str(row["no"]), "state": state}
        seat = request.get("seat_no", "")
        if seat:
            identity = seat_identity(seat)
            if identity not in observed:
                raise AlertQueryError(
                    "seat_not_found", "没有查到指定座位，未判断为空闲；请核对座位号。"
                )
            observed = {identity: observed[identity]}
        return {"area": area, "observed": observed}
    finally:
        client.session.close()
