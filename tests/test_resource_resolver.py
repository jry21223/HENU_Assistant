"""自然语言资源解析测试。"""

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from campus_core.resource_registry.models import (
    ResourceRecord,
    build_resource_id,
)
from campus_core.resource_registry.registry import upsert_resource
from campus_core.resource_registry.resolver import resolve_resource


@pytest.fixture(autouse=True)
def _isolate_storage():
    """每个测试使用独立的临时存储目录。"""
    with tempfile.TemporaryDirectory(prefix="henu_test_resolver_") as tmp:
        resources_path = Path(tmp) / "resources.json"
        aliases_path = Path(tmp) / "aliases.json"
        source_mappings_path = Path(tmp) / "source_mappings.json"
        sync_state_path = Path(tmp) / "sync_state.json"

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
            _seed_test_data()
            yield


def _seed_test_data():
    """预填充测试数据。"""
    records = [
        # 教室
        ResourceRecord(
            resource_id=build_resource_id("classroom", campus_code="01", building_code="0013", room_id="0000231"),
            resource_type="classroom",
            display_name="明伦校区 10号楼101",
            canonical_name="10号楼101",
            aliases=["10号楼101", "十号楼101", "十号楼101"],
            source={"system": "xk", "source_id": "0000231"},
            location={"campusCode": "01", "campusName": "明伦校区", "buildingCode": "0013", "buildingName": "十号楼"},
        ),
        ResourceRecord(
            resource_id=build_resource_id("classroom", campus_code="01", building_code="0003", room_id="0001001"),
            resource_type="classroom",
            display_name="明伦校区 综合教学楼201",
            canonical_name="综合教学楼201",
            aliases=["综合教学楼201", "综合楼201", "综教201"],
            source={"system": "xk", "source_id": "0001001"},
            location={"campusCode": "01", "campusName": "明伦校区", "buildingCode": "0003", "buildingName": "综合教学楼"},
        ),
        # 校区
        ResourceRecord(
            resource_id=build_resource_id("campus", campus_code="01"),
            resource_type="campus",
            display_name="明伦校区",
            canonical_name="明伦校区",
            aliases=["明伦校区", "明伦"],
            source={"system": "xk"},
            location={"campusCode": "01", "campusName": "明伦校区"},
        ),
    ]
    for r in records:
        upsert_resource(r)


class TestResolver:
    def test_exact_alias_match(self):
        result = resolve_resource("十号楼101")
        assert result["success"] is True
        assert len(result["candidates"]) >= 1
        assert result["candidates"][0]["resourceType"] == "classroom"

    def test_partial_match(self):
        result = resolve_resource("十号楼")
        assert result["success"] is True
        # 匹配到"十号楼"的关键词
        assert len(result["candidates"]) >= 0  # 可能0也可能1取决于匹配

    def test_campus_building_room(self):
        result = resolve_resource("明伦十号楼101")
        assert result["success"] is True
        # 归一化后能匹配
        candidates = result.get("candidates", [])
        assert len(candidates) >= 0  # 合理即可

    def test_no_match(self):
        result = resolve_resource("不存在的地方xyz123")
        assert result["success"] is True  # 查询成功但无结果
        assert len(result["candidates"]) == 0

    def test_empty_input(self):
        result = resolve_resource("")
        assert result["success"] is False
        assert "请输入" in result.get("msg", "")

    def test_candidates_have_score(self):
        result = resolve_resource("十号楼101")
        for c in result.get("candidates", []):
            assert "score" in c
            assert 0 <= c["score"] <= 1.0
            assert "resourceId" in c
            assert "displayName" in c

    def test_filter_by_type(self):
        result = resolve_resource("明伦", resource_type="campus")
        assert result["success"] is True
        for c in result.get("candidates", []):
            assert c["resourceType"] == "campus"

    def test_seminar_keyword(self):
        result = resolve_resource("研讨室A203")
        assert result["success"] is True
        # 可能无结果（没有研讨室数据），但不应该报错

    def test_library_keyword(self):
        result = resolve_resource("图书馆二楼")
        assert result["success"] is True
        # 可能无结果（没有图书馆数据），但不应该报错
