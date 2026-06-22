"""空教室查询数据模型。

所有模型使用 frozen dataclass，保证不可变性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Term:
    """学期信息。"""

    term_code: str  # "2025,1"
    term_name: str  # "2025-2026学年第二学期"
    year: str  # "2025"
    term_part: str  # "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "termCode": self.term_code,
            "termName": self.term_name,
            "year": self.year,
            "termPart": self.term_part,
        }


@dataclass(frozen=True)
class Campus:
    """校区信息。"""

    campus_code: str  # "01"
    campus_name: str  # "明伦校区"

    def to_dict(self) -> dict[str, Any]:
        return {
            "campusCode": self.campus_code,
            "campusName": self.campus_name,
        }


@dataclass(frozen=True)
class Building:
    """楼房信息。"""

    building_code: str  # "0013"
    building_name: str  # "十号楼"
    campus_code: str  # "01"

    def to_dict(self) -> dict[str, Any]:
        return {
            "buildingCode": self.building_code,
            "buildingName": self.building_name,
            "campusCode": self.campus_code,
        }


@dataclass(frozen=True)
class RoomType:
    """教室类型。"""

    type_code: str  # "05"
    type_name: str  # "多媒体教室"

    def to_dict(self) -> dict[str, Any]:
        return {
            "typeCode": self.type_code,
            "typeName": self.type_name,
        }


@dataclass(frozen=True)
class Classroom:
    """教室信息。"""

    room_id: str  # "0000231"
    room_name: str  # "十号楼101"
    campus_code: str
    campus_name: str
    building_code: str
    building_name: str
    capacity: int  # 160
    type_name: str  # "多媒体教室"

    def to_dict(self) -> dict[str, Any]:
        return {
            "roomId": self.room_id,
            "roomName": self.room_name,
            "campusCode": self.campus_code,
            "campusName": self.campus_name,
            "buildingCode": self.building_code,
            "buildingName": self.building_name,
            "capacity": self.capacity,
            "typeName": self.type_name,
        }


@dataclass(frozen=True)
class ScheduleCell:
    """课表单元格（一门课在一个时间格的占用）。"""

    term_code: str
    room_id: str
    room_name: str
    day_of_week: int  # 1-7
    period: int  # 1-5, 9=中午
    week_bitmap: list[int]  # 占用的周次列表，如 [1,2,3,...,18]
    week_expr: str  # 原始周次表达式，如 "[1-18]周"
    section_text: str  # "1-2节"
    course_name: str  # "高等数学A（二）"
    teacher_name: str  # "李鸿军"
    class_name: str  # "25软件选课6班"
    student_count: int  # 94
    department_name: str  # "软件学院"
    raw_text: str  # 上游原始文本

    def to_dict(self) -> dict[str, Any]:
        return {
            "termCode": self.term_code,
            "roomId": self.room_id,
            "roomName": self.room_name,
            "dayOfWeek": self.day_of_week,
            "period": self.period,
            "weeks": self.week_bitmap,
            "weekExpr": self.week_expr,
            "sectionText": self.section_text,
            "courseName": self.course_name,
            "teacherName": self.teacher_name,
            "className": self.class_name,
            "studentCount": self.student_count,
            "departmentName": self.department_name,
            "rawText": self.raw_text,
        }

    def occupies(self, week: int, day_of_week: int, period: int) -> bool:
        """判断该单元格是否在指定时间占用。"""
        return (
            self.day_of_week == day_of_week
            and self.period == period
            and week in self.week_bitmap
        )


@dataclass(frozen=True)
class Freshness:
    """缓存新鲜度信息。"""

    strategy: str  # cache_first | live | stale_while_revalidate | cache_only
    source: str  # "xk_room_schedule"
    fetched_at: str  # ISO datetime
    parsed_at: str  # ISO datetime
    ttl_seconds: int  # 300
    age_seconds: int  # 缓存已存在秒数
    stale: bool  # 是否已过期
    live_checked: bool  # 是否实际请求了上游
    note: str = "仅反映学校系统中已登记的课程/占用信息"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "source": self.source,
            "fetchedAt": self.fetched_at,
            "parsedAt": self.parsed_at,
            "ttlSeconds": self.ttl_seconds,
            "ageSeconds": self.age_seconds,
            "stale": self.stale,
            "liveChecked": self.live_checked,
            "note": self.note,
        }


@dataclass(frozen=True)
class SyncResult:
    """同步结果。"""

    term_code: str
    campus_code: str
    building_code: str
    sync_status: str  # "success" | "failed" | "skipped"
    room_count: int
    schedule_cell_count: int
    updated_at: str  # ISO datetime
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "termCode": self.term_code,
            "campusCode": self.campus_code,
            "buildingCode": self.building_code,
            "syncStatus": self.sync_status,
            "roomCount": self.room_count,
            "scheduleCellCount": self.schedule_cell_count,
            "updatedAt": self.updated_at,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class FreeClassroomResult:
    """空教室查询结果。"""

    query: dict[str, Any]  # 查询参数摘要
    total: int  # 空闲教室数量
    rooms: list[dict[str, Any]]  # 空闲教室列表
    freshness: dict[str, Any]  # Freshness.to_dict()
    resolved: dict[str, Any] = field(default_factory=dict)  # 解析后的 campus/building 信息

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "query": self.query,
            "total": self.total,
            "rooms": self.rooms,
            "freshness": self.freshness,
        }
        if self.resolved:
            result["resolved"] = self.resolved
        return result
