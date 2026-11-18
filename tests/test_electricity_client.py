"""用合成响应检查云缴费的只读契约和错误边界。"""

import traceback
from decimal import Decimal

import httpx
import pytest

from campus_core.electricity_client import (
    ORIGIN,
    ROOM_FIELDS,
    ROOM_STATE,
    ElectricityAuthError,
    ElectricityInputError,
    ElectricityNetworkError,
    ElectricityProtocolError,
    query_balance,
)

PRIVATE = "synthetic-private-value"


def check(respond, **overrides):
    def handle(request):
        assert request.method == "GET"
        assert str(request.url).split("?", 1)[0] == ROOM_STATE
        assert set(request.url.params.keys()) == ROOM_FIELDS
        return respond(request)

    config = {
        "room_ref": dict.fromkeys(ROOM_FIELDS, "synthetic"),
        "cookie": f"TEST={PRIVATE}",
        "referer": ORIGIN + "/CloudPayment/bill/selectPayProject.do",
    }
    config.update(overrides)
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        return query_balance(http, **config)


def success(quantity, unit="度"):
    return lambda _: httpx.Response(200, json={"returncode": "100", "quantity": quantity, "quantityunit": unit})


@pytest.mark.parametrize(
    ("quantity", "expected"), [("12.35", Decimal("12.35")), ("0", Decimal("0")), (None, None)],
)
def test_query_balance_parses_quantity(quantity, expected):
    reading = check(success(quantity))
    assert reading.quantity == expected
    assert reading.unit == "度"
    assert reading.currency_balance is None


def test_query_balance_uses_source_unit():
    assert check(success("1.25", "kWh")).unit == "kWh"


def timeout(request):
    raise httpx.ReadTimeout(PRIVATE, request=request)


def disconnect(request):
    raise httpx.ConnectError(PRIVATE, request=request)


def no_request(_):
    raise AssertionError("无效输入不能发送请求")


@pytest.mark.parametrize(
    ("respond", "error_type", "expected", "overrides"),
    (
        (no_request, ElectricityInputError, "房间参数", {"room_ref": {}}),
        (no_request, ElectricityInputError, "房间参数", {
            "room_ref": {**dict.fromkeys(ROOM_FIELDS, "synthetic"), "roomverify": ""},
        }),
        (no_request, ElectricityInputError, "房间参数", {
            "room_ref": {**dict.fromkeys(ROOM_FIELDS, "synthetic"), "payProId": "  "},
        }),
        (no_request, ElectricityInputError, "缺少有效", {"cookie": f" TEST={PRIVATE}"}),
        (no_request, ElectricityInputError, "地址不匹配", {"referer": "https://example.invalid"}),
        (lambda _: httpx.Response(302, headers={"Location": f"https://example.invalid/{PRIVATE}"}),
         ElectricityAuthError, "重定向", {}),
        (lambda _: httpx.Response(401, text=PRIVATE), ElectricityAuthError, "HTTP 401", {}),
        (lambda _: httpx.Response(403, text=PRIVATE), ElectricityAuthError, "HTTP 403", {}),
        (lambda _: httpx.Response(304), ElectricityProtocolError, "HTTP 304", {}),
        (lambda _: httpx.Response(200, text=PRIVATE), ElectricityProtocolError, "非 JSON", {}),
        (lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=PRIVATE.encode()),
         ElectricityProtocolError, "无法解码", {}),
        (lambda _: httpx.Response(200, json={"returncode": "999", "returnmsg": PRIVATE}),
         ElectricityProtocolError, "查询成功状态", {}),
        (success(PRIVATE), ElectricityProtocolError, "无法解析", {}),
        (timeout, ElectricityNetworkError, "超时", {}),
        (disconnect, ElectricityNetworkError, "无法连接", {}),
    ),
)
def test_query_balance_returns_sanitized_errors(respond, error_type, expected, overrides):
    with pytest.raises(error_type) as exc_info:
        check(respond, **overrides)
    assert expected in str(exc_info.value)
    assert PRIVATE not in "".join(traceback.format_exception(exc_info.value))
