"""会话受限与网络异常的回归测试。"""

from pathlib import Path

import httpx
import pytest

from campus_core.electricity_client import (
    ORIGIN, PAY_EXCHANGE, ROOM_ACCESS, ElectricityAccessDenied, ElectricityNetworkError,
    _exchange_cookie, query_address, query_saved)


def _context():
    return {
        "cookie": "TEST=x", "referer": ORIGIN + "/CloudPayment/bill/selectPayProject.do",
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
    }


def test_use_flag_zero_is_access_denied_not_auth():
    def handle(request):
        assert str(request.url).split("?", 1)[0] == ROOM_ACCESS
        return httpx.Response(200, json={"businessData": {"useFlag": "0"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ElectricityAccessDenied, match="未允许选房"):
            query_address(http, "东苑26 638", context=_context())


def test_access_denied_does_not_trigger_refresh(monkeypatch, tmp_path):
    from campus_core import electricity_client as ec

    path = tmp_path / "context.json"
    path.write_text('{"cookie":"TEST=x","referer":"%s","schoolcode":"s","payProId":"p",'
                    '"businesstype":"b","session_id":"sess"}' % (ORIGIN + "/CloudPayment/bill/selectPayProject.do"),
                    encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(ec, "_query_once", lambda context, address: (_ for _ in ()).throw(
        ElectricityAccessDenied("云缴费暂未允许选房或查询。")))
    monkeypatch.setattr(ec, "refresh_saved", lambda p: pytest.fail("受限不应触发换票"))
    with pytest.raises(ElectricityAccessDenied):
        query_saved(path, "东苑26 638")


def test_exchange_network_error_is_wrapped():
    def boom(request):
        raise httpx.ConnectError("boom", request=request)

    with httpx.Client(transport=httpx.MockTransport(boom)) as http:
        with pytest.raises(ElectricityNetworkError, match="无法连接云缴费换票接口"):
            _exchange_cookie(http, _context(), "session")
