"""用合成目录和表 ID 验证分表解析。"""

import pytest

from campus_core.electricity_meter import FloorDirectory, MeterLookupError, RoomOption, resolve_meters


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

    north = FloorDirectory("甲校区", "示例苑13号公寓", "1层", (
        RoomOption("example-ac", "205房间"), RoomOption("example-light", "13205房间"),
    ))
    assert resolve_meters((north,), campus="甲", building="示例苑13号楼", room="205", meter="lighting")[0].label == "13205房间"


@pytest.mark.parametrize(
    ("directory", "query", "error"),
    (
        ((floor,), address, "还是两项都查"),
        ((floor, other_floor), {**address, "meter": "aircon"}, "补楼层"),
        ((other_campus,), address, "未匹配"),
        ((floor,), {**address, "room": "12418", "meter": "lighting"}, "可能是照明表"),
        ((other_floor,), {**address, "meter": "both"}, "未同时提供"),
    ),
)
def test_resolve_meters_rejects_ambiguous_or_incomplete_directory(directory, query, error):
    with pytest.raises(MeterLookupError, match=error):
        resolve_meters(directory, **query)
