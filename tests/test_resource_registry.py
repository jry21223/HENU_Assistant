"""资源 Registry CRUD 测试。"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from campus_core.resource_registry.models import (
    ResourceRecord,
    build_resource_id,
    parse_resource_id,
)
from campus_core.resource_registry.registry import (
    ensure_classroom_resource,
    get_resource,
    get_stats,
    list_resources,
    search_resources,
    upsert_resource,
)
from campus_core.resource_registry.storage import (
    load_resources,
    safe_save_resources,
)


@pytest.fixture(autouse=True)
def _isolate_storage():
    """每个测试使用独立的临时存储目录。"""
    with tempfile.TemporaryDirectory(prefix="henu_test_registry_") as tmp:
        resources_path = Path(tmp) / "resources.json"
        aliases_path = Path(tmp) / "aliases.json"
        source_mappings_path = Path(tmp) / "source_mappings.json"
        sync_state_path = Path(tmp) / "sync_state.json"

        # Mock 底层路径
        with mock.patch(
            "campus_core.resource_registry.storage._resources_path",
            return_value=resources_path,
        ), mock.patch(
            "campus_core.resource_registry.storage._aliases_path",
            return_value=aliases_path,
        ), mock.patch(
            "campus_core.resource_registry.storage._source_mappings_path",
            return_value=source_mappings_path,
        ), mock.patch(
            "campus_core.resource_registry.storage._sync_state_path",
            return_value=sync_state_path,
        ):
            yield


def _make_classroom_record(
    campus_code="01",
    campus_name="明伦校区",
    building_code="0013",
    building_name="十号楼",
    room_id="0000231",
    room_name="十号楼101",
    capacity=160,
) -> ResourceRecord:
    rid = build_resource_id(
        "classroom",
        campus_code=campus_code,
        building_code=building_code,
        room_id=room_id,
    )
    return ResourceRecord(
        resource_id=rid,
        resource_type="classroom",
        display_name=f"{campus_name} 10号楼101",
        canonical_name="10号楼101",
        aliases=["10号楼101", "十号楼101"],
        source={"system": "xk", "source_id": room_id},
        location={
            "campusCode": campus_code,
            "campusName": campus_name,
            "buildingCode": building_code,
            "buildingName": building_name,
            "roomName": room_name,
            "capacity": capacity,
        },
        attributes={"capacity": capacity},
    )


class TestRegistryCRUD:
    def test_upsert_and_get(self):
        record = _make_classroom_record()
        upsert_resource(record)

        found = get_resource(record.resource_id)
        assert found is not None
        assert found.resource_id == record.resource_id
        assert found.display_name == record.display_name
        assert found.canonical_name == "10号楼101"

    def test_upsert_is_idempotent(self):
        record = _make_classroom_record()
        upsert_resource(record)
        upsert_resource(record)  # 重复 upsert 不应报错
        found = get_resource(record.resource_id)
        assert found is not None

    def test_get_nonexistent(self):
        assert get_resource("henu:classroom:xk:99:9999:9999999") is None

    def test_search_by_query(self):
        record = _make_classroom_record()
        upsert_resource(record)

        # 按别名搜索
        results = search_resources(query="十号楼101")
        assert len(results) >= 1

    def test_search_by_resource_type(self):
        record = _make_classroom_record()
        upsert_resource(record)

        results = search_resources(resource_type="classroom")
        assert len(results) >= 1

        results = search_resources(resource_type="seminar_room")
        assert len(results) == 0

    def test_list_resources(self):
        record = _make_classroom_record()
        upsert_resource(record)

        results = list_resources(resource_type="classroom")
        assert len(results) >= 1

    def test_get_stats(self):
        record1 = _make_classroom_record()
        upsert_resource(record1)

        # 添加不同类型
        rid2 = build_resource_id("campus", campus_code="01")
        record2 = ResourceRecord(
            resource_id=rid2,
            resource_type="campus",
            display_name="明伦校区",
            canonical_name="明伦校区",
            aliases=["明伦校区"],
            source={"system": "xk"},
            location={"campusCode": "01", "campusName": "明伦校区"},
        )
        upsert_resource(record2)

        stats = get_stats()
        assert stats.get("classroom", 0) >= 1
        assert stats.get("campus", 0) >= 1
        assert stats.get("total", 0) >= 2

    def test_ensure_classroom_resource(self):
        record = ensure_classroom_resource(
            campus_code="01",
            campus_name="明伦校区",
            building_code="0013",
            building_name="十号楼",
            room_id="0000231",
            room_name="十号楼101",
            capacity=160,
            type_name="多媒体教室",
        )
        assert record is not None
        assert record.resource_type == "classroom"
        assert "明伦校区" in record.display_name
        assert record.attributes.get("auto_created") is True

        # 再次调用应返回已有记录
        record2 = ensure_classroom_resource(
            campus_code="01",
            campus_name="明伦校区",
            building_code="0013",
            building_name="十号楼",
            room_id="0000231",
            room_name="十号楼101",
        )
        assert record2.resource_id == record.resource_id


class TestResourceID:
    def test_build_classroom(self):
        rid = build_resource_id("classroom", campus_code="01", building_code="0013", room_id="0000231")
        assert rid == "henu:classroom:xk:01:0013:0000231"

    def test_build_library_area(self):
        rid = build_resource_id("library_area", library_id="henu_library", area_id="8")
        assert rid == "henu:library:area:henu_library:8"

    def test_build_library_seat(self):
        rid = build_resource_id("library_seat", library_id="henu_library", area_id="8", seat_no="A101")
        assert rid == "henu:library:seat:henu_library:8:A101"

    def test_build_seminar_room(self):
        rid = build_resource_id("seminar_room", area_id="abc123")
        assert rid == "henu:seminar:room:abc123"

    def test_build_building(self):
        rid = build_resource_id("building", campus_code="01", building_code="0013")
        assert rid == "henu:building:01:0013"

    def test_build_campus(self):
        rid = build_resource_id("campus", campus_code="01")
        assert rid == "henu:campus:01"

    def test_parse_classroom(self):
        parsed = parse_resource_id("henu:classroom:xk:01:0013:0000231")
        assert parsed["type"] == "classroom"
        assert parsed["campus_code"] == "01"
        assert parsed["building_code"] == "0013"
        assert parsed["room_id"] == "0000231"

    def test_parse_library_seat(self):
        parsed = parse_resource_id("henu:library:seat:henu_library:8:A101")
        assert parsed["type"] == "library_seat"
        assert parsed["library_id"] == "henu_library"
        assert parsed["area_id"] == "8"
        assert parsed["seat_no"] == "A101"

    def test_parse_seminar(self):
        parsed = parse_resource_id("henu:seminar:room:abc123")
        assert parsed["type"] == "seminar_room"
        assert parsed["area_id"] == "abc123"

    def test_parse_unknown(self):
        parsed = parse_resource_id("unknown:format")
        assert parsed["type"] == "unknown"


class TestStorageSecurity:
    def test_no_sensitive_in_resources(self):
        """验证 resources.json 不含敏感关键词。"""
        resources = {
            "henu:classroom:xk:01:0013:0000231": {
                "resourceId": "henu:classroom:xk:01:0013:0000231",
                "resourceType": "classroom",
                "displayName": "明伦校区 10号楼101",
                "canonicalName": "10号楼101",
                "aliases": ["10号楼101"],
                "source": {"system": "xk"},
                "location": {"campusCode": "01"},
            }
        }
        assert safe_save_resources(resources) is True

    def test_rejects_sensitive_data(self):
        """验证含敏感关键词的数据被拒绝保存。"""
        resources = {
            "henu:classroom:xk:01:0013:bad": {
                "resourceId": "henu:classroom:xk:01:0013:bad",
                "resourceType": "classroom",
                "displayName": "bad",
                "CASTGC": "secret-tgt",
            }
        }
        assert safe_save_resources(resources) is False
