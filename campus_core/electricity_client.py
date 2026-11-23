"""用有效的云缴费会话查询已选房间的剩余电量。"""

import argparse
import json
import logging
import os
import re
import shlex
import stat
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

from .atomic_io import atomic_write_json, file_transaction
from .wmxy_auth import AuthError, login_and_refresh
from .electricity_meter import (
    FloorDirectory, MeterAddress, MeterLookupError, RoomOption, _building, _floor, _normalize, _numbered_building,
    matches_area, parse_meter_text, resolve_meter_text, resolve_meters, suggest_targets,
)

ORIGIN = "https://h5cloud.17wanxiao.com:18443"
ROOM_STATE = f"{ORIGIN}/CloudPayment/user/getRoomState.do"
ROOM_CATALOG = f"{ORIGIN}/CloudPayment/user/getRoom.do"
ROOM_ACCESS = f"{ORIGIN}/CloudPayment/user/checkRoomCount.do"
# 云缴费换票：token=校区 sessionId，302 时下发可用的 SESSION。2026-11-19 实测。
PAY_EXCHANGE = f"{ORIGIN}/CloudPayment/user/pay.do"
EXCHANGE_PARAMS = {"UAinfo": "wanxiao", "systemType": "Android", "versioncode": "10589101"}
EXCHANGE_HEADERS = {"X-Requested-With": "com.newcapec.mobile.ncp"}
ROOM_FIELDS = {"businesstype", "payProId", "roomverify", "schoolcode"}
ADDRESS_FIELDS = {"campus", "building", "room"}
PROJECT_FIELDS = {"schoolcode", "payProId", "businesstype"}
CONTEXT_FIELDS = PROJECT_FIELDS | {"cookie", "referer"}
CONTEXT_EXTRAS = {"directory", "session_id", "custom_id", "login_token", "user_id", "login_name",
                  "device_id", "device_model", "device_product", "device_release"}

# httpx 的 INFO 请求日志含完整 URL，云缴费查询参数里有电表 ID，不进宿主日志。
logging.getLogger("httpx").addFilter(lambda record: ORIGIN not in str(record.args))
# httpcore 的 DEBUG 会打印响应头（含 Set-Cookie）。
logging.getLogger("httpcore").setLevel(logging.WARNING)


class ElectricityError(Exception):
    pass


class ElectricityInputError(ElectricityError):
    pass


class ElectricityLookupError(ElectricityInputError):
    """查不到或多处匹配；candidates 为可直接再查的近似选项。"""

    def __init__(self, message: str, candidates=()):
        super().__init__(message)
        self.candidates = tuple(candidates)


class ElectricityAuthError(ElectricityError):
    pass


class ElectricityExpiredError(ElectricityAuthError):
    """官方选房接口明确认定会话过期。"""


class ElectricityAccessDenied(ElectricityError):
    """服务端暂未允许选房或额度受限；不是登录失效，不要触发 token 轮换。"""


class ElectricityNetworkError(ElectricityError):
    pass


class ElectricityProtocolError(ElectricityError):
    pass


@dataclass(frozen=True)
class Reading:
    quantity: Decimal | None
    unit: str | None
    currency_balance: None
    queried_at: str


def _session_headers(cookie: str, referer: str) -> dict[str, str]:
    if (
        not isinstance(cookie, str)
        or not cookie
        or cookie != cookie.strip()
        or any(ord(char) < 32 or ord(char) > 126 for char in cookie)
    ):
        raise ElectricityInputError("缺少有效的云缴费会话。")
    if not isinstance(referer, str):
        raise ElectricityInputError("缺少云缴费页面地址。")
    try:
        page = urlsplit(referer)
    except ValueError:
        raise ElectricityInputError("云缴费页面地址无效。") from None
    if (page.scheme, page.netloc, page.path) != (
        "https", "h5cloud.17wanxiao.com:18443", "/CloudPayment/bill/selectPayProject.do"
    ) or referer != referer.strip() or any(ord(char) < 32 or ord(char) > 126 for char in referer):
        raise ElectricityInputError("云缴费页面地址不匹配。")
    return {"Cookie": cookie, "Referer": referer, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}


def _get_json(http: httpx.Client, url: str, params: dict[str, str], headers: dict[str, str], *,
              verify_exchange: bool = False) -> dict:
    try:
        if verify_exchange:
            response = _exchange_get(http, url, params=params, headers=headers, follow_redirects=False)
        else:
            response = http.get(url, params=params, headers=headers, follow_redirects=False)
    except httpx.TimeoutException:
        raise ElectricityNetworkError("电量查询超时。") from None
    except httpx.DecodingError:
        raise ElectricityProtocolError("无法解码云缴费响应。") from None
    except httpx.RequestError:
        raise ElectricityNetworkError("无法连接云缴费查询接口。") from None
    if verify_exchange and response.status_code != 200:
        raise ElectricityProtocolError(
            f"刷新电费登录态失败（HTTP {response.status_code}）。上游未返回选房验证结果，请稍后再试。"
        )
    if response.status_code in (401, 403):
        raise ElectricityAuthError(f"云缴费会话需要重新认证或授权（HTTP {response.status_code}）。")
    if response.has_redirect_location:
        raise ElectricityAuthError("云缴费接口返回重定向，请核对会话并按需重新认证。")
    if response.status_code != 200:
        raise ElectricityProtocolError(f"电量查询返回 HTTP {response.status_code}。")
    try:
        data = response.json()
    except ValueError:
        raise ElectricityProtocolError("云缴费返回了非 JSON 内容。") from None
    if not isinstance(data, dict):
        raise ElectricityProtocolError("云缴费未返回有效 JSON 对象。")
    return data


def query_balance(
    http: httpx.Client, room_ref: dict[str, str], *, cookie: str, referer: str
) -> Reading:
    if (
        not isinstance(room_ref, dict)
        or set(room_ref) != ROOM_FIELDS
        or any(
            not isinstance(value, str) or not value.strip() or not value.isprintable() or len(value) > 4096
            for value in room_ref.values()
        )
    ):
        raise ElectricityInputError("房间参数必须来自官方用电页的查询请求。")
    data = _get_json(http, ROOM_STATE, room_ref, _session_headers(cookie, referer))
    if data.get("returncode") != "100":
        raise ElectricityProtocolError("云缴费未返回查询成功状态。")

    quantity = data.get("quantity")
    if quantity is None or quantity in ("", "-1"):
        value = None
    elif isinstance(quantity, str) and re.fullmatch(r"[+-]?\d{1,18}(?:\.\d{1,12})?", quantity, flags=re.ASCII):
        value = Decimal(quantity)
    else:
        raise ElectricityProtocolError("云缴费返回了无法解析的电量。")
    unit = data.get("quantityunit")
    return Reading(value, unit if isinstance(unit, str) and len(unit) <= 12 else None, None,
                   datetime.now(timezone.utc).isoformat())


def _room_options(rows: object) -> tuple[RoomOption, ...]:
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("id", "name"))
        for row in rows
    ):
        raise ElectricityProtocolError("云缴费房间目录格式无效。")
    return tuple(RoomOption(row["id"], row["name"]) for row in rows)


def _area_aliases() -> dict[str, dict[str, str]]:
    path = Path(__file__).with_name("config") / "electricity_area_aliases.toml"
    try:
        aliases = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ElectricityInputError("无法读取园区别名数据。") from None
    if not isinstance(aliases, dict) or any(
        not isinstance(entries, dict) or any(not isinstance(value, str) or not value for value in entries.values())
        for entries in aliases.values()
    ):
        raise ElectricityInputError("园区别名数据格式无效。")
    return aliases


def _building_aliases(campus: str, building: str, aliases: dict[str, dict[str, str]]) -> tuple[str, ...]:
    numbered = _numbered_building(building)
    if numbered is None:
        return ()
    scope = aliases.get(_normalize(campus).removesuffix("校区"), {})
    return tuple(alias for alias, official in scope.items() if _normalize(official) == numbered[0])


def _room_access(http: httpx.Client, context: dict, cookie: str, *, verify_exchange: bool = False) -> None:
    """选房权限接口同时是会话是否仍被识别的探针。"""
    access = _get_json(http, ROOM_ACCESS, {"schoolCode": context["schoolcode"]},
                       _session_headers(cookie, context["referer"]), verify_exchange=verify_exchange)
    # 2026-11-19 实测：会话空闲过期后返回 returncode=-600「idserial为空」。
    if "idserial为空" in str(access.get("returnmsg")):
        raise ElectricityExpiredError("云缴费会话已过期，需要重新导入登录态。")
    business = access.get("businessData")
    flag = business.get("useFlag") if isinstance(business, dict) else None
    if str(flag) == "0":
        raise ElectricityAccessDenied("云缴费暂未允许选房或查询，请稍后再试或核对查询额度。")
    if str(flag) != "1":
        raise ElectricityProtocolError("云缴费未返回选房权限状态。")


def _level(http: httpx.Client, context: dict, optype: str, **parents: str) -> tuple[RoomOption, ...]:
    data = _get_json(http, ROOM_CATALOG, {
        "schoolcode": context["schoolcode"], "payProId": context["payProId"],
        "businesstype": context["businesstype"], "optype": optype,
        "areaid": "0", "buildid": "0", "levelid": "0", "unitid": "0", **parents,
    }, _session_headers(context["cookie"], context["referer"]))
    if data.get("code") != "SUCCESS":
        raise ElectricityProtocolError("云缴费官方目录查询失败。")
    return _room_options(data.get("roomlist"))


def _catalog(http: httpx.Client, context: dict) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """只拉校区与楼栋，用于失败时的近似建议。"""
    aliases = _area_aliases()
    rows = []
    for campus in _level(http, context, "1"):
        for building in _level(http, context, "2", areaid=campus.id):
            if _numbered_building(building.name) is None:
                continue
            rows.append((campus.name, building.name, _building_aliases(campus.name, building.name, aliases)))
    return tuple(rows)


def _official_directory(http: httpx.Client, address: dict | MeterAddress, context: dict) -> tuple[FloorDirectory, ...]:
    _room_access(http, context, context["cookie"])
    campuses = _level(http, context, "1")
    candidates = []
    if isinstance(address, MeterAddress):
        aliases = _area_aliases()
        scoped = {address.campus} if address.campus is not None else {
            campus for campus, names in aliases.items()
            if any(matches_area(address.place, campus, official, (alias,)) for alias, official in names.items())
        }
        for campus in campuses:
            if scoped and _normalize(campus.name).removesuffix("校区") not in scoped:
                continue
            for building in _level(http, context, "2", areaid=campus.id):
                nicknames = _building_aliases(campus.name, building.name, aliases)
                if address.matches_building(campus.name, building.name, nicknames):
                    candidates.append((campus, building, nicknames))
        if address.building_number is None:
            prefixes = address.compact_building_prefixes([building.name for _, building, _ in candidates])
            if prefixes:
                candidates = [row for row in candidates if _numbered_building(row[1].name)[1] in prefixes]
        requested_floor = address.floor
    else:
        campus_key = _normalize(address["campus"]).removesuffix("校区")
        building_key = _building(address["building"])
        for campus in campuses:
            if _normalize(campus.name).removesuffix("校区") != campus_key:
                continue
            for building in _level(http, context, "2", areaid=campus.id):
                if _building(building.name) == building_key:
                    candidates.append((campus, building, ()))
        requested_floor = address.get("floor")

    directory = []
    for campus, building, nicknames in candidates:
        parents = {"areaid": campus.id, "buildid": building.id}
        for floor in _level(http, context, "3", **parents):
            if requested_floor is not None and _floor(floor.name) != _floor(requested_floor):
                continue
            directory.append(FloorDirectory(
                campus.name, building.name, floor.name, _level(http, context, "4", **parents, levelid=floor.id), nicknames,
            ))
    return tuple(directory)


def _imported_directory(entries: object, aliases: dict[str, dict[str, str]]) -> tuple[FloorDirectory, ...]:
    if not isinstance(entries, list):
        raise ElectricityInputError("云缴费上下文中的官方房间目录格式无效。")
    directory = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in ("campus", "building", "floor"))
            or not isinstance(entry.get("aliases", []), list)
            or any(not isinstance(alias, str) or not alias.strip() for alias in entry.get("aliases", []))
        ):
            raise ElectricityInputError("云缴费上下文中的官方房间目录格式无效。")
        directory.append(FloorDirectory(
            entry["campus"], entry["building"], entry["floor"], _room_options(entry.get("rooms")),
            (*_building_aliases(entry["campus"], entry["building"], aliases), *entry.get("aliases", [])),
        ))
    return tuple(directory)


def validate_context(context: object) -> None:
    if (
        not isinstance(context, dict)
        or not CONTEXT_FIELDS <= context.keys()
        or not context.keys() <= CONTEXT_FIELDS | CONTEXT_EXTRAS
        or any(not isinstance(context[field], str) or not context[field].strip() for field in CONTEXT_FIELDS)
    ):
        raise ElectricityInputError("云缴费上下文缺少有效会话和项目字段。")
    _session_headers(context["cookie"], context["referer"])


def query_address(http: httpx.Client, address: dict | str | MeterAddress, *, context: dict) -> list[dict]:
    """用官方目录定位电表，逐表返回读数或脱敏错误。"""
    if not isinstance(address, (str, MeterAddress)) and (
        not isinstance(address, dict)
        or not ADDRESS_FIELDS <= address.keys() <= ADDRESS_FIELDS | {"meter", "floor"}
        or any(not isinstance(address[key], str) or not address[key].strip() for key in ADDRESS_FIELDS)
        or address.get("meter") not in (None, "aircon", "lighting", "both")
        or (address.get("floor") is not None and not isinstance(address["floor"], str))
    ):
        raise ElectricityInputError("地址需要 campus、building、room，可选 meter、floor。")
    validate_context(context)

    live = "directory" not in context
    directory: tuple[FloorDirectory, ...] = ()
    try:
        if isinstance(address, str):
            address = parse_meter_text(address)
        directory = (
            _official_directory(http, address, context)
            if live else _imported_directory(context["directory"], _area_aliases() if isinstance(address, MeterAddress) else {})
        )
        if isinstance(address, MeterAddress):
            meters = resolve_meter_text(directory, address, require_pair=live)
        else:
            meters = resolve_meters(directory, **address, require_pair=live)
        if not live and context.get("session_id"):
            # 导入目录也要先探测会话，过期时才会触发自动换票。
            _room_access(http, context, context["cookie"])
    except MeterLookupError as exc:
        candidates = exc.candidates
        if live and isinstance(address, MeterAddress) and exc.reason == "no_match" and not directory:
            candidates = suggest_targets(_catalog(http, context), address)
        raise ElectricityLookupError(str(exc), candidates) from None
    results = []
    for meter in meters:
        room_ref = meter.query_params(
            schoolcode=context["schoolcode"], pay_pro_id=context["payProId"], business_type=context["businesstype"],
        )
        result = {
            "campus": meter.campus, "building": meter.building, "floor": meter.floor,
            "room": meter.room, "meter": meter.kind, "label": meter.label,
        }
        try:
            reading = query_balance(http, room_ref, cookie=context["cookie"], referer=context["referer"])
            if reading.quantity is None:
                raise ElectricityProtocolError("云缴费未返回该表电量。")
        except (ElectricityNetworkError, ElectricityProtocolError) as exc:
            if len(meters) == 1:
                raise
            result["error"] = str(exc)
        else:
            result.update(asdict(reading))
        results.append(result)
    return results


def _load_context(path: Path) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, encoding="utf-8") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ElectricityInputError("云缴费上下文必须是本人独享的普通文件，例如权限 0600。")
            context = json.load(file)
            if not isinstance(context, dict):
                raise ElectricityInputError("云缴费上下文必须是 JSON 对象。")
            return context
    except (OSError, ValueError):
        raise ElectricityInputError("无法读取云缴费上下文 JSON。") from None


def _import_curl(text: str) -> dict:
    """从 DevTools「Copy as cURL」的 getRoomState.do 请求取出会话和缴费项目。"""
    try:
        args = shlex.split(text.replace("\\\n", " "))
    except ValueError:
        raise ElectricityInputError("无法解析 cURL 命令。") from None
    headers = {}
    for flag, value in zip(args, args[1:]):
        if flag in ("-H", "--header"):
            name, _, header = value.partition(":")
            headers[name.strip().lower()] = header.strip()
        elif flag in ("-b", "--cookie"):
            headers["cookie"] = value
    url = next((arg for arg in args if arg.startswith(ROOM_STATE + "?")), "")
    params = dict(parse_qsl(urlsplit(url).query))
    context = {
        "cookie": headers.get("cookie"), "referer": headers.get("referer"),
        **{key: params.get(key) for key in ("schoolcode", "payProId", "businesstype")},
    }
    if any(not isinstance(value, str) or not value.strip() for value in context.values()):
        raise ElectricityInputError("请复制 getRoomState.do 请求的 cURL，需含 Cookie、Referer 和缴费项目参数。")
    _session_headers(context["cookie"], context["referer"])
    return context


def _merge_cookie(header: str, updates: dict[str, str]) -> str:
    """把服务端 Set-Cookie 并入 Cookie 头，保留原有顺序。"""
    pairs = dict(part.strip().partition("=")[::2] for part in header.split(";") if part.strip())
    pairs.update(updates)
    return "; ".join(f"{name}={value}" for name, value in pairs.items())


def _merge_jar(header: str, jar) -> str:
    return _merge_cookie(header, {item.name: item.value for item in jar})


def _seconds_left(deadline: float | None, cap: float) -> float:
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ElectricityNetworkError("电费查询超时，请稍后再试。")
    return min(cap, remaining)


def _http_client(deadline: float | None) -> httpx.Client:
    def apply_budget(request: httpx.Request) -> None:
        request.extensions["timeout"] = httpx.Timeout(_seconds_left(deadline, 12)).as_dict()

    return httpx.Client(timeout=12, trust_env=False, event_hooks={"request": [apply_budget]})


def _query_once(context: dict, address: dict | MeterAddress, deadline: float | None = None) -> tuple[list[dict], str]:
    with _http_client(deadline) as http:
        outcomes = query_address(http, address, context=context)
        cookie = _merge_jar(context["cookie"], http.cookies.jar)
    return outcomes, cookie


def query_saved(path: Path, address: dict | str | MeterAddress, *, auto_refresh: bool = True,
                deadline: float | None = None) -> list[dict]:
    """用私有上下文文件查询；会话过期时先 pay.do 换票重试一次，有读数时回写 Cookie。"""
    if isinstance(address, str):
        try:
            address = parse_meter_text(address)
        except MeterLookupError as exc:
            raise ElectricityLookupError(str(exc)) from None
    # 整个读-改-写放进文件锁，避免把其它进程轮换出的 token/session 覆盖回旧值。
    with file_transaction(path, timeout=_seconds_left(deadline, 30)):
        context = _load_context(path)
        try:
            outcomes, cookie = _query_once(context, address, deadline)
        except ElectricityAuthError:
            if not auto_refresh or not context.get("session_id"):
                raise
            report = refresh_saved(path, deadline=deadline)
            if not report["recognized"]:
                if not report.get("session_expired") and report.get("reason"):
                    raise ElectricityProtocolError(report["reason"]) from None
                raise
            context = _load_context(path)
            outcomes, cookie = _query_once(context, address, deadline)
        if cookie != context["cookie"] and any("error" not in item for item in outcomes):
            atomic_write_json(path, {**context, "cookie": cookie})
    return outcomes


def _drop_cookies(header: str, names: set[str]) -> str:
    return "; ".join(
        part for part in (item.strip() for item in header.split(";"))
        if part and part.partition("=")[0].strip() not in names
    )


_EXCHANGE_HTTP_REASONS = {
    401: "上游身份认证未通过",
    403: "上游拒绝本次请求",
    429: "上游限制请求频率",
    500: "上游服务内部故障",
    502: "网关收到无效的后端响应",
    503: "上游服务暂不可用",
    504: "网关等待后端响应超时",
}


def _exchange_get(http: httpx.Client, url, **kwargs) -> httpx.Response:
    try:
        response = http.get(url, **kwargs)
    except (httpx.TimeoutException, ElectricityNetworkError):
        raise ElectricityNetworkError("刷新电费登录态超时，未收到 HTTP 状态码，请稍后再试。") from None
    except httpx.DecodingError:
        raise ElectricityProtocolError("刷新电费登录态失败，无法解码云缴费响应，请稍后再试。") from None
    except httpx.RequestError:
        raise ElectricityNetworkError("刷新电费登录态失败，无法连接云缴费换票接口，请稍后再试。") from None
    if response.is_error:
        code = response.status_code
        reason = _EXCHANGE_HTTP_REASONS.get(code, "上游服务出错" if response.is_server_error else "上游拒绝本次请求")
        raise ElectricityProtocolError(f"刷新电费登录态失败（HTTP {code}）。{reason}，请稍后再试。")
    return response


def _exchange_destination(base: httpx.URL, location: str) -> httpx.URL:
    origin = httpx.URL(ORIGIN)
    try:
        target = base.join(location)
    except httpx.InvalidURL:
        raise ElectricityProtocolError("云缴费换票跳转地址无效。") from None
    if (target.scheme, target.host, target.port) != (origin.scheme, origin.host, origin.port) or target.userinfo:
        raise ElectricityProtocolError("云缴费换票跳转超出原站点。")
    return target


def _exchange_cookie(http: httpx.Client, context: dict, session_id: str | None) -> dict:
    """用校区会话换云缴费 SESSION；recognized 为真时附带新 Cookie。"""
    if not session_id:
        return {"page_status": None, "issued": [], "recognized": False, "reason": "缺少 session_id。"}
    base = _drop_cookies(context["cookie"], {"SESSION"})
    page = _exchange_get(http, PAY_EXCHANGE, params={
        "customerId": str(context.get("custom_id") or "1"), "token": session_id, **EXCHANGE_PARAMS,
    }, headers={"Cookie": base, **EXCHANGE_HEADERS}, follow_redirects=False)
    jar = {item.name: item.value for item in http.cookies.jar}
    report = {"page_status": page.status_code, "issued": sorted(jar)}
    if not jar.get("SESSION") or not page.has_redirect_location:
        if page.status_code == 200 and "出错啦" in page.text:
            return report | {"recognized": False, "session_expired": True,
                             "reason": "校园会话已失效（HTTP 200），云缴费未接受本次换票。"}
        return report | {"recognized": False, "reason": (
            f"刷新电费登录态失败（HTTP {page.status_code}）。上游未返回可用会话，请稍后再试。"
        )}
    merged = _merge_jar(base, http.cookies.jar)
    current = httpx.URL(PAY_EXCHANGE)
    for _ in range(3):
        current = _exchange_destination(current, page.headers["location"])
        page = _exchange_get(http, current, headers={"Cookie": merged}, follow_redirects=False)
        merged = _merge_jar(base, http.cookies.jar)
        if not page.has_redirect_location:
            break
    else:
        raise ElectricityProtocolError("云缴费换票跳转次数超限。")
    if page.status_code != 200 or not any(c.name == "SESSION" and c.value for c in http.cookies.jar):
        return report | {"recognized": False, "follow_status": page.status_code, "reason": (
            f"刷新电费登录态失败（HTTP {page.status_code}）。上游未返回可用会话，请稍后再试。"
        )}
    try:
        _room_access(http, context, merged, verify_exchange=True)
    except ElectricityExpiredError:
        return report | {"recognized": False, "session_expired": True,
                         "reason": "换票后的会话未通过选房验证（HTTP 200）。"}
    return report | {"recognized": True, "follow_status": page.status_code, "cookie": merged}


def _login_device(context: dict) -> dict:
    return {"deviceId": context.get("device_id", ""), "telephoneModel": context.get("device_model"),
            "shebeixinghao": context.get("device_product"), "telephoneInfo": context.get("device_release")}


def refresh_saved(path: Path, *, deadline: float | None = None) -> dict:
    """先用现有 session_id 换票；明确过期时才用 token 续期一次。"""
    with file_transaction(path, timeout=_seconds_left(deadline, 30)):
        context = _load_context(path)
        validate_context(context)
        with _http_client(deadline) as http:
            report = _exchange_cookie(http, context, context.get("session_id"))
        if report["recognized"]:
            cookie = report.pop("cookie")
            atomic_write_json(path, {**context, "cookie": cookie})
            return report
        if not report.get("session_expired") or not all(
            context.get(field) for field in ("login_token", "user_id", "login_name", "device_id")
        ):
            return report
        try:
            # 认证域和两次云缴费换票分别用独立 Cookie jar。
            with _http_client(deadline) as auth_http:
                session, user = login_and_refresh(
                    auth_http, token=context["login_token"], user_id=context["user_id"],
                    user_name=context["login_name"], device=_login_device(context))
        except (AuthError, ValueError):
            return report | {"recognized": False, "reason": "完美校园登录态续期失败。"}
        # 续期会轮换 token，先落盘再换票。
        context = {**context, "login_token": str(user.get("token") or context["login_token"]),
                   "session_id": session.session,
                   "custom_id": str(user.get("customId") or context.get("custom_id") or "")}
        atomic_write_json(path, context)
        with _http_client(deadline) as http:
            report = _exchange_cookie(http, context, context["session_id"]) | {"token_refreshed": True}
        if report["recognized"]:
            cookie = report.pop("cookie")
            atomic_write_json(path, {**context, "cookie": cookie})
    return report


def main(argv: list[str] | None = None) -> int:
    """从标准输入读取宿舍简写、地址 JSON 或原有的低层请求。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, help="含有效授权会话及缴费项目的私有 JSON 文件")
    parser.add_argument("--import-curl", action="store_true", help="从标准输入的 getRoomState.do cURL 写入 --context")
    parser.add_argument("--refresh", action="store_true", help="用上下文里的长寿命 Cookie 尝试换发新会话")
    args = parser.parse_args(argv)
    if args.refresh:
        try:
            if args.context is None:
                raise ElectricityInputError("--refresh 需要 --context。")
            report = refresh_saved(args.context)
        except ElectricityError as exc:
            print(exc, file=sys.stderr)
            return 1
        except OSError:
            print("暂时无法读写云缴费上下文。", file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["recognized"] else 1
    if args.import_curl:
        try:
            if args.context is None:
                raise ElectricityInputError("--import-curl 需要 --context 指定保存位置。")
            context = _import_curl(sys.stdin.read())
            with file_transaction(args.context):
                if args.context.exists():
                    previous = _load_context(args.context)
                    if any(previous.get(key) != context[key] for key in PROJECT_FIELDS):
                        previous.pop("directory", None)
                    context = {**previous, **context}
                atomic_write_json(args.context, context)
        except ElectricityError as exc:
            print(exc, file=sys.stderr)
            return 1
        except OSError:
            print("暂时无法读写云缴费上下文。", file=sys.stderr)
            return 1
        print(f"已保存云缴费登录态：{args.context}", file=sys.stderr)
        return 0
    try:
        raw = sys.stdin.read().strip()
        config = json.loads(raw)
    except ValueError:
        config = raw
    try:
        is_address = isinstance(config, str) or (isinstance(config, dict) and bool(ADDRESS_FIELDS & config.keys()))
        if is_address:
            if args.context is None:
                raise ElectricityInputError("地址输入需要 --context 指向含有效会话和缴费项目的私有 JSON。")
            outcomes = query_saved(args.context, config)
            result = {"readings": [item for item in outcomes if "error" not in item]}
            errors = [item for item in outcomes if "error" in item]
            if errors:
                result["errors"] = errors
        elif not isinstance(config, dict) or config.keys() != {"room_ref", "cookie", "referer"}:
            raise ElectricityInputError("请提供地址 JSON，或底层的 room_ref、cookie 和 referer。")
        else:
            with httpx.Client(timeout=12, trust_env=False) as http:
                result = asdict(query_balance(http, **config))
        print(json.dumps(result, default=str, ensure_ascii=False))
    except ElectricityError as exc:
        print(exc, file=sys.stderr)
        for index, candidate in enumerate(getattr(exc, "candidates", ()), 1):
            print(f"{index}. {candidate.command_text}", file=sys.stderr)
        return 1
    except OSError:
        print("暂时无法读写云缴费上下文。", file=sys.stderr)
        return 1
    return 1 if is_address and not result["readings"] else 0


if __name__ == "__main__":
    sys.exit(main())
