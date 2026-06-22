"""全局资源编号映射模块。

统一管理教室、图书馆区域/座位、研讨室、楼房、校区的资源标识。
"""

from .alias import (
    generate_aliases,
    normalize,
    normalize_building_name,
    normalize_campus_name,
    normalize_room_name,
)
from .models import (
    ALL_RESOURCE_TYPES,
    ResourceRecord,
    ResolveCandidate,
    build_resource_id,
    parse_resource_id,
)
from .registry import (
    ensure_classroom_resource,
    get_resource,
    get_stats,
    list_resources,
    search_resources,
    upsert_resource,
)
from .resolver import resolve_resource
from .seed import preload_seed_if_needed
from .sync import (
    sync_classrooms_from_metadata,
    sync_library_resources,
    sync_seminar_resources,
)

__all__ = [
    # Models
    "ResourceRecord",
    "ResolveCandidate",
    "build_resource_id",
    "parse_resource_id",
    "ALL_RESOURCE_TYPES",
    # Alias
    "normalize",
    "normalize_campus_name",
    "normalize_building_name",
    "normalize_room_name",
    "generate_aliases",
    # Registry
    "upsert_resource",
    "get_resource",
    "search_resources",
    "list_resources",
    "get_stats",
    "ensure_classroom_resource",
    # Resolver
    "resolve_resource",
    # Sync
    "sync_classrooms_from_metadata",
    "sync_library_resources",
    "sync_seminar_resources",
    # Seed
    "preload_seed_if_needed",
]
