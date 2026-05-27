from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .time_utils import _now_dt

LIBRARY_LOCATIONS_FILE = Path(__file__).resolve().parent / "config" / "library_locations.json"


@lru_cache(maxsize=1)
def load_library_location_map() -> dict[str, str]:
    try:
        data = json.loads(LIBRARY_LOCATIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    raw_locations = data.get("locations") if isinstance(data, dict) else {}
    if not isinstance(raw_locations, dict):
        return {}
    return {
        str(name).strip(): str(area_id).strip()
        for name, area_id in raw_locations.items()
        if str(name).strip() and str(area_id).strip()
    }


class LocationMixin:
    @staticmethod
    def _normalize_area_name(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[（(].*?[）)]", "", text)
        for token in ("河南大学", "图书馆"):
            text = text.replace(token, "")
        return text

    @staticmethod
    def _area_id(area: dict[str, Any]) -> str:
        return str(area.get("id") or area.get("area_id") or area.get("areaId") or "").strip()

    @staticmethod
    def _area_name(area: dict[str, Any]) -> str:
        return str(area.get("name") or area.get("areaName") or area.get("title") or "").strip()

    @classmethod
    def _iter_area_rows(cls, areas: Any):
        if not isinstance(areas, list):
            return
        for area in areas:
            if not isinstance(area, dict):
                continue
            yield area
            for key in ("children", "child", "areas"):
                children = area.get(key)
                if isinstance(children, list):
                    yield from cls._iter_area_rows(children)

    @classmethod
    def _location_summary(cls, area: dict[str, Any], *, source: str = "live") -> dict[str, Any]:
        area_id = cls._area_id(area)
        name = cls._area_name(area)
        return {
            "location": name or area_id,
            "area_id": area_id,
            "source": source,
        }

    @classmethod
    def _summarize_areas(cls, areas: list[dict[str, Any]], *, source: str = "live") -> list[dict[str, Any]]:
        locations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for area in cls._iter_area_rows(areas):
            area_id = cls._area_id(area)
            name = cls._area_name(area)
            if not area_id and not name:
                continue
            key = (area_id, name)
            if key in seen:
                continue
            seen.add(key)
            locations.append(cls._location_summary(area, source=source))
        return locations

    @classmethod
    def _find_area_by_id(cls, areas: list[dict[str, Any]], area_id: str) -> dict[str, Any] | None:
        target_id = str(area_id or "").strip()
        if not target_id:
            return None
        for area in cls._iter_area_rows(areas):
            if cls._area_id(area) == target_id:
                return area
        return None

    @classmethod
    def _find_area_by_name(cls, areas: list[dict[str, Any]], location: str) -> dict[str, Any] | None:
        target = str(location or "").strip()
        target_norm = cls._normalize_area_name(target)
        if not target_norm:
            return None

        area_rows = list(cls._iter_area_rows(areas))
        for area in area_rows:
            if target == cls._area_name(area):
                return area

        for area in area_rows:
            area_norm = cls._normalize_area_name(cls._area_name(area))
            if target_norm and target_norm == area_norm:
                return area

        for area in area_rows:
            area_norm = cls._normalize_area_name(cls._area_name(area))
            if target_norm and area_norm and (target_norm in area_norm or area_norm in target_norm):
                return area

        return None

    def _fetch_pick_areas(self, target_date: str) -> list[dict[str, Any]]:
        resp = self._post_json("/v4/space/pick", {"date": target_date})
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "获取区域列表失败"))
        return ((resp.get("data") or {}).get("area") or [])

    def list_locations(self, target_date: str = "") -> dict[str, Any]:
        target_day = str(target_date or "").strip()
        if not target_day:
            target_day = (_now_dt().date() + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            dt.date.fromisoformat(target_day)
        except ValueError:
            return {"success": False, "msg": "target_date 格式必须为 YYYY-MM-DD", "locations": []}

        if not self._is_token_valid() and not self.login():
            return self._login_failed_result(
                {
                    "date": target_day,
                    "locations": self.static_locations(),
                    "source": "static_fallback",
                    "is_live": False,
                }
            )

        try:
            areas = self._fetch_pick_areas(target_day)
            locations = self._summarize_areas(areas, source="live")
            return {
                "success": True,
                "msg": "操作成功",
                "date": target_day,
                "locations": locations,
                "total": len(locations),
                "source": "live",
                "is_live": True,
            }
        except Exception as exc:
            return {
                "success": False,
                "msg": f"获取实时图书馆区域失败: {exc}",
                "date": target_day,
                "locations": self.static_locations(),
                "source": "static_fallback",
                "is_live": False,
            }

    @classmethod
    def _configured_location_id(cls, location: str) -> str:
        return load_library_location_map().get(str(location or "").strip(), "")

    @classmethod
    def static_locations(cls) -> list[dict[str, Any]]:
        return [
            {"location": name, "area_id": str(area_id), "source": "static_fallback"}
            for name, area_id in load_library_location_map().items()
        ]

    def _resolve_area(self, location_name: str, target_date: str) -> tuple[str, str]:
        location = str(location_name or "").strip()
        if not location:
            raise RuntimeError("区域名称不能为空")

        areas: list[dict[str, Any]] = []
        areas_error = ""
        try:
            areas = self._fetch_pick_areas(target_date)
        except Exception as exc:
            areas_error = str(exc)

        if areas:
            if location.isdigit():
                area = self._find_area_by_id(areas, location)
                if area:
                    return self._area_id(area), self._area_name(area) or location
                raise RuntimeError(f"区域 ID '{location}' 不在 {target_date} 的实时可预约区域列表中")

            area = self._find_area_by_name(areas, location)
            if area:
                return self._area_id(area), self._area_name(area)

            mapped_id = self._configured_location_id(location)
            if mapped_id:
                area = self._find_area_by_id(areas, mapped_id)
                if area:
                    return self._area_id(area), self._area_name(area) or location
                raise RuntimeError(
                    f"区域 '{location}' 的配置映射 {mapped_id} 不在 {target_date} 的实时区域列表中，请先查询 locations"
                )

        if location.isdigit():
            return location, location

        mapped_id = self._configured_location_id(location)
        if mapped_id:
            return mapped_id, location

        if areas_error:
            raise RuntimeError(f"获取实时区域列表失败，且配置映射中没有 '{location}': {areas_error}")
        raise RuntimeError(f"区域 '{location}' 未找到，请先查询 locations 确认实时区域名称")

