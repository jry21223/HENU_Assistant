"""空教室共享缓存模块测试。

覆盖：
- 数据模型创建与序列化
- 课表 HTML 解析
- 文件锁并发保护
- 缓存读写与脱敏
- 空闲教室计算逻辑
- 周次解析
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

# 确保 mcp-server 在 path 中
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

from campus_core.empty_classroom.models import (
    Building,
    Campus,
    Classroom,
    FreeClassroomResult,
    Freshness,
    RoomType,
    ScheduleCell,
    SyncResult,
    Term,
)
from campus_core.empty_classroom.parser import (
    _parse_course_line,
    _parse_div_id,
    _parse_weeks,
    parse_schedule_html,
)
from campus_core.empty_classroom.lock import FileLock, lock_path_for
from campus_core.empty_classroom.storage import (
    _sanitize,
    compute_query_hash,
    get_locks_dir,
    save_parsed_schedule,
    load_parsed_schedule,
    save_query_cache,
    load_query_cache,
    save_schedule_html,
    load_schedule_html,
)
from campus_core.empty_classroom.query import _compute_free_rooms


# ── 助手 ────────────────────────────────────────────────────


def _temp_cache_dir():
    """创建临时缓存目录并 mock 路径。"""
    tmp = tempfile.mkdtemp(prefix="henu_test_")
    return Path(tmp)


# ── Models 测试 ─────────────────────────────────────────────


class TestModels:
    def test_term_creation_and_serialization(self):
        t = Term(term_code="2025,1", term_name="2025-2026学年第二学期", year="2025", term_part="1")
        d = t.to_dict()
        assert d["termCode"] == "2025,1"
        assert d["termName"] == "2025-2026学年第二学期"
        assert d["year"] == "2025"
        assert d["termPart"] == "1"

    def test_campus_creation(self):
        c = Campus(campus_code="01", campus_name="明伦校区")
        d = c.to_dict()
        assert d["campusCode"] == "01"
        assert d["campusName"] == "明伦校区"

    def test_building_creation(self):
        b = Building(building_code="0013", building_name="十号楼", campus_code="01")
        d = b.to_dict()
        assert d["buildingCode"] == "0013"
        assert d["buildingName"] == "十号楼"
        assert d["campusCode"] == "01"

    def test_classroom_creation(self):
        cr = Classroom(
            room_id="0000231", room_name="十号楼101",
            campus_code="01", campus_name="明伦校区",
            building_code="0013", building_name="十号楼",
            capacity=160, type_name="多媒体教室",
        )
        d = cr.to_dict()
        assert d["roomId"] == "0000231"
        assert d["capacity"] == 160

    def test_room_type_creation(self):
        rt = RoomType(type_code="05", type_name="多媒体教室")
        d = rt.to_dict()
        assert d["typeCode"] == "05"

    def test_schedule_cell_occupies(self):
        cell = ScheduleCell(
            term_code="2025,1", room_id="001", room_name="101",
            day_of_week=1, period=2,
            week_bitmap=[1, 2, 3, 4, 5],
            week_expr="[1-5]周", section_text="3-4节",
            course_name="数学", teacher_name="李老师",
            class_name="1班", student_count=50,
            department_name="数学学院", raw_text="数学 李老师 [1-5]周 3-4节",
        )
        assert cell.occupies(week=3, day_of_week=1, period=2) is True
        assert cell.occupies(week=6, day_of_week=1, period=2) is False
        assert cell.occupies(week=3, day_of_week=2, period=2) is False
        assert cell.occupies(week=3, day_of_week=1, period=1) is False

    def test_schedule_cell_serialization(self):
        cell = ScheduleCell(
            term_code="2025,1", room_id="001", room_name="101",
            day_of_week=1, period=2,
            week_bitmap=[1, 2, 3],
            week_expr="[1-3]周", section_text="3-4节",
            course_name="数学", teacher_name="李老师",
            class_name="1班", student_count=50,
            department_name="数学学院", raw_text="原始文本",
        )
        d = cell.to_dict()
        assert d["termCode"] == "2025,1"
        assert d["weeks"] == [1, 2, 3]
        assert d["dayOfWeek"] == 1
        assert d["period"] == 2

    def test_freshness_creation(self):
        f = Freshness(
            strategy="cache_first", source="xk_room_schedule",
            fetched_at="2026-01-01T00:00:00", parsed_at="2026-01-01T00:00:00",
            ttl_seconds=300, age_seconds=42,
            stale=False, live_checked=True,
        )
        d = f.to_dict()
        assert d["strategy"] == "cache_first"
        assert d["source"] == "xk_room_schedule"
        assert d["stale"] is False
        assert d["liveChecked"] is True
        assert "仅反映" in d["note"]

    def test_sync_result_creation(self):
        sr = SyncResult(
            term_code="2025,1", campus_code="01", building_code="0013",
            sync_status="success", room_count=18, schedule_cell_count=756,
            updated_at="2026-01-01T00:00:00",
        )
        d = sr.to_dict()
        assert d["syncStatus"] == "success"
        assert d["roomCount"] == 18

    def test_sync_result_with_error(self):
        sr = SyncResult(
            term_code="2025,1", campus_code="01", building_code="0013",
            sync_status="failed", room_count=0, schedule_cell_count=0,
            updated_at="2026-01-01T00:00:00", error="上游请求失败",
        )
        d = sr.to_dict()
        assert d["error"] == "上游请求失败"

    def test_free_classroom_result_serialization(self):
        f = Freshness(
            strategy="cache_first", source="xk_room_schedule",
            fetched_at="2026-01-01T00:00:00", parsed_at="2026-01-01T00:00:00",
            ttl_seconds=300, age_seconds=0, stale=False, live_checked=False,
        )
        result = FreeClassroomResult(
            query={"termCode": "2025,1", "week": 18},
            total=2,
            rooms=[
                {"roomId": "001", "roomName": "101", "status": "free", "capacity": 160},
            ],
            freshness=f.to_dict(),
        )
        d = result.to_dict()
        assert d["total"] == 2
        assert len(d["rooms"]) == 1
        assert d["freshness"]["strategy"] == "cache_first"

    def test_models_are_immutable(self):
        """Dataclass frozen=True 确保不可变。"""
        t = Term(term_code="2025,1", term_name="test", year="2025", term_part="1")
        with pytest.raises(Exception):
            t.term_code = "2025,2"  # type: ignore[misc]


# ── 周次解析测试 ────────────────────────────────────────────


class TestWeekParsing:
    def test_simple_range(self):
        assert _parse_weeks("[1-18]周") == list(range(1, 19))

    def test_multi_range(self):
        weeks = _parse_weeks("[1-5,7-18]周")
        assert 1 in weeks
        assert 5 in weeks
        assert 6 not in weeks
        assert 7 in weeks
        assert 18 in weeks

    def test_single_week(self):
        assert _parse_weeks("[4]周") == [4]

    def test_odd_weeks(self):
        weeks = _parse_weeks("[1-10]周 单周")
        assert weeks == [1, 3, 5, 7, 9]

    def test_even_weeks(self):
        weeks = _parse_weeks("[1-10]周 双周")
        assert weeks == [2, 4, 6, 8, 10]

    def test_no_match(self):
        assert _parse_weeks("无周次信息") == []


# ── Div ID 解析测试 ─────────────────────────────────────────


class TestDivIdParsing:
    def test_three_digit(self):
        assert _parse_div_id("011") == (0, 1, 1)

    def test_four_digit(self):
        assert _parse_div_id("1011") == (10, 1, 1)

    def test_various_periods(self):
        assert _parse_div_id("015") == (0, 1, 5)
        assert _parse_div_id("129") == (1, 2, 9)

    def test_invalid(self):
        assert _parse_div_id("") == (0, 0, 0)
        assert _parse_div_id("x") == (0, 0, 0)


# ── 课程文本解析测试 ────────────────────────────────────────


class TestCourseLineParsing:
    def test_standard_format(self):
        info = _parse_course_line(
            "高等数学A（二） 李鸿军 [1-18]周 1-2节 007 25软件选课6班 94 软件学院"
        )
        assert info is not None
        assert info["course_name"] == "高等数学A（二）"
        assert info["teacher_name"] == "李鸿军"
        assert info["week_expr"] == "[1-18]周"
        assert info["section_text"] == "1-2节"
        assert info["student_count"] == 94
        assert info["department_name"] == "软件学院"

    def test_empty_line(self):
        assert _parse_course_line("") is None
        assert _parse_course_line("   ") is None

    def test_no_week_expr(self):
        assert _parse_course_line("某课程 某老师 无周次") is None


# ── HTML 解析测试 ───────────────────────────────────────────


SAMPLE_SCHEDULE_HTML = """<!DOCTYPE html>
<html><head><meta charset="GBK"/></head><body>
<table id="mytable0">
<tr><td>教室：十号楼101(160)</td></tr>
<tr><td>
<div class="div1" id="011">高等数学A（二） 李鸿军 [1-18]周 1-2节 007 25软件选课6班 94 软件学院</div>
<div class="div1" id="012"></div>
<div class="div1" id="021">大学英语（二） 王芳 [1-18]周 3-4节 语音室1 25软件选课6班 94 软件学院</div>
</td></tr>
</table>
<table id="mytable1">
<tr><td>教室：十号楼102(140)</td></tr>
<tr><td>
<div class="div1" id="011">线性代数 张明 [1-18]周 1-2节 008 25软件选课5班 90 软件学院</div>
<div class="div1" id="012">大学物理 陈强 [1-18]周 3-4节 实验室 25软件选课5班 90 物理学院</div>
</td></tr>
</table>
</body></html>"""


class TestScheduleParser:
    def test_parse_basic(self):
        result = parse_schedule_html(SAMPLE_SCHEDULE_HTML, "2025,1", "01", "0013")
        classrooms = result["classrooms"]
        cells = result["cells"]

        assert len(classrooms) >= 1
        assert len(cells) >= 2

    def test_parse_identifies_classrooms(self):
        result = parse_schedule_html(SAMPLE_SCHEDULE_HTML, "2025,1", "01", "0013")
        classrooms = result["classrooms"]
        room_names = [c["room_name"] for c in classrooms]
        # At least one of the rooms should be found
        assert any("101" in name or "102" in name for name in room_names)

    def test_parse_empty_div_not_in_cells(self):
        result = parse_schedule_html(SAMPLE_SCHEDULE_HTML, "2025,1", "01", "0013")
        cells = result["cells"]
        # The empty div (id="012" for table 0) should NOT produce cells
        empty_cell_found = any(
            c.get("dayOfWeek") == 1 and c.get("period") == 2 and c.get("room_name", "").startswith("十号楼101")
            for c in cells
        )
        assert not empty_cell_found

    def test_parse_captures_course_details(self):
        result = parse_schedule_html(SAMPLE_SCHEDULE_HTML, "2025,1", "01", "0013")
        cells = result["cells"]
        course_names = [c.get("courseName", "") for c in cells]
        assert any("高等数学" in name for name in course_names)
        assert any("大学英语" in name for name in course_names)

    def test_parse_handles_empty_html(self):
        result = parse_schedule_html("", "2025,1")
        assert result["parse_errors"] or (not result["cells"] and not result["classrooms"])

    def test_parse_handles_invalid_html(self):
        result = parse_schedule_html("<not>valid<html>", "2025,1")
        # Should not crash, may or may not find divs
        assert isinstance(result, dict)


# ── 文件锁测试 ──────────────────────────────────────────────


class TestFileLock:
    def test_acquire_and_release(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path, timeout=1.0)
        assert lock.acquire() is True
        assert lock_path.exists()
        lock.release()
        assert not lock_path.exists()

    def test_context_manager(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        with FileLock(lock_path, timeout=1.0) as acquired:
            assert acquired is True
            assert lock_path.exists()
        assert not lock_path.exists()

    def test_concurrent_lock_blocks(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        results: list[bool] = []

        def holder():
            lock = FileLock(lock_path, timeout=3.0)
            results.append(lock.acquire())
            time.sleep(0.3)
            lock.release()

        def waiter():
            time.sleep(0.05)
            lock = FileLock(lock_path, timeout=1.0)
            results.append(lock.acquire())
            lock.release()

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=waiter)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results) == 2
        assert results[0] is True  # first should succeed
        assert results[1] is True  # second should succeed (waits for release)

    def test_lock_timeout(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        lock1 = FileLock(lock_path, timeout=5.0)
        assert lock1.acquire() is True

        # Second lock with very short timeout
        lock2 = FileLock(lock_path, timeout=0.1)
        assert lock2.acquire() is False  # should time out

        lock1.release()

    def test_lock_path_for(self, tmp_path):
        path = lock_path_for("2025,1", "01", "0013", tmp_path)
        # "2025,1" 中的逗号被替换为下划线
        assert "2025_1_01_0013" in str(path)
        assert str(path).endswith(".lock")

    def test_stale_lock_break(self, tmp_path):
        """僵尸锁（超过 60 秒）应该被打破。"""
        lock_path = tmp_path / "stale.lock"
        # 手动创建旧锁文件
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("12345")
        # 设置 mtime 为 61 秒前
        stale_time = time.time() - 61
        os.utime(str(lock_path), (stale_time, stale_time))

        lock = FileLock(lock_path, timeout=0.5)
        assert lock.acquire() is True
        lock.release()
        assert not lock_path.exists()


# ── 缓存存储测试 ────────────────────────────────────────────


class TestCacheStorage:
    def test_save_and_load_html(self, tmp_path):
        with mock.patch(
            "campus_core.empty_classroom.storage._schedule_html_dir",
            return_value=tmp_path,
        ):
            path = save_schedule_html("<html>test</html>", "2025,1", "01", "0013")
            assert path.exists()
            loaded = load_schedule_html("2025,1", "01", "0013")
            assert "test" in loaded

    def test_save_and_load_parsed(self, tmp_path):
        with mock.patch(
            "campus_core.empty_classroom.storage._parsed_schedule_dir",
            return_value=tmp_path,
        ):
            data = {
                "classrooms": [{"room_name": "101", "capacity": 160}],
                "cells": [],
            }
            path = save_parsed_schedule(data, "2025,1", "01", "0013")
            assert path.exists()
            loaded = load_parsed_schedule("2025,1", "01", "0013")
            assert loaded["classrooms"][0]["room_name"] == "101"

    def test_save_and_load_query_cache(self, tmp_path):
        with mock.patch(
            "campus_core.empty_classroom.storage._query_cache_dir",
            return_value=tmp_path,
        ):
            data = {"rooms": [{"roomName": "101"}], "total": 1}
            query_hash = "test_hash_123"
            path = save_query_cache(query_hash, data)
            assert path.exists()

            cached = load_query_cache(query_hash, ttl_seconds=300)
            assert cached is not None
            assert cached["total"] == 1

    def test_query_cache_ttl_expired(self, tmp_path):
        with mock.patch(
            "campus_core.empty_classroom.storage._query_cache_dir",
            return_value=tmp_path,
        ):
            data = {"rooms": [], "total": 0}
            query_hash = "test_hash_expired"
            save_query_cache(query_hash, data)

            # TTL=0 总应过期
            cached = load_query_cache(query_hash, ttl_seconds=0)
            assert cached is None

    def test_query_cache_missing(self, tmp_path):
        with mock.patch(
            "campus_core.empty_classroom.storage._query_cache_dir",
            return_value=tmp_path,
        ):
            cached = load_query_cache("nonexistent", ttl_seconds=300)
            assert cached is None

    def test_query_hash_deterministic(self):
        h1 = compute_query_hash(
            term_code="2025,1", week=18, day_of_week=1, period=2,
            campus_code="01", building_code="0013",
        )
        h2 = compute_query_hash(
            term_code="2025,1", week=18, day_of_week=1, period=2,
            campus_code="01", building_code="0013",
        )
        assert h1 == h2
        assert len(h1) == 16

    def test_query_hash_differs(self):
        h1 = compute_query_hash(
            term_code="2025,1", week=18, day_of_week=1, period=2,
        )
        h2 = compute_query_hash(
            term_code="2025,1", week=18, day_of_week=1, period=3,
        )
        assert h1 != h2


# ── 数据脱敏测试 ────────────────────────────────────────────


class TestSanitization:
    def test_removes_cookie_keys(self):
        data = {
            "classrooms": [{"room_name": "101"}],
            "CASTGC": "secret-tgt-value",
            "cookies": {"JSESSIONID": "abc123", "name": "test"},
            "token": "bearer-secret",
        }
        clean = _sanitize(data)
        assert "CASTGC" not in clean
        assert "JSESSIONID" not in str(clean)
        assert "token" not in clean
        # 正常数据应保留
        assert "classrooms" in clean

    def test_removes_sensitive_string_values(self):
        data = {
            "header": "CASTGC=abc123; JSESSIONID=def456",
            "info": "normal info",
        }
        clean = _sanitize(data)
        assert "CASTGC" not in str(clean)
        assert "JSESSIONID" not in str(clean)
        assert "normal info" in str(clean)

    def test_nested_sensitive_removal(self):
        data = {
            "result": {
                "auth": {"cookie": "secret", "CASTGC": "tgt"},
                "data": {"room": "101"},
            }
        }
        clean = _sanitize(data)
        assert "CASTGC" not in str(clean)
        assert "cookie" not in str(clean)
        assert "101" in str(clean)

    def test_list_items_sanitized(self):
        data = {
            "items": [
                {"name": "ok", "password": "secret1"},
                {"name": "also_ok", "Cookie": "secret2"},
            ]
        }
        clean = _sanitize(data)
        clean_str = json.dumps(clean)
        assert "password" not in clean_str
        assert "Cookie" not in clean_str
        assert "ok" in clean_str
        assert "also_ok" in clean_str


# ── 空闲教室计算逻辑测试 ────────────────────────────────────


class TestFreeRoomComputation:
    def test_all_free_when_no_cells(self):
        classrooms = [
            {"room_name": "101", "capacity": 160, "room_id": "001", "type_name": "多媒体教室"},
            {"room_name": "102", "capacity": 140, "room_id": "002", "type_name": "多媒体教室"},
        ]
        free = _compute_free_rooms(classrooms, [], week=18, day_of_week=1, period=2)
        assert len(free) == 2

    def test_occupied_room_excluded(self):
        classrooms = [
            {"room_name": "101", "capacity": 160},
            {"room_name": "102", "capacity": 140},
        ]
        # 101 在周18周一第二大节被占用
        cells = [
            ScheduleCell(
                term_code="2025,1", room_id="001", room_name="101",
                day_of_week=1, period=2, week_bitmap=[18],
                week_expr="[18]周", section_text="1-2节",
                course_name="数学", teacher_name="老师",
                class_name="1班", student_count=50,
                department_name="学院", raw_text="",
            ).to_dict(),
        ]
        free = _compute_free_rooms(classrooms, cells, week=18, day_of_week=1, period=2)
        assert len(free) == 1
        assert free[0]["roomName"] == "102"

    def test_different_week_not_excluded(self):
        classrooms = [
            {"room_name": "101", "capacity": 160},
        ]
        cells = [
            ScheduleCell(
                term_code="2025,1", room_id="001", room_name="101",
                day_of_week=1, period=2, week_bitmap=[17],  # week 17 only
                week_expr="[17]周", section_text="1-2节",
                course_name="数学", teacher_name="老师",
                class_name="1班", student_count=50,
                department_name="学院", raw_text="",
            ).to_dict(),
        ]
        free = _compute_free_rooms(classrooms, cells, week=18, day_of_week=1, period=2)
        assert len(free) == 1  # 第18周空闲

    def test_different_period_not_excluded(self):
        classrooms = [
            {"room_name": "101", "capacity": 160},
        ]
        cells = [
            ScheduleCell(
                term_code="2025,1", room_id="001", room_name="101",
                day_of_week=1, period=1,  # period 1, not period 2
                week_bitmap=[18],
                week_expr="[18]周", section_text="1-2节",
                course_name="数学", teacher_name="老师",
                class_name="1班", student_count=50,
                department_name="学院", raw_text="",
            ).to_dict(),
        ]
        free = _compute_free_rooms(classrooms, cells, week=18, day_of_week=1, period=2)
        assert len(free) == 1

    def test_min_capacity_filter(self):
        classrooms = [
            {"room_name": "小教室", "capacity": 30},
            {"room_name": "大教室", "capacity": 200},
        ]
        free = _compute_free_rooms(classrooms, [], week=18, day_of_week=1, period=2, min_capacity=60)
        assert len(free) == 1
        assert free[0]["roomName"] == "大教室"

    def test_keyword_filter(self):
        classrooms = [
            {"room_name": "十号楼101", "capacity": 160},
            {"room_name": "综合楼201", "capacity": 140},
        ]
        free = _compute_free_rooms(classrooms, [], week=18, day_of_week=1, period=2, keyword="十号楼")
        assert len(free) == 1
        assert free[0]["roomName"] == "十号楼101"

    def test_result_sorted_by_name(self):
        classrooms = [
            {"room_name": "103", "capacity": 100},
            {"room_name": "101", "capacity": 100},
            {"room_name": "102", "capacity": 100},
        ]
        free = _compute_free_rooms(classrooms, [], week=1, day_of_week=1, period=1)
        names = [r["roomName"] for r in free]
        assert names == sorted(names)
