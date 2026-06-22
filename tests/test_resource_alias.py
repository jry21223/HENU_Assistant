"""资源别名归一化测试。"""

import pytest

from campus_core.resource_registry.alias import (
    generate_aliases,
    normalize,
    normalize_building_name,
    normalize_campus_name,
    normalize_room_name,
)


class TestCampusNormalization:
    def test_minglun(self):
        assert normalize_campus_name("明伦") == "明伦校区"
        assert normalize_campus_name("明伦校区") == "明伦校区"

    def test_jinming(self):
        assert normalize_campus_name("金明") == "金明校区"
        assert normalize_campus_name("金明校区") == "金明校区"

    def test_zhengzhou(self):
        assert normalize_campus_name("郑州") == "郑州校区"
        assert normalize_campus_name("郑州校区") == "郑州校区"

    def test_unknown_passthrough(self):
        assert normalize_campus_name("未知校区") == "未知校区"


class TestBuildingNormalization:
    def test_cn_digit_to_arabic(self):
        assert normalize_building_name("十号楼") == "10号楼"
        assert normalize_building_name("一号楼") == "1号楼"
        assert normalize_building_name("二十三号楼") == "23号楼"

    def test_special_aliases(self):
        assert normalize_building_name("综合楼") == "综合教学楼"
        assert normalize_building_name("综教") == "综合教学楼"

    def test_fullwidth(self):
        result = normalize_building_name("３号楼")  # 全角3
        assert "3" in result

    def test_already_normal(self):
        assert normalize_building_name("10号楼") == "10号楼"
        assert normalize_building_name("综合教学楼") == "综合教学楼"


class TestRoomNormalization:
    def test_fullwidth_to_halfwidth(self):
        result = normalize_room_name("Ａ２０３")  # 全角A, 全角2, 全角0, 全角3
        assert "A" in result
        assert "203" in result


class TestNormalize:
    def test_full_text_campus_building_room(self):
        result = normalize("明伦十号楼101")
        assert "明伦校区" in result
        assert "10号楼" in result
        assert "101" in result

    def test_no_campus(self):
        result = normalize("十号楼101")
        assert "10号楼" in result
        assert "101" in result

    def test_campus_building(self):
        result = normalize("金明综合教学楼")
        assert "金明校区" in result
        assert "综合教学楼" in result

    def test_minglun_library(self):
        result = normalize("明伦图书馆二楼")
        assert "明伦校区" in result
        assert "图书馆" in result

    def test_empty(self):
        assert normalize("") == ""
        assert normalize("   ") == ""


class TestGenerateAliases:
    def test_basic(self):
        aliases = generate_aliases("明伦校区", "十号楼", "101")
        assert "10号楼" in aliases
        assert "10号楼101" in aliases

    def test_no_room(self):
        aliases = generate_aliases("金明校区", "综合教学楼")
        assert "综合教学楼" in aliases
        # 可能有"综合"这类缩写
        assert len(aliases) > 0

    def test_no_duplicates(self):
        aliases = generate_aliases("明伦校区", "十号楼", "101")
        assert len(aliases) == len(set(aliases))
