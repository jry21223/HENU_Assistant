"""中文别名归一化。

将用户输入的各种中文表述归一化为标准格式，用于资源搜索和解析。

规则：
- 全角字符转半角
- 中文数字转阿拉伯数字
- 校区简称归一（明伦→明伦校区、金明→金明校区、郑州→郑州校区）
- 教学楼别称归一（十号楼/10号楼/第十教学楼）
- 大小写不敏感（A区/a区）
"""

from __future__ import annotations

import re

# ── 全角→半角映射 ─────────────────────────────────────────

_FULLWIDTH_MAP = str.maketrans(
    "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ，。！？（）【】",
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ,.!?()[]",
)

# ── 中文数字→阿拉伯 ───────────────────────────────────────

_CN_DIGIT_MAP = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100,
}

# 中文数字词组 → 数字
_CN_NUMBER_WORDS: dict[str, str] = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
    "零": "0",
    "十一": "11", "十二": "12", "十三": "13", "十四": "14", "十五": "15",
    "十六": "16", "十七": "17", "十八": "18", "十九": "19", "二十": "20",
}


def _parse_cn_number(text: str) -> int | None:
    """解析中文数字为整数。支持"一百二十三"、"十"、"二十三"等。"""
    text = text.strip()
    if not text:
        return None

    # 快捷映射
    if text in _CN_NUMBER_WORDS:
        return int(_CN_NUMBER_WORDS[text])

    total = 0
    section = 0
    for ch in text:
        if ch not in _CN_DIGIT_MAP:
            return None
        val = _CN_DIGIT_MAP[ch]
        if val >= 10:
            section = (section or 1) * val
            total += section
            section = 0
        else:
            section = val
    total += section
    return total if total > 0 else None


# 中文数字正则（匹配"一百二十三"、"十二"、"五"等）
_CN_NUMBER_RE = re.compile(
    r"[零一二三四五六七八九十百]+"
)

# ── 校区归一化 ────────────────────────────────────────────

_CAMPUS_ALIASES: dict[str, str] = {
    "明伦": "明伦校区",
    "明伦校区": "明伦校区",
    "明伦校": "明伦校区",
    "ml": "明伦校区",
    "minglun": "明伦校区",
    "金明": "金明校区",
    "金明校区": "金明校区",
    "金明校": "金明校区",
    "jm": "金明校区",
    "jinming": "金明校区",
    "郑州": "郑州校区",
    "郑州校区": "郑州校区",
    "郑州校": "郑州校区",
    "zz": "郑州校区",
    "zhengzhou": "郑州校区",
}

# ── 楼房别名规则 ──────────────────────────────────────────

# 教学楼后缀归一
_BUILDING_SUFFIX_RE = re.compile(r"(教学楼|楼|教|栋)$")

# 楼房特殊别名
_BUILDING_SPECIAL: dict[str, str] = {
    "综合楼": "综合教学楼",
    "综合教学楼": "综合教学楼",
    "综教": "综合教学楼",
    "综合": "综合教学楼",
}


def normalize(text: str) -> str:
    """主归一化函数。

    应用所有归一化规则，返回清理后的文本。
    """
    if not text:
        return ""

    # 1. 去首尾空白
    text = text.strip()

    # 2. 全角转半角
    text = text.translate(_FULLWIDTH_MAP)

    # 3. 中文数字 → 阿拉伯（仅独立的数字词）
    text = _replace_cn_numbers(text)

    # 4. 校区归一
    text = _normalize_campus(text)

    # 5. 去多余空格
    text = re.sub(r"\s+", "", text)

    return text


def _replace_cn_numbers(text: str) -> str:
    """将文本中的中文数字词组替换为阿拉伯数字。"""
    def _replacer(m: re.Match) -> str:
        word = m.group(0)
        num = _parse_cn_number(word)
        return str(num) if num is not None else word

    return _CN_NUMBER_RE.sub(_replacer, text)


def _normalize_campus(text: str) -> str:
    """归一化校区名称（仅替换校区部分，保留其余文本）。"""
    sorted_aliases = sorted(_CAMPUS_ALIASES.keys(), key=len, reverse=True)
    for alias in sorted_aliases:
        if alias in text:
            # 只替换校区别名为标准名称，保留其余文本
            return text.replace(alias, _CAMPUS_ALIASES[alias], 1)
    return text


def normalize_campus_name(name: str) -> str:
    """归一化校区名称为标准名称。"""
    return _CAMPUS_ALIASES.get(name.strip(), name.strip())


def normalize_building_name(name: str) -> str:
    """归一化楼房名称。

    "十号楼" → "10号楼"
    "综合教学楼" → "综合教学楼"（不变）
    "综教" → "综合教学楼"
    """
    name = name.strip()
    # 全角转半角
    name = name.translate(_FULLWIDTH_MAP)
    # 中文数字 → 阿拉伯
    name = _replace_cn_numbers(name)
    # 特殊映射
    lower = name.lower()
    for special, target in _BUILDING_SPECIAL.items():
        if lower == special.lower() or lower == normalize(special).lower():
            return target
    return name


def normalize_room_name(name: str) -> str:
    """归一化教室/房间名称。

    "十号楼101" → "10号楼101"
    "A203" → "a203"
    """
    name = name.strip()
    # 全角转半角
    name = name.translate(_FULLWIDTH_MAP)
    # 中文数字 → 阿拉伯
    name = _replace_cn_numbers(name)
    return name


def generate_aliases(
    campus_name: str,
    building_name: str = "",
    room_name: str = "",
) -> list[str]:
    """根据校区/楼房/教室名称生成常用别名列表。"""
    aliases: list[str] = []

    cn_campus = normalize_campus_name(campus_name)
    if cn_campus != campus_name:
        aliases.append(cn_campus)

    if building_name:
        cn_building = normalize_building_name(building_name)
        # 始终包含标准化后的楼房名
        aliases.append(cn_building)

        # 带数字的楼：同时生成缩写
        num_match = re.search(r"(\d+)号楼", cn_building)
        if num_match:
            num = num_match.group(1)
            # 十号楼 → 10号楼
            aliases.append(f"{num}号楼")

        # 去"教学楼"后缀
        short = _BUILDING_SUFFIX_RE.sub("", cn_building)
        if short and short != cn_building:
            aliases.append(short)

        # X号教学楼 → 同时生成 X号楼
        teach_bld_match = re.match(r"(\d+)号教学楼", cn_building)
        if teach_bld_match:
            aliases.append(f"{teach_bld_match.group(1)}号楼")

        if room_name:
            # 楼房+房间组合：明伦校区十号楼101 → [十号楼101, 10号楼101]
            cn_room = normalize_room_name(room_name)
            aliases.append(f"{cn_building}{cn_room}")
            if num_match:
                aliases.append(f"{num}号楼{cn_room}")

    # 去重 + 保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for a in aliases:
        if a not in seen and a.strip():
            seen.add(a)
            unique.append(a.strip())
    return unique
