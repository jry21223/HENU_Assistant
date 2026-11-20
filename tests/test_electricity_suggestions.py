"""查不到/多处匹配时的候选建议测试。"""

import json

import httpx
import pytest

from campus_core.electricity_client import (
    ORIGIN, ROOM_ACCESS, ROOM_CATALOG, ElectricityLookupError, query_address)

PRIVATE = "synthetic-private-value"
REFERER = ORIGIN + "/CloudPayment/bill/selectPayProject.do"


def _context():
    return {
        "cookie": f"TEST={PRIVATE}", "referer": REFERER,
        "schoolcode": "school", "payProId": "project", "businesstype": "electricity",
    }


def _client(handle):
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_unknown_place_returns_candidates_without_floor_fetch():
    seen = []

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        params = dict(request.url.params)
        seen.append((url, params.get("optype")))
        if url == ROOM_ACCESS:
            return httpx.Response(200, json={"businessData": {"useFlag": "1"}})
        assert url == ROOM_CATALOG
        if params["optype"] == "1":
            return httpx.Response(200, json={"code": "SUCCESS", "roomlist": [
                {"id": "c1", "name": "金明校区"}, {"id": "c2", "name": "明伦校区"}]})
        if params["optype"] == "2":
            rows = {"c1": [{"id": "b26", "name": "东苑26号公寓"}, {"id": "b25", "name": "东苑25号公寓"}],
                    "c2": [{"id": "b1", "name": "铁三15号公寓"}]}[params["areaid"]]
            return httpx.Response(200, json={"code": "SUCCESS", "roomlist": rows})
        raise AssertionError("候选阶段不应请求楼层/房间")

    with _client(handle) as http:
        with pytest.raises(ElectricityLookupError, match="未收录") as exc_info:
            query_address(http, "东区26 638", context=_context())
    candidates = exc_info.value.candidates
    assert candidates and candidates[0].command_text == "金明校区 东苑26 638"
    assert not [item for item in seen if item[1] in ("3", "4")]


def test_duplicate_meters_in_one_floor_cannot_be_disambiguated():
    seen = []

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        params = dict(request.url.params)
        seen.append((url, params.get("optype")))
        if url == ROOM_ACCESS:
            return httpx.Response(200, json={"businessData": {"useFlag": "1"}})
        assert url == ROOM_CATALOG
        rows = {
            "1": [{"id": "c1", "name": "明伦校区"}],
            "2": [{"id": "b1", "name": "铁三15号公寓"}],
            "3": [{"id": "f1", "name": "3层"}],
            "4": [{"id": "air", "name": "345房间"}, {"id": "l1", "name": "15345房间"},
                  {"id": "l2", "name": "15345房间"}],
        }[params["optype"]]
        return httpx.Response(200, json={"code": "SUCCESS", "roomlist": rows})

    with _client(handle) as http:
        with pytest.raises(ElectricityLookupError, match="重复电表项") as exc_info:
            query_address(http, "顺河15345", context=_context())
    assert not exc_info.value.candidates
    assert len(seen) == 5


def test_auth_error_during_catalog_is_not_masked():
    from campus_core.electricity_client import ElectricityAuthError

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        if url == ROOM_ACCESS:
            return httpx.Response(200, json={"businessData": {"useFlag": "1"}})
        return httpx.Response(401, headers={"Set-Cookie": "SESSION=x"})

    with _client(handle) as http:
        with pytest.raises(ElectricityAuthError):
            query_address(http, "东区26 638", context=_context())


def test_service_reply_lists_candidates(monkeypatch, tmp_path):
    from campus_core import electricity_client
    from campus_core.electricity_meter import LookupCandidate
    from henu_plugin.service import HenuPluginService

    monkeypatch.setenv("HENU_ELECTRICITY_CONTEXT", str(tmp_path / "ctx.json"))
    candidates = (
        LookupCandidate("金明校区", "东苑26号公寓", "638", "金明 东苑26 638"),
        LookupCandidate("金明校区", "东苑25号公寓", "638", "金明 东苑25 638"),
    )

    def fake_query(path, text, *, deadline=None):
        raise electricity_client.ElectricityLookupError("官方目录未收录该园区的这个编号。", candidates)

    monkeypatch.setattr(electricity_client, "query_saved", fake_query)
    result = HenuPluginService(tmp_path)._henu_cli({"command": "电费 东区26 638"})
    assert result["success"] is False
    assert "1. 金明 东苑26 638" in result["reply_text"]
    assert "2. 金明 东苑25 638" in result["reply_text"]
    assert [item["command_text"] for item in result["candidates"]] == [
        "金明 东苑26 638", "金明 东苑25 638"]
