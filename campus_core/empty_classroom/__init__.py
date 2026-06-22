"""空教室查询模块。

提供基于学校教务系统教室课表的空教室查询能力。

主要入口：
- query_free_classrooms(): 查询指定时间的空闲教室
- sync_schedule(): 同步指定楼房的教室课表缓存

安全约束：共享缓存中不存放 Cookie、密码、JSESSIONID、CASTGC。
"""

from .client import EmptyClassroomClient
from .models import (
    Building,
    Campus,
    Classroom,
    FreeClassroomResult,
    Freshness,
    RoomType,
    ScheduleCell,
    Term,
)
from .query import query_free_classrooms, sync_schedule

__all__ = [
    "EmptyClassroomClient",
    "Term",
    "Campus",
    "Building",
    "Classroom",
    "RoomType",
    "ScheduleCell",
    "FreeClassroomResult",
    "Freshness",
    "query_free_classrooms",
    "sync_schedule",
]
