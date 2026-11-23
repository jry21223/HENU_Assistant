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
    assert "电费 金明 东苑26 638" in result["reply_text"]
    assert "电费 金明 东苑25 638" in result["reply_text"]
    assert [item["command_text"] for item in result["candidates"]] == [
        "金明 东苑26 638", "金明 东苑25 638"]


@pytest.mark.parametrize(("meter", "types"), [("lighting", ["lighting"]), ("both", ["aircon", "lighting"])])
def test_json_floor_candidates_can_be_repeated_as_text(meter, types):
    from campus_core.electricity_client import ROOM_STATE

    context = {**_context(), "directory": [
        {"campus": "甲校区", "building": "测试苑12栋", "floor": f"{floor}层", "rooms": [
            {"id": f"air-{floor}", "name": "418房间"},
            {"id": f"light-{floor}", "name": "12418房间"},
        ]} for floor in (6, 7)
    ]}
    seen = []

    def handle(request):
        assert request.url.path == httpx.URL(ROOM_STATE).path
        seen.append(request.url.params["roomverify"])
        return httpx.Response(200, json={"returncode": "100", "quantity": "12", "quantityunit": "度"})

    with _client(handle) as http:
        with pytest.raises(ElectricityLookupError, match="补楼层") as exc:
            query_address(http, {"campus": "甲", "building": "测试苑12栋", "room": "418", "meter": meter},
                          context=context)
        assert seen == []
        choices = exc.value.candidates
        assert [(choice.floor, choice.room, choice.meter) for choice in choices] == [
            ("6层", "418", meter), ("7层", "418", meter),
        ]
        for choice in choices:
            readings = query_address(http, choice.command_text, context=context)
            assert [reading["meter"] for reading in readings] == types
            assert {reading["floor"] for reading in readings} == {choice.floor}
    expected = ["light"] if meter == "lighting" else ["air", "light"]
    assert seen == [f"{label}-{floor}" for floor in (6, 7) for label in expected]


@pytest.mark.parametrize("address", [
    "龙子湖校区 二斋 2301 --floor 三",
    "郑州校区 2斋 2301 --floor 三",
    "二斋2301 --floor 三",
    {"campus": "郑州", "building": "2斋", "room": "2301", "floor": "三层"},
])
def test_longzihu_reads_exact_catalog_entry_without_guessing_type(address):
    from campus_core.electricity_client import ROOM_STATE

    rows = {
        "1": [{"id": "campus", "name": "龙子湖校区"}, {"id": "other", "name": "金明校区"}],
        "2": [{"id": "building", "name": "二斋"}, {"id": "neighbor", "name": "三斋"}],
        "3": [{"id": "floor", "name": "三层"}, {"id": "other-floor", "name": "四层"}],
        "4": [{"id": "exact", "name": "2301房间"}, {"id": "short", "name": "301"}],
    }
    seen = []

    def handle(request):
        url = str(request.url).split("?", 1)[0]
        seen.append(url)
        if url == ROOM_ACCESS:
            return httpx.Response(200, json={"businessData": {"useFlag": "1"}})
        if url == ROOM_CATALOG:
            params = request.url.params
            for level, key, expected in (("2", "areaid", "campus"), ("3", "buildid", "building"),
                                         ("4", "levelid", "floor")):
                if params["optype"] == level:
                    assert params[key] == expected
            return httpx.Response(200, json={"code": "SUCCESS", "roomlist": rows[params["optype"]]})
        assert url == ROOM_STATE
        assert request.url.params["roomverify"] == "exact"
        return httpx.Response(200, json={"returncode": "100", "quantity": "12.5", "quantityunit": "度"})

    with _client(handle) as http:
        reading, = query_address(http, address, context=_context())
    assert (reading["campus"], reading["building"], reading["room"], reading["meter"]) == (
        "龙子湖校区", "二斋", "2301", "unknown",
    )
    assert float(reading["quantity"]) == 12.5
    assert seen == [ROOM_ACCESS, *([ROOM_CATALOG] * 4), ROOM_STATE]


def test_longzihu_ambiguity_requires_floor_and_explicit_type_has_no_reading():
    from campus_core.electricity_meter import FloorDirectory, MeterLookupError, RoomOption, resolve_meter_text

    directory = tuple(FloorDirectory("龙子湖校区", "八斋", floor,
                                     (RoomOption(f"room-{floor}", "4301房间"),))
                      for floor in ("三层", "四层"))
    with pytest.raises(MeterLookupError, match="补楼层") as ambiguous:
        resolve_meter_text(directory, "郑州校区 8斋4301", require_pair=True)
    commands = [entry.command_text for entry in ambiguous.value.candidates]
    assert commands == ["龙子湖校区 八斋 4301 --floor 三", "龙子湖校区 八斋 4301 --floor 四"]
    assert [resolve_meter_text(directory, command)[0].roomverify for command in commands] == [
        "room-三层", "room-四层",
    ]
    with pytest.raises(MeterLookupError, match="无法确认.*表型"):
        resolve_meter_text(directory, "八斋 4301照明 --floor 三", require_pair=True)
    duplicate = FloorDirectory("龙子湖校区", "八斋", "三层", (*directory[0].rooms,
                              RoomOption("duplicate", "4301")))
    with pytest.raises(MeterLookupError, match="重复电表项"):
        resolve_meter_text((duplicate,), "八斋4301", require_pair=True)


def test_qq_labels_unknown_meter_without_calling_it_lighting(monkeypatch, tmp_path):
    from campus_core import electricity_client
    from henu_plugin.service import HenuPluginService

    monkeypatch.setenv("HENU_ELECTRICITY_CONTEXT", str(tmp_path / "context.json"))
    monkeypatch.setattr(electricity_client, "query_saved", lambda *args, **kwargs: [
        {"campus": "龙子湖校区", "building": "二斋", "floor": "三层", "room": "2301",
         "meter": "unknown", "quantity": 12.5, "unit": "度"},
    ])
    result = HenuPluginService(tmp_path)._henu_cli({"command": "电费 郑州校区 2斋 2301"})
    assert result["reply_text"] == "龙子湖校区 二斋 三层 2301室\n剩余 12.5 度（表型待确认）"
