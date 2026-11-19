"""从官方房间目录选空调表或照明表。适用于已确认分表的楼栋。"""

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal


class MeterLookupError(ValueError):
    pass


@dataclass(frozen=True)
class RoomOption:
    id: str
    name: str


@dataclass(frozen=True)
class FloorDirectory:
    campus: str
    building: str
    floor: str
    rooms: tuple[RoomOption, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedMeter:
    kind: Literal["aircon", "lighting"]
    campus: str
    building: str
    floor: str
    room: str
    label: str
    roomverify: str

    def query_params(self, *, schoolcode: str, pay_pro_id: str, business_type: str) -> dict[str, str]:
        return {
            "schoolcode": schoolcode, "payProId": pay_pro_id,
            "businesstype": business_type, "roomverify": self.roomverify,
        }


def _normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def _building(value: str) -> str:
    return re.sub(r"(?:号(?:公寓|宿舍|楼|栋)?|栋|幢)$", "", _normalize(value))


def _floor(value: str) -> str:
    return re.sub(r"[楼层]$", "", _normalize(value))


def _numbered_building(value: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"([^0-9]*)([0-9]+)", _building(value))
    return (match[1], match[2]) if match else None


def _room_label(value: str) -> str:
    return _normalize(value).removesuffix("房间")


def _area(value: str) -> str:
    return re.sub(r"(?:学生公寓|公寓|宿舍|园区)$", "", value)


def split_meter_text(text: str) -> tuple[str, str, Literal["aircon", "lighting"] | None]:
    text = re.sub(r"^(?:帮我查|查询|查)", "", _normalize(text))
    shorthand = re.fullmatch(
        r"([^0-9]+)([0-9]+)(?:房间|房|室)?"
        r"(?:(?:的)?(?:(空调|照明)(?:电量|电费|电|表)?|空调和照明|照明和空调|两块都查|都查))?",
        text,
    )
    if not shorthand:
        raise MeterLookupError("请提供园区和房间编号。")
    place, code, meter = shorthand.groups()
    kind = "aircon" if meter == "空调" else "lighting" if meter == "照明" else None
    return _area(place), code, kind


def matches_area(place: str, campus: str, stem: str, aliases: tuple[str, ...]) -> bool:
    campus = _normalize(campus)
    areas = {_area(stem), *(_area(_normalize(alias)) for alias in aliases)}
    return any(
        place == prefix + name for prefix in ("", campus, campus.removesuffix("校区"))
        for name in areas if name
    )


def resolve_meter_text(
    directory: tuple[FloorDirectory, ...], text: str, *, require_pair: bool = False,
) -> tuple[ResolvedMeter, ...]:
    """简写中的完整编号必须对应一个官方目录显示项。"""
    place, code, meter = split_meter_text(text)

    matches = []
    for entry in directory:
        building = _numbered_building(entry.building)
        if building is None:
            continue
        stem, prefix = building
        if not matches_area(place, entry.campus, stem, entry.aliases):
            continue
        labels = tuple(_room_label(room.name) for room in entry.rooms)
        matches.extend((entry, prefix, labels) for label in labels if label == code)

    if not matches:
        raise MeterLookupError("官方目录未收录该园区的这个编号，请更新目录或核对编号。")
    if len(matches) != 1:
        raise MeterLookupError("该编号匹配多个目录项，请补校区、楼栋或楼层。")
    entry, prefix, labels = matches[0]
    room = code.removeprefix(prefix)
    if room != code and room not in labels:
        raise MeterLookupError("目录缺少同房的实际房号，无法确认这个编号的表型。")
    return resolve_meters(
        (entry,), campus=entry.campus, building=entry.building,
        floor=entry.floor, room=room, meter=meter, require_pair=require_pair,
    )


def resolve_meters(
    directory: tuple[FloorDirectory, ...], *, campus: str, building: str, room: str,
    meter: Literal["aircon", "lighting", "both"] | None = None, floor: str | None = None,
    require_pair: bool = False,
) -> tuple[ResolvedMeter, ...]:
    """directory 包含该分表楼栋的候选楼层，ID 来自每层官方 roomlist。"""
    if not all(isinstance(item, str) for item in (campus, building, room)):
        raise MeterLookupError("请给出校区、楼栋和实际房号。")
    campus_key = _normalize(campus).removesuffix("校区")
    building_key = _building(building)
    room_number = re.sub(r"(?:房间|房|室)$", "", _normalize(room))
    if not campus_key or not building_key or not re.fullmatch(r"[0-9]+", room_number):
        raise MeterLookupError("请给出校区、楼栋和实际房号。")
    if meter not in (None, "aircon", "lighting", "both"):
        raise MeterLookupError("请选择空调、照明或两项。")
    if floor is not None and (not isinstance(floor, str) or not _floor(floor)):
        raise MeterLookupError("楼层格式无效。")

    floors = [
        entry for entry in directory
        if _normalize(entry.campus).removesuffix("校区") == campus_key
        and _building(entry.building) == building_key
        and (floor is None or _floor(entry.floor) == _floor(floor))
    ]
    if not floors:
        raise MeterLookupError("官方目录中未匹配到该校区、楼栋或楼层。")
    numbered_building = _numbered_building(floors[0].building)
    if numbered_building is None:
        raise MeterLookupError("官方楼栋没有唯一楼号，无法按分表规则匹配。")
    _, prefix = numbered_building
    names = {"aircon": room_number, "lighting": prefix + room_number}

    matches = []
    for entry in floors:
        labels = [_room_label(item.name) for item in entry.rooms]
        if room_number in labels and room_number.startswith(prefix) and room_number[len(prefix):] in labels:
            raise MeterLookupError("该编号可能是照明表，请填写实际房号并另选表型。")
        options = {}
        for kind, number in names.items():
            records = [item for item, label in zip(entry.rooms, labels) if label == number]
            if len(records) > 1:
                raise MeterLookupError("官方目录有重复电表项，请核对房间。")
            if records:
                option = records[0]
                if not option.id.strip():
                    raise MeterLookupError("官方目录未提供电表 ID。")
                options[kind] = ResolvedMeter(kind, entry.campus, entry.building, entry.floor, room_number, option.name, option.id)
        if options:
            matches.append(options)

    if not matches:
        raise MeterLookupError("官方目录里没有匹配的房间或电表。")
    if len(matches) > 1:
        raise MeterLookupError("多个楼层有匹配项，请补楼层。")
    options = matches[0]
    if len({item.roomverify for item in options.values()}) != len(options):
        raise MeterLookupError("空调和照明指向同一目录 ID，请核对表型。")
    if require_pair and len(options) != 2:
        raise MeterLookupError("目录未确认该房间为空调、照明分表，请核对表型。")
    if meter in (None, "both"):
        if meter == "both" and len(options) != 2:
            raise MeterLookupError("目录未同时提供空调表和照明表。")
        return tuple(options.values())
    if meter not in options:
        raise MeterLookupError("官方目录未找到指定类型的电表。")
    return (options[meter],)
