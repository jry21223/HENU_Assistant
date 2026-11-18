"""用有效的云缴费会话查询已选房间的剩余电量。"""

import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

ORIGIN = "https://h5cloud.17wanxiao.com:18443"
ROOM_STATE = f"{ORIGIN}/CloudPayment/user/getRoomState.do"
ROOM_FIELDS = {"businesstype", "payProId", "roomverify", "schoolcode"}


class ElectricityError(Exception):
    pass


class ElectricityInputError(ElectricityError):
    pass


class ElectricityAuthError(ElectricityError):
    pass


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

    try:
        response = http.get(
            ROOM_STATE,
            params=room_ref,
            headers={"Cookie": cookie, "Referer": referer, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        raise ElectricityNetworkError("电量查询超时。") from None
    except httpx.DecodingError:
        raise ElectricityProtocolError("无法解码云缴费响应。") from None
    except httpx.RequestError:
        raise ElectricityNetworkError("无法连接云缴费查询接口。") from None
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
    if not isinstance(data, dict) or data.get("returncode") != "100":
        raise ElectricityProtocolError("云缴费未返回查询成功状态。")

    quantity = data.get("quantity")
    if quantity is None or quantity == "":
        value = None
    elif isinstance(quantity, str) and re.fullmatch(r"[+-]?\d{1,18}(?:\.\d{1,12})?", quantity, flags=re.ASCII):
        value = Decimal(quantity)
    else:
        raise ElectricityProtocolError("云缴费返回了无法解析的电量。")
    unit = data.get("quantityunit")
    return Reading(value, unit if isinstance(unit, str) and len(unit) <= 12 else None, None,
                   datetime.now(timezone.utc).isoformat())


def main() -> int:
    """通过标准输入接收会话及房间参数，标准输出只写读数。"""
    try:
        config = json.load(sys.stdin)
    except ValueError:
        print("会话输入不是有效 JSON。", file=sys.stderr)
        return 1
    try:
        if not isinstance(config, dict) or set(config) != {"room_ref", "cookie", "referer"}:
            raise ElectricityInputError("需要 room_ref、cookie 和 referer。")
        with httpx.Client(timeout=12, trust_env=False) as http:
            reading = query_balance(http, **config)
        print(json.dumps(asdict(reading), default=str, ensure_ascii=False))
    except ElectricityError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
