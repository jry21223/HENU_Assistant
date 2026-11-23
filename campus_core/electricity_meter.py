"""从官方房间目录选空调表或照明表。适用于已确认分表的楼栋。"""

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Iterator, Literal, Sequence


class MeterLookupError(ValueError):
    def __init__(self, message: str, candidates: tuple["LookupCandidate", ...] = (), *,
                 reason: str = "missing"):
        super().__init__(message)
        self.candidates = candidates
        self.reason = reason


@dataclass(frozen=True)
class LookupCandidate:
    """查不到或多处匹配时给用户的近似选项，command_text 可直接再查。"""

    campus: str
    building: str
    room: str
    command_text: str
    floor: str | None = None
    meter: str | None = None


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


@dataclass(frozen=True)
class MeterAddress:
    place: str
    number: str
    meter: Literal["aircon", "lighting"] | None
    building_number: str | None = None
    floor: str | None = None
    campus: str | None = None

    def matches_building(self, campus: str, building: str, aliases: tuple[str, ...]) -> bool:
        if self.campus is not None and _normalize(campus).removesuffix("校区") != self.campus:
            return False
        numbered = _numbered_building(building)
        if numbered is None:
            return False
        stem, number = numbered
        return (self.building_number in (None, number) and matches_area(self.place, campus, stem, aliases))
    def compact_building_prefixes(self, buildings: Sequence[str]) -> set[str]:
        numbers = [_numbered_building(building) for building in buildings]
        prefixes = {
            entry[1] for entry in numbers
            if entry is not None and len(entry[1]) < len(self.number) and self.number.startswith(entry[1])
        }
        if not prefixes and len(buildings) > 1:
            raise MeterLookupError("该园区有多个楼栋，请补楼号。")
        return prefixes


def parse_meter_text(text: str) -> MeterAddress:
    text = re.sub(r"^(?:帮我查|查询|查)", "", unicodedata.normalize("NFKC", text).strip()).strip()
    address, option, floor = text.partition("--floor")
    floor_number = _floor(floor) if option else None
    if option and (not floor[:1].isspace() or len(floor.split()) != 1 or not floor_number
                   or floor_number.startswith("--")):
        raise MeterLookupError("楼层格式无效，请使用 --floor <楼层号>。")
    campus_match = re.match(r"^([^0-9]+?)校区\s*(?=[^0-9\s])", address)
    campus = _normalize(campus_match[1]) if campus_match else None
    if campus_match:
        address = address[campus_match.end():]
    # 保留两个数字段之间的分隔，其余空格可由用户自由输入。
    address = re.sub(r"(?<=[0-9])\s+(?=[0-9])", " ", address.strip())
    address = re.sub(r"(?<![0-9])\s+|\s+(?![0-9])", "", address)
    match = re.fullmatch(
        r"([^0-9]+)(?:([0-9]+)(?: |号(?:公寓|宿舍|楼|栋)?|栋|幢))?"
        r"([0-9]+)(?:房间|房|室)?"
        r"(?:(?:的)?(?:(空调|照明)(?:电量|电费|电|表)?|空调和照明|照明和空调|两块都查|都查))?",
        address,
    )
    if not match:
        raise MeterLookupError("请提供园区和房间编号。")
    place, building_number, number, meter = match.groups()
    kind = {"空调": "aircon", "照明": "lighting"}.get(meter)
    return MeterAddress(_area(place), number, kind, building_number, floor_number, campus)


def matches_area(place: str, campus: str, stem: str, aliases: tuple[str, ...]) -> bool:
    campus = _normalize(campus)
    areas = {_area(stem), *(_area(_normalize(alias)) for alias in aliases)}
    return any(
        place == prefix + name for prefix in ("", campus, campus.removesuffix("校区"))
        for name in areas if name
    )


def _similarity(place: str, names: Sequence[str]) -> float:
    scored = 0.0
    for name in names:
        if not name:
            continue
        scored = max(scored, SequenceMatcher(None, place, name).ratio())
        if len(name) >= 2 and (name in place or place in name):
            scored = max(scored, 0.85)
    return scored


def _matching_rooms(directory: tuple[FloorDirectory, ...], address: MeterAddress) -> Iterator[tuple[FloorDirectory, str]]:
    entries = tuple(
        entry for entry in directory if address.matches_building(entry.campus, entry.building, entry.aliases)
    )
    prefixes = set()
    if address.building_number is None:
        buildings = dict.fromkeys((_normalize(entry.campus), _building(entry.building)) for entry in entries)
        prefixes = address.compact_building_prefixes([building for _, building in buildings])
    seen = set()
    for entry in entries:
        if address.floor is not None and _floor(entry.floor) != address.floor:
            continue
        building = _numbered_building(entry.building)
        assert building is not None  # matches_building() requires a numbered building.
        _, prefix = building
        if prefixes and prefix not in prefixes:
            continue
        labels = [_room_label(item.name) for item in entry.rooms]
        room = address.number
        if address.building_number:
            if room not in labels and prefix + room not in labels:
                continue
        else:
            if room not in labels:
                continue
            if room.startswith(prefix):
                actual = room[len(prefix):]
                if actual in labels:
                    if prefix + room in labels:
                        raise MeterLookupError("官方目录无法区分实际房号与照明表编号，请核对房间。")
                    room = actual
                elif prefix + room not in labels:
                    raise MeterLookupError("目录缺少同房的实际房号，无法确认这个编号的表型。")
        key = (_normalize(entry.campus), _building(entry.building), _floor(entry.floor), room)
        if key in seen or labels.count(room) > 1 or labels.count(prefix + room) > 1:
            raise MeterLookupError("官方目录有重复电表项，请核对房间。")
        seen.add(key)
        yield entry, room


def _candidate(campus: str, building: str, room: str, meter: str | None, floor: str | None = None) -> LookupCandidate:
    campus_label = _normalize(campus).removesuffix("校区") + "校区"
    suffix = {"lighting": "照明", "aircon": "空调"}.get(meter, "")
    command = f"{campus_label} {_building(building)} {room}{suffix}"
    if floor is not None:
        command += f" --floor {_floor(floor)}"
    return LookupCandidate(campus, building, room, command, floor, meter)


def suggest_targets(
    buildings: Sequence[tuple[str, str, tuple[str, ...]]], address: MeterAddress | str, *, limit: int = 3,
) -> tuple[LookupCandidate, ...]:
    """按园区相似度给出楼栋建议，房号仍须重查。"""
    if isinstance(address, str):
        try:
            address = parse_meter_text(address)
        except MeterLookupError:
            return ()
    scored = []
    for campus, building, aliases in buildings:
        if address.campus is not None and _normalize(campus).removesuffix("校区") != address.campus:
            continue
        numbered = _numbered_building(building)
        if numbered is None:
            continue
        stem, prefix = numbered
        if address.building_number not in (None, prefix):
            continue
        names = [_area(stem), _area(_normalize(campus)), _normalize(campus).removesuffix("校区"),
                 *(_area(_normalize(alias)) for alias in aliases)]
        score = _similarity(address.place, names)
        room = address.number
        if address.building_number is None and room.startswith(prefix):
            room = room[len(prefix):]
            score += 0.5
        if room:
            scored.append((score, _candidate(campus, building, room, address.meter, address.floor)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return tuple(dict.fromkeys(candidate for _, candidate in scored))[:limit]


def resolve_meter_text(
    directory: tuple[FloorDirectory, ...], address: MeterAddress | str, *, require_pair: bool = False,
) -> tuple[ResolvedMeter, ...]:
    if isinstance(address, str):
        address = parse_meter_text(address)
    matches = list(_matching_rooms(directory, address))
    if not matches:
        raise MeterLookupError("官方目录未收录该园区的这个编号，请更新目录或核对编号。", reason="no_match")
    if len(matches) != 1:
        candidates = tuple(
            _candidate(entry.campus, entry.building, room, address.meter, entry.floor)
            for entry, room in matches[:3]
        )
        raise MeterLookupError("该编号匹配多个目录项，请补校区、楼栋或楼层。", candidates, reason="ambiguous")
    entry, room = matches[0]
    return resolve_meters(
        (entry,), campus=entry.campus, building=entry.building,
        floor=entry.floor, room=room, meter=address.meter, require_pair=require_pair,
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
        raise MeterLookupError("官方目录中未匹配到该校区、楼栋或楼层。", reason="no_match")
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
        raise MeterLookupError("官方目录里没有匹配的房间或电表。", reason="no_match")
    if len(matches) > 1:
        raise MeterLookupError("多个楼层有匹配项，请补楼层。", reason="ambiguous")
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
