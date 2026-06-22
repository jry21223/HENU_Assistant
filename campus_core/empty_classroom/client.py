"""空教室上游 API 客户端。

封装河南大学选课系统 (xk.henu.edu.cn) 的空教室相关接口。
依赖已验证登录的 HenuXkClient，复用其 Session 和 Cookie。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests

from .models import Building, Campus, Classroom, RoomType, Term

# ── 上游接口常量 ──────────────────────────────────────────────

_DROPLIST_URL = "/frame/droplist/getDropLists.action"
_CLASSROOM_LIST_URL = "/taglib/CombBoxServlet.jsp"
_SCHEDULE_HTML_URL = "/kbbp/dykb.GS1.jsp"


def _decode_text(resp: requests.Response) -> str:
    """自动检测编码解码响应内容。"""
    content = resp.content or b""
    for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="ignore")


def _parse_droplist_json(text: str) -> list[dict[str, str]]:
    """解析上游 droplist 返回的 JSON。

    上游可能返回纯 JSON 数组，也可能包裹在 HTML 片段中。
    """
    text = text.strip()
    # 尝试直接解析 JSON
    import json as _json

    if text.startswith("["):
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            pass

    # 尝试从 HTML 中提取 JSON
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            return _json.loads(match.group(0))
        except _json.JSONDecodeError:
            pass

    return []


def _parse_classroom_xml(xml_text: str) -> list[dict[str, str]]:
    """解析教室列表 XML 响应。

    上游返回格式：
    <data>
      <info>
        <filtrateCount>1</filtrateCount>
        <value>0000231</value>
        <filtrateInfo0>十号楼101</filtrateInfo0>
        <name>十号楼101[160][多媒体教室]</name>
        <fillName>十号楼101[160][多媒体教室]</fillName>
      </info>
    </data>
    """
    results: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # 尝试修复 GBK 编码的 XML
        try:
            root = ET.fromstring(xml_text.encode("latin-1").decode("gbk"))
        except (ET.ParseError, UnicodeError):
            return results

    for info in root.findall(".//info"):
        value_el = info.find("value")
        name_el = info.find("name")
        filtrate_el = info.find("filtrateInfo0")

        room_id = value_el.text.strip() if value_el is not None and value_el.text else ""
        room_name = filtrate_el.text.strip() if filtrate_el is not None and filtrate_el.text else ""
        name_text = name_el.text.strip() if name_el is not None and name_el.text else ""

        # 从 name 中解析容量和类型
        capacity = 0
        type_name = ""
        cap_match = re.search(r"\[(\d+)\]", name_text)
        if cap_match:
            capacity = int(cap_match.group(1))
        # 第二个方括号是类型
        type_matches = re.findall(r"\[([^\]]+)\]", name_text)
        if len(type_matches) >= 2:
            type_name = type_matches[1]

        if room_id and room_name:
            results.append(
                {
                    "room_id": room_id,
                    "room_name": room_name,
                    "capacity": str(capacity),
                    "type_name": type_name,
                }
            )

    return results


class EmptyClassroomClient:
    """空教室查询上游客户端。

    复用 HenuXkClient 的登录态（Session + Cookies），
    封装教务系统的教室课表相关接口。
    """

    def __init__(self, xk_client: Any):
        """用已验证登录的 HenuXkClient 初始化。

        Args:
            xk_client: HenuXkClient 实例，应已完成 login()。
        """
        self._client = xk_client
        self._session: requests.Session = xk_client.session
        self._base_url: str = xk_client.base_url

    # ── 低级 HTTP 方法 ──────────────────────────────────────

    def _post_form(
        self,
        path: str,
        data: dict[str, str],
        referer: str = "",
    ) -> dict[str, Any]:
        """发送 POST 表单请求，返回解码后的文本和状态。"""
        url = urljoin(self._base_url, path)
        headers: dict[str, str] = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if referer:
            headers["Referer"] = referer
        else:
            headers["Referer"] = f"{self._base_url}/frame/homes.action"

        try:
            resp = self._session.post(url, data=data, headers=headers, timeout=30)
            text = _decode_text(resp)
            return {
                "url": url,
                "final_url": str(resp.url),
                "status_code": resp.status_code,
                "text": text,
                "ok": resp.status_code == 200 and bool(text.strip()),
            }
        except requests.RequestException as exc:
            return {
                "url": url,
                "final_url": "",
                "status_code": 0,
                "text": "",
                "ok": False,
                "error": f"{exc.__class__.__name__}: {exc}",
            }

    def _droplist(
        self,
        combo_box_name: str,
        param_value: str = "",
    ) -> list[dict[str, str]]:
        """调用 droplist 接口获取下拉列表数据。"""
        data = {
            "comboBoxName": combo_box_name,
            "paramValue": param_value,
            "isYXB": "0",
            "isCDDW": "0",
            "isXQ": "0",
            "isDJKSLB": "0",
            "isZY": "0",
        }
        result = self._post_form(_DROPLIST_URL, data)
        if not result["ok"]:
            return []
        return _parse_droplist_json(result["text"])

    # ── 学期 ────────────────────────────────────────────────

    def fetch_terms(self) -> list[Term]:
        """获取学期列表。"""
        items = self._droplist("Ms_KBBP_FBXQLLJXAP")
        terms: list[Term] = []
        for item in items:
            code = item.get("code", "")
            name = item.get("name", "")
            if not code or not name:
                continue
            parts = code.split(",")
            year = parts[0] if len(parts) > 0 else ""
            term_part = parts[1] if len(parts) > 1 else ""
            terms.append(
                Term(
                    term_code=code,
                    term_name=name,
                    year=year,
                    term_part=term_part,
                )
            )
        return terms

    # ── 校区 ────────────────────────────────────────────────

    def fetch_campuses(self) -> list[Campus]:
        """获取校区列表。"""
        items = self._droplist("MsSchoolArea")
        return [
            Campus(campus_code=item.get("code", ""), campus_name=item.get("name", ""))
            for item in items
            if item.get("code") and item.get("name")
        ]

    # ── 楼房 ────────────────────────────────────────────────

    def fetch_buildings(self, campus_code: str) -> list[Building]:
        """获取指定校区下的楼房列表。"""
        param_value = f"ssxq={campus_code}"
        items = self._droplist("MsSchoolArea_LF", param_value)
        return [
            Building(
                building_code=item.get("code", ""),
                building_name=item.get("name", ""),
                campus_code=campus_code,
            )
            for item in items
            if item.get("code") and item.get("name")
        ]

    # ── 教室类型 ────────────────────────────────────────────

    def fetch_room_types(self) -> list[RoomType]:
        """获取教室类型列表。"""
        items = self._droplist("MsCodeset", "DM-JSLX")
        return [
            RoomType(type_code=item.get("code", ""), type_name=item.get("name", ""))
            for item in items
            if item.get("code") and item.get("name")
        ]

    # ── 教室列表 ────────────────────────────────────────────

    def fetch_classrooms(
        self,
        campus_code: str = "",
        building_code: str = "",
        type_code: str = "",
    ) -> list[Classroom]:
        """获取教室列表。

        Args:
            campus_code: 校区代码，空表示全部。
            building_code: 楼房代码，空表示全部。
            type_code: 教室类型代码，空表示全部。
        """
        data = {
            "className": "jxap_combbox_js",
            "loadDataStyle": "loadClass",
            "xq_m": campus_code,
            "jslx_m": type_code,
            "lf_m": building_code,
            "flag": "xkyjs",
        }
        result = self._post_form(_CLASSROOM_LIST_URL, data)
        if not result["ok"]:
            return []

        items = _parse_classroom_xml(result["text"])
        classrooms: list[Classroom] = []
        for item in items:
            classrooms.append(
                Classroom(
                    room_id=item["room_id"],
                    room_name=item["room_name"],
                    campus_code=campus_code,
                    campus_name="",  # 需要外部补充
                    building_code=building_code,
                    building_name="",  # 需要外部补充
                    capacity=int(item["capacity"]) if item["capacity"].isdigit() else 0,
                    type_name=item.get("type_name", ""),
                )
            )
        return classrooms

    # ── 教室课表 HTML ───────────────────────────────────────

    def fetch_schedule_html(
        self,
        term_code: str,
        campus_code: str,
        building_code: str,
        type_code: str = "",
    ) -> str:
        """获取指定学期/校区/楼房的教室课表 HTML。

        Args:
            term_code: 学期代码，如 "2025,1"。
            campus_code: 校区代码，如 "01"。
            building_code: 楼房代码，如 "0013"。
            type_code: 教室类型，空表示全部。

        Returns:
            上游返回的 HTML 文本。空字符串表示请求失败。
        """
        parts = term_code.split(",")
        year = parts[0] if len(parts) > 0 else ""
        term_part = parts[1] if len(parts) > 1 else ""

        # 按 API 文档构建完整 POST body
        form_data = {
            "hidFJBH": "",
            "hidXQ": campus_code,
            "hidLF": building_code,
            "userType": "STU",
            "hidJSLX": type_code,
            "hidSYDW": "",
            "hidCXLX": "flf",
            "hidBfy": "0",
            "hidZZLX": "A4",
            "orientation": "L",
            "xssj": "xssj",
            "xsrq": "xsrq",
            "sfxsym": "xsym",
            "lx": "",
            "xkyjs": "1",
            "xnxq": term_code,
            "xn": year,
            "xn1": "",
            "_xq": "",
            "xq_m": term_part,
            "jslx": type_code,
            "selGS": "1",
            "selXQ": campus_code,
            "selLF": building_code,
            "txt_jsmc": "",
            "skdd": "",
            "selSYDW": "",
            "selJSMC": "",
            "radioa": "on",
            "chkXSDYRQ": "on",
            "chkXSDYSJ": "on",
            "chkXSYM": "on",
            "chkXKYJS": "on",
            "radiob": "A4",
            "radiofx": "hx",
            "chk_week6": "1",
            "chk_week7": "1",
            "menucode_current": "SB04",
        }

        path = f"/kbbp/dykb.GS1.jsp?kblx=jsikb"
        result = self._post_form(
            path,
            form_data,
            referer=f"{self._base_url}/kbbp/dykb.GS1.jsp?kblx=jsikb",
        )

        if not result["ok"]:
            return ""

        return result["text"]

    # ── 便捷方法 ────────────────────────────────────────────

    def get_campus_map(self) -> dict[str, str]:
        """获取校区代码→名称映射。"""
        campuses = self.fetch_campuses()
        return {c.campus_code: c.campus_name for c in campuses}

    def get_building_map(self, campus_code: str) -> dict[str, str]:
        """获取楼房代码→名称映射。"""
        buildings = self.fetch_buildings(campus_code)
        return {b.building_code: b.building_name for b in buildings}
