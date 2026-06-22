"""Seed 数据预加载器。

首次访问 resource_registry 时，自动从 building_seed.json 导入
校区、楼房、教室资源记录到 data/shared/resource_registry/。

Seed 文件由维护流程预生成并随配置文件发布；运行时不依赖生成脚本。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .models import ResourceRecord, build_resource_id
from .registry import get_stats, upsert_resource
from .storage import load_sync_state, update_sync_state

_logger = logging.getLogger(__name__)

_SEED_PATH = Path(__file__).resolve().parent.parent / "config" / "building_seed.json"


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


def is_seed_loaded() -> bool:
    """检查 seed 数据是否已导入 registry。"""
    state = load_sync_state()
    return state.get("seed_classrooms", {}).get("status") == "loaded"


def preload_seed_if_needed(force: bool = False) -> dict[str, Any]:
    """如果 seed 未导入，自动从 building_seed.json 导入资源。

    Args:
        force: 强制重新导入。

    Returns:
        {"loaded": bool, "synced_count": int, "msg": str}
    """
    if not force and is_seed_loaded():
        return {"loaded": False, "synced_count": 0, "msg": "seed 已导入，跳过"}

    if not _SEED_PATH.exists():
        return {"loaded": False, "synced_count": 0, "msg": f"seed 文件不存在: {_SEED_PATH}"}

    try:
        seed_data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"loaded": False, "synced_count": 0, "msg": f"seed 文件解析失败: {exc}"}

    synced = 0

    for campus_code, campus_info in seed_data.items():
        if not isinstance(campus_info, dict):
            continue

        campus_name = campus_info.get("campus_name", campus_code)

        # ── 校区资源 ──
        campus_rid = build_resource_id("campus", campus_code=campus_code)
        upsert_resource(
            ResourceRecord(
                resource_id=campus_rid,
                resource_type="campus",
                display_name=campus_name,
                canonical_name=campus_name,
                aliases=[campus_name],
                source={"system": "xk", "source_id": campus_code},
                location={"campusCode": campus_code, "campusName": campus_name},
                updated_at=_now_iso(),
            )
        )
        synced += 1

        # ── 楼房资源 ──
        buildings = campus_info.get("buildings", {})
        for building_code, building_info in buildings.items():
            if not isinstance(building_info, dict):
                continue

            building_name = building_info.get("building_name", building_code)

            from .alias import generate_aliases, normalize_building_name

            cn_building = normalize_building_name(building_name)

            building_rid = build_resource_id(
                "building", campus_code=campus_code, building_code=building_code
            )
            upsert_resource(
                ResourceRecord(
                    resource_id=building_rid,
                    resource_type="building",
                    display_name=f"{campus_name} {cn_building}",
                    canonical_name=cn_building,
                    aliases=generate_aliases(campus_name, building_name),
                    source={
                        "system": "xk",
                        "source_id": building_code,
                    },
                    location={
                        "campusCode": campus_code,
                        "campusName": campus_name,
                        "buildingCode": building_code,
                        "buildingName": building_name,
                    },
                    updated_at=_now_iso(),
                )
            )
            synced += 1

            # ── 教室资源 ──
            classrooms = building_info.get("classrooms", [])
            for cr in classrooms:
                room_id = str(cr.get("room_id", ""))
                room_name = str(cr.get("room_name", ""))
                capacity = cr.get("capacity", 0)
                type_name = str(cr.get("type_name", ""))

                if not room_id or not room_name:
                    continue

                from .alias import generate_aliases as gen_alias, normalize_room_name

                cn_room = normalize_room_name(room_name)

                # 去掉 room_name 中重复的楼房名前缀
                # 例: building="十号楼", room="十号楼101" → cn_room 应只保留 "101"
                short_room = cn_room
                if cn_building and short_room.startswith(cn_building):
                    short_room = short_room[len(cn_building):]

                display_name = f"{campus_name} {cn_building}{short_room}"
                canonical_name = f"{cn_building}{short_room}"

                # 生成别名：包含原始中文数字形式
                aliases = gen_alias(campus_name, building_name, room_name)
                # 额外保留原始 room_name 作为别名
                if room_name not in aliases:
                    aliases.append(room_name)
                if short_room and short_room not in aliases:
                    aliases.append(short_room)

                rid = build_resource_id(
                    "classroom",
                    campus_code=campus_code,
                    building_code=building_code,
                    room_id=room_id,
                )

                upsert_resource(
                    ResourceRecord(
                        resource_id=rid,
                        resource_type="classroom",
                        display_name=display_name,
                        canonical_name=canonical_name,
                        aliases=aliases,
                        source={
                            "system": "xk",
                            "source_id": room_id,
                            "source_room_code": room_name,
                            "source_building_code": building_code,
                        },
                        location={
                            "campusCode": campus_code,
                            "campusName": campus_name,
                            "buildingCode": building_code,
                            "buildingName": building_name,
                            "roomName": room_name,
                            "capacity": capacity,
                            "typeName": type_name,
                        },
                        attributes={
                            "capacity": capacity,
                            "type_name": type_name,
                            "from_seed": True,
                        },
                        updated_at=_now_iso(),
                    )
                )
                synced += 1

    update_sync_state("seed_classrooms", "loaded", {"synced_count": synced})
    return {"loaded": True, "synced_count": synced, "msg": f"从 seed 导入 {synced} 条资源"}
