"""用合成目录和表 ID 验证分表解析。"""

import pytest

from campus_core.electricity_meter import FloorDirectory, MeterLookupError, RoomOption, resolve_meter_text, resolve_meters


floor = FloorDirectory("甲校区", "测试苑12号公寓", "6层", (
    RoomOption("opaque-ac", "418房间"), RoomOption("opaque-light", "12418房间"),
))
other_campus = FloorDirectory("乙校区", "测试苑12号公寓", "6层", (
    RoomOption("different-campus", "418房间"),
))
other_floor = FloorDirectory("甲校区", "测试苑12号公寓", "7层", (
    RoomOption("different-floor", "418房间"),
))
address = {"campus": "甲", "building": "测试苑１２栋", "room": "418室"}


def test_resolve_meters_preserves_scope_and_catalog_ids():
    assert resolve_meters((floor, other_campus), **address, meter="aircon")[0].roomverify == "opaque-ac"
    lighting, = resolve_meters((floor,), **address, meter="lighting", floor="6楼")
    assert lighting.query_params(schoolcode="school", pay_pro_id="project", business_type="business") == {
        "schoolcode": "school", "payProId": "project", "businesstype": "business", "roomverify": "opaque-light",
    }
    assert [item.label for item in resolve_meters((floor,), **address, meter="both")] == ["418房间", "12418房间"]
    assert [item.kind for item in resolve_meters((floor,), **address)] == ["aircon", "lighting"]
    assert [item.roomverify for item in resolve_meter_text((floor,), "测试苑12 418")] == ["opaque-ac", "opaque-light"]

    north = FloorDirectory("甲校区", "示例苑13号公寓", "1层", (
        RoomOption("example-ac", "205房间"), RoomOption("example-light", "13205房间"),
    ))
    assert resolve_meters((north,), campus="甲", building="示例苑13号楼", room="205", meter="lighting")[0].label == "13205房间"


@pytest.mark.parametrize(
    ("directory", "query", "error"),
    (
        ((floor, other_floor), {**address, "meter": "aircon"}, "补楼层"),
        ((other_campus,), address, "未匹配"),
        ((floor,), {**address, "room": "12418", "meter": "lighting"}, "可能是照明表"),
        ((other_floor,), {**address, "meter": "both"}, "未同时提供"),
    ),
)
def test_resolve_meters_rejects_ambiguous_or_incomplete_directory(directory, query, error):
    with pytest.raises(MeterLookupError, match=error):
        resolve_meters(directory, **query)


def test_compact_room_code_defaults_to_both_and_honors_named_meter():
    official = FloorDirectory("明伦校区", "铁三15号公寓", "3层", (
        RoomOption("synthetic-aircon", "345房间"), RoomOption("synthetic-light", "15345房间"),
    ), aliases=("顺河",))
    unrelated = FloorDirectory("甲校区", "测试苑15号公寓", "3层", official.rooms)
    aircon, lighting = resolve_meter_text((official, unrelated), "顺河 15345")
    assert (aircon.roomverify, lighting.roomverify) == ("synthetic-aircon", "synthetic-light")
    meter, = resolve_meter_text((official, unrelated), "查顺河15345的照明电量")
    assert (meter.campus, meter.building, meter.floor, meter.room, meter.kind) == (
        "明伦校区", "铁三15号公寓", "3层", "345", "lighting",
    )
    assert meter.roomverify == "synthetic-light"

    assert resolve_meter_text((official,), "顺河345") == (aircon, lighting)
    assert resolve_meter_text((official,), "顺河15345空调") == (aircon,)
    with pytest.raises(MeterLookupError, match="匹配多个"):
        resolve_meter_text((official, FloorDirectory(official.campus, official.building, "4层", official.rooms, official.aliases)), "顺河15345")
    with pytest.raises(MeterLookupError, match="无法确认这个编号的表型"):
        resolve_meter_text((FloorDirectory(official.campus, official.building, official.floor, official.rooms[1:], official.aliases),), "顺河15345")
    with pytest.raises(MeterLookupError, match="未收录"):
        resolve_meter_text((unrelated,), "顺河15345")
