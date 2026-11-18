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


@dataclass(frozen=True)
class ResolvedMeter:
    kind: Literal["aircon", "lighting"]
    floor: str
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


def resolve_meters(
    directory: tuple[FloorDirectory, ...], *, campus: str, building: str, room: str,
    meter: Literal["aircon", "lighting", "both"] | None = None, floor: str | None = None,
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
    building_numbers = re.findall(r"[0-9]+", _building(floors[0].building))
    if len(building_numbers) != 1 or not _building(floors[0].building).endswith(building_numbers[0]):
        raise MeterLookupError("官方楼栋没有唯一楼号，无法按分表规则匹配。")
    prefix = building_numbers[0]
    names = {"aircon": room_number, "lighting": prefix + room_number}

    matches = []
    for entry in floors:
        labels = [_normalize(item.name).removesuffix("房间") for item in entry.rooms]
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
                options[kind] = ResolvedMeter(kind, entry.floor, option.name, option.id)
        if options:
            matches.append(options)

    if not matches:
        raise MeterLookupError("官方目录里没有匹配的房间或电表。")
    if len(matches) > 1:
        raise MeterLookupError("多个楼层有匹配项，请补楼层。")
    options = matches[0]
    if len({item.roomverify for item in options.values()}) != len(options):
        raise MeterLookupError("空调和照明指向同一目录 ID，请核对表型。")
    if meter is None:
        if len(options) > 1:
            raise MeterLookupError("查空调电、照明电，还是两项都查？")
        return tuple(options.values())
    if meter == "both":
        if len(options) != 2:
            raise MeterLookupError("目录未同时提供空调表和照明表。")
        return tuple(options.values())
    if meter not in options:
        raise MeterLookupError("官方目录未找到指定类型的电表。")
    return (options[meter],)
