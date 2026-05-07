import base64
import datetime as dt
import json
import math
import random
import re
from html import unescape
from typing import Any

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


class HenuLibraryBot:
    # 常用区域映射（可继续补充）；不在映射内时会尝试按当天区域列表模糊匹配
    LOCATIONS = {
        "二楼南附楼走廊": "8",
        "二楼北附楼走廊": "9",
        "二楼大厅走廊": "10",
        "201经管教外文阅览室": "11",
        "202自然科学阅览室": "12",
        "208过刊阅览室": "13",
        "209报纸阅览室": "14",
        "三层南附楼走廊": "15",
        "三层北附楼走廊": "16",
        "三楼北附楼走廊": "16",
        "三楼大厅走廊": "17",
        "读书室": "18",
        "四层南附楼走廊": "22",
        "四楼大厅走廊": "22",
        "四层北附楼走廊": "23",
        "401医学生物数理化书库": "23",
        "501经管教外文书库": "24",
        "502科学技术书库": "25",
        "五层走廊": "26",
        "五楼大厅走廊": "26",
        "601社会科学书库": "27",
        "602文学语言艺术书库": "28",
        "701七层南自习室": "30",
        "702七层北自习室": "31",
        "东馆社会科学阅览室": "38",
        "东馆文学艺术阅览室": "42",
        "东馆素质教育阅览室": "45",
        "103期刊阅览室": "62",
        "104期刊阅览室": "63",
        "109期刊阅览室": "64",
        "金明三楼南走廊": "15",
        "金明三楼北走廊": "16",
        "金明三楼走廊": "17",
        "金明四楼走廊": "22",
        "金明四楼书库": "23",
        "金明五楼走廊": "26",
        "金明七层南自习": "30",
        "金明七层北自习": "31",
        "明伦二层借书": "67",
        "明伦三层现刊": "41",
        "明伦三层报纸": "40",
        "明伦三层借书": "39",
        "明伦四层第一": "43",
        "明伦四层第二": "44",
        "明伦四层第三": "47",
    }

    AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
    API_IV = "ZZWBKJ_ZHIHUAWEI"
    RECORD_TYPE_ALIASES = {
        "1": "1",
        "normal": "1",
        "seat": "1",
        "普通": "1",
        "普通座位": "1",
        "3": "3",
        "study": "3",
        "研习": "3",
        "研习座位": "3",
        "4": "4",
        "exam": "4",
        "考研": "4",
        "考研座位": "4",
    }
    SIGNIN_RECORD_TYPES = {"1", "3", "4"}

    def __init__(self, username: str, password: str, saved_cookies: dict[str, Any] | None = None):
        self.username = str(username).strip()
        self.password = password or ""
        self.base_url = "https://zwyy.henu.edu.cn"
        self.cas_login_url = "https://ids.henu.edu.cn/authserver/login"
        self.token = ""
        self.last_error = ""

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/h5/index.html#/home",
            }
        )

        if saved_cookies:
            cookie_data = dict(saved_cookies)
            self.token = str(cookie_data.pop("_v4_token", "") or "")
            # CASTGC 属于 ids.henu.edu.cn，必须带 domain 才能在 CAS 跳转时正确发送
            castgc_value = cookie_data.pop("CASTGC", None)
            if cookie_data:
                self.session.cookies.update(cookie_data)
            if castgc_value:
                self.session.cookies.set("CASTGC", castgc_value, domain="ids.henu.edu.cn")

        self._set_auth_header()

    def get_cookies(self) -> dict[str, Any]:
        cookies = self.session.cookies.get_dict()
        if self.token:
            cookies["_v4_token"] = self.token
        return cookies

    def _random_string(self, length: int) -> str:
        return "".join(
            self.AES_CHARS[math.floor(random.random() * len(self.AES_CHARS))]
            for _ in range(length)
        )

    def _encrypt_password(self, password: str, salt: str) -> str:
        random_prefix = self._random_string(64)
        iv_str = self._random_string(16)
        text = random_prefix + password
        key_bytes = salt.encode("utf-8")
        iv_bytes = iv_str.encode("utf-8")
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        return base64.b64encode(cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))).decode("utf-8")

    def _api_aes_key(self) -> bytes:
        date_text = dt.datetime.now().strftime("%Y%m%d")
        return f"{date_text}{date_text[::-1]}".encode("utf-8")

    def _encrypt_api_payload(self, data: dict[str, Any]) -> str:
        plain = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cipher = AES.new(self._api_aes_key(), AES.MODE_CBC, self.API_IV.encode("utf-8"))
        encrypted = cipher.encrypt(pad(plain, AES.block_size))
        return base64.b64encode(encrypted).decode("utf-8")

    def _set_auth_header(self) -> None:
        if self.token:
            self.session.headers["authorization"] = f"bearer{self.token}"
        else:
            self.session.headers.pop("authorization", None)

    @staticmethod
    def _resp_msg(resp: dict[str, Any], fallback: str = "未知返回结果") -> str:
        return str(resp.get("message") or resp.get("msg") or fallback)

    @staticmethod
    def _exc_text(exc: Exception) -> str:
        text = str(exc).strip()
        return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__

    @staticmethod
    def _extract_cas_ticket(url: str) -> str:
        if "#/cas/?cas=" in url:
            return url.split("#/cas/?cas=", 1)[1].split("&", 1)[0]
        match = re.search(r"[?&]cas=([^&#]+)", url)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_cas_login_error(html_text: str) -> str:
        text = str(html_text or "")
        if not text:
            return ""

        patterns = [
            r'id="msg"[^>]*>\s*([^<]{1,120})\s*<',
            r'id="showErrorTip"[^>]*>\s*([^<]{1,120})\s*<',
            r'class="errors?"[^>]*>\s*([^<]{1,120})\s*<',
            r'class="authError"[^>]*>\s*([^<]{1,120})\s*<',
            r'"message"\s*:\s*"([^"]{1,120})"',
            r"'message'\s*:\s*'([^']{1,120})'",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return re.sub(r"\s+", " ", unescape(match.group(1))).strip()

        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", unescape(plain)).strip()
        for keyword in (
            "密码错误",
            "账号或密码错误",
            "用户名或密码错误",
            "验证码错误",
            "用户不存在",
            "账户不存在",
            "登录失败",
            "认证失败",
            "访问过于频繁",
            "账号已锁定",
        ):
            if keyword in plain:
                return keyword
        return ""

    def _set_last_error(self, message: str) -> str:
        self.last_error = str(message or "").strip()
        return self.last_error

    def get_last_error(self) -> str:
        return str(self.last_error or "").strip()

    def _login_failed_result(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {"success": False, "msg": self.get_last_error() or "未登录或登录失效"}
        if extra:
            result.update(extra)
        return result

    def _post_json(
        self,
        path: str,
        data: dict[str, Any],
        is_crypto: bool = False,
        allow_reauth: bool = True,
    ) -> dict[str, Any]:
        payload = {"aesjson": self._encrypt_api_payload(data)} if is_crypto else data
        resp = self.session.post(f"{self.base_url}{path}", json=payload, timeout=25)
        result = resp.json()

        if (
            allow_reauth
            and result.get("code") == 10001
            and self.password
            and not path.startswith("/v4/login/")
        ):
            # token 过期时自动重登一次并重试原请求
            if self.login():
                retry_payload = {"aesjson": self._encrypt_api_payload(data)} if is_crypto else data
                retry_resp = self.session.post(f"{self.base_url}{path}", json=retry_payload, timeout=25)
                return retry_resp.json()
        return result

    def _exchange_cas_ticket(self, cas_ticket: str) -> bool:
        if not cas_ticket:
            self._set_last_error("CAS 未返回有效 ticket")
            return False
        try:
            resp = self._post_json("/v4/login/user", {"cas": cas_ticket}, allow_reauth=False)
        except Exception as exc:
            self._set_last_error(f"使用 CAS ticket 换取图书馆 token 失败: {self._exc_text(exc)}")
            return False
        if resp.get("code") != 0:
            self._set_last_error(f"图书馆 token 换取失败: {self._resp_msg(resp, '未知错误')}")
            return False
        token = ((resp.get("data") or {}).get("member") or {}).get("token") or ""
        self.token = str(token)
        self._set_auth_header()
        if not self.token:
            self._set_last_error("图书馆登录成功但未返回 token")
            return False
        self._set_last_error("")
        return True

    def _is_token_valid(self) -> bool:
        if not self.token:
            return False
        try:
            check_day = dt.date.today().strftime("%Y-%m-%d")
            resp = self._post_json("/v4/space/pick", {"date": check_day}, allow_reauth=False)
            code = resp.get("code")
            msg = str(resp.get("message") or resp.get("msg") or "")
            if code == 10001 or "尚未登录" in msg:
                return False
            return True
        except Exception:
            return False

    def login(self) -> bool:
        self._set_last_error("")

        # 1) 先试缓存 token
        if self._is_token_valid():
            self._set_last_error("")
            return True

        self.token = ""
        self._set_auth_header()

        service_url = f"{self.base_url}/v4/login/cas"
        cas_auth_url = f"{self.cas_login_url}?service={service_url}"
        original_content_type = self.session.headers.pop("Content-Type", None)

        try:
            # 2) 先尝试 TGT 免密跳转
            try:
                resp = self.session.get(cas_auth_url, allow_redirects=True, timeout=25)
                cas_ticket = self._extract_cas_ticket(resp.url)
                if cas_ticket and self._exchange_cas_ticket(cas_ticket):
                    return True
            except Exception as exc:
                self._set_last_error(f"访问 CAS 登录入口失败: {self._exc_text(exc)}")

            if not self.password:
                if not self.get_last_error():
                    self._set_last_error("缺少密码，无法执行 CAS 登录")
                return False

            # 3) 密码登录 CAS
            try:
                login_page = self.session.get(cas_auth_url, timeout=25)
            except Exception as exc:
                self._set_last_error(f"获取 CAS 登录页失败: {self._exc_text(exc)}")
                return False

            try:
                execution_match = re.search(r'name="execution" value="(.*?)"', login_page.text)
                salt_match = re.search(r'id="pwdEncryptSalt" value="(.*?)"', login_page.text)
                if not execution_match or not salt_match:
                    page_error = self._extract_cas_login_error(login_page.text)
                    if page_error:
                        self._set_last_error(f"CAS 登录页异常: {page_error}")
                    else:
                        self._set_last_error("CAS 登录页缺少 execution/pwdEncryptSalt 字段，可能页面已改版或被拦截")
                    return False

                form_data = {
                    "username": self.username,
                    "password": self._encrypt_password(self.password, salt_match.group(1)),
                    "captcha": "",
                    "_eventId": "submit",
                    "cllt": "userNameLogin",
                    "dllt": "generalLogin",
                    "lt": "",
                    "execution": execution_match.group(1),
                }

                login_resp = self.session.post(
                    login_page.url,
                    data=form_data,
                    allow_redirects=True,
                    timeout=25,
                )
                cas_ticket = self._extract_cas_ticket(login_resp.url)
                if not cas_ticket:
                    page_error = self._extract_cas_login_error(login_resp.text)
                    if page_error:
                        self._set_last_error(f"CAS 登录失败: {page_error}")
                    elif "authserver/login" in str(login_resp.url):
                        self._set_last_error("CAS 登录未返回 ticket，可能是账号或密码错误，或学校启用了额外校验")
                    else:
                        self._set_last_error("CAS 登录未返回 ticket，无法完成图书馆登录")
                    return False
                return self._exchange_cas_ticket(cas_ticket)
            except Exception as exc:
                self._set_last_error(f"提交 CAS 登录失败: {self._exc_text(exc)}")
                return False
        finally:
            if original_content_type:
                self.session.headers["Content-Type"] = original_content_type

    @staticmethod
    def _to_hhmm(raw_time: Any) -> str:
        if raw_time is None:
            return ""
        text = str(raw_time).strip()
        if not text:
            return ""

        match = re.search(r"(\d{1,2})[:：](\d{1,2})", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"

        match = re.search(r"(\d{1,2})点(?:(\d{1,2})分?)?", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or "0")
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"

        compact = re.sub(r"\D", "", text)
        if len(compact) in (3, 4):
            if len(compact) == 3:
                hour = int(compact[0])
                minute = int(compact[1:])
            else:
                hour = int(compact[:2])
                minute = int(compact[2:])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"

        match = re.search(r"(\d{2}:\d{2})", text)
        return match.group(1) if match else text

    @staticmethod
    def _time_to_minutes(raw_time: Any) -> int | None:
        hhmm = HenuLibraryBot._to_hhmm(raw_time)
        if not hhmm:
            return None
        try:
            hour, minute = hhmm.split(":")
            return int(hour) * 60 + int(minute)
        except Exception:
            return None

    @staticmethod
    def _minutes_to_hhmm(value: int) -> str:
        hour = max(0, value) // 60
        minute = max(0, value) % 60
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _normalize_seat_no(value: Any) -> str:
        text = str(value or "").strip()
        return text.lstrip("0") or "0"

    @staticmethod
    def _normalize_points(points: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(points, dict):
            return {}

        normalized: dict[str, Any] = {}
        for key in ("lat", "lng", "time"):
            value = points.get(key)
            if value in (None, ""):
                continue
            normalized[key] = value

        if ("lat" in normalized or "lng" in normalized) and "time" not in normalized:
            normalized["time"] = int(dt.datetime.now().timestamp())

        return normalized

    @staticmethod
    def _current_record_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "type": str(record.get("type") or ""),
            "area_name": str(record.get("areaName") or record.get("nameMerge") or ""),
            "seat_no": str(record.get("no") or record.get("name") or record.get("spaceName") or ""),
            "show_time": str(record.get("showTime") or record.get("examTime") or ""),
            "status": str(record.get("status") or ""),
            "status_name": str(
                record.get("status_name")
                or record.get("statusname")
                or record.get("status_name_zh")
                or ""
            ),
            "flag_in": str(record.get("flag_in") or ""),
            "flag_leave": str(record.get("flag_leave") or ""),
        }

    @classmethod
    def _resolve_signin_action(cls, record: dict[str, Any]) -> str:
        record_type = str(record.get("type") or "")
        if record_type not in cls.SIGNIN_RECORD_TYPES:
            return ""
        if str(record.get("flag_leave") or "") == "1":
            return "return_signin"
        if str(record.get("flag_in") or "") == "1":
            return "signin"
        return ""

    def _fetch_pick_areas(self, target_date: str) -> list[dict[str, Any]]:
        resp = self._post_json("/v4/space/pick", {"date": target_date})
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "获取区域列表失败"))
        return ((resp.get("data") or {}).get("area") or [])

    def _resolve_area(self, location_name: str, target_date: str) -> tuple[str, str]:
        location = str(location_name or "").strip()
        if not location:
            raise RuntimeError("区域名称不能为空")

        if location.isdigit():
            return location, location

        if location in self.LOCATIONS:
            return str(self.LOCATIONS[location]), location

        areas = self._fetch_pick_areas(target_date)

        for area in areas:
            if location == str(area.get("name", "")).strip():
                return str(area.get("id")), str(area.get("name"))

        for area in areas:
            area_name = str(area.get("name", "")).strip()
            if location and (location in area_name or area_name in location):
                return str(area.get("id")), area_name

        raise RuntimeError(f"区域 '{location}' 未找到，请检查名称")

    def _get_space_map(self, area_id: str) -> dict[str, Any]:
        resp = self._post_json("/v4/Space/map", {"id": str(area_id)})
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "获取区域详情失败"))
        data = resp.get("data") or {}
        if not data:
            raise RuntimeError("区域详情为空")
        return data

    @staticmethod
    def _pick_date_row(date_list: list[dict[str, Any]], target_date: str) -> dict[str, Any] | None:
        for row in date_list:
            if str(row.get("day")) == target_date:
                return row
        for row in date_list:
            day = str(row.get("day") or "")
            if day and day >= target_date:
                return row
        return date_list[0] if date_list else None

    def _get_study_period(self, area_id: str, target_date: str) -> dict[str, Any]:
        resp = self._post_json("/v4/member/checkStudyOpenTime", {"area": str(area_id)})
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "获取可预约周期失败"))
        periods = resp.get("data") or []
        if not periods:
            raise RuntimeError("可预约周期为空")
        for item in periods:
            start_day = str(item.get("startDay") or "")
            end_day = str(item.get("endDay") or "")
            if start_day and end_day and start_day <= target_date <= end_day:
                return item
        return periods[0]

    def _build_reservation_plan(
        self,
        area_id: str,
        space_map: dict[str, Any],
        target_date: str,
        preferred_time: str | None = None,
    ) -> dict[str, Any]:
        space_type = str(space_map.get("type") or "")
        label_ids: list[Any] = []

        if space_type != "1":
            period = self._get_study_period(area_id, target_date)
            begdate = str(period.get("startDay") or "")
            enddate = str(period.get("endDay") or "")
            if not begdate or not enddate:
                raise RuntimeError("学习周期日期无效")
            return {
                "seat_query": {
                    "id": str(area_id),
                    "day": "",
                    "label_id": label_ids,
                    "start_time": "",
                    "end_time": "",
                    "begdate": begdate,
                    "enddate": enddate,
                },
                "confirm_path": "/v4/space/studyConfirm",
                "confirm_payload": {
                    "begdate": begdate,
                    "enddate": enddate,
                },
                "confirm_crypto": True,
            }

        date_cfg = space_map.get("date") or {}
        reserve_type = str(date_cfg.get("reserveType") or "")
        date_list = date_cfg.get("list") or []
        date_row = self._pick_date_row(date_list, target_date)
        if not date_row:
            raise RuntimeError(f"区域未返回 {target_date} 的开放时间")

        day = str(date_row.get("day") or target_date)
        preferred_hhmm = self._to_hhmm(preferred_time or "")
        preferred_min = self._time_to_minutes(preferred_hhmm) if preferred_hhmm else None
        seat_query = {
            "id": str(area_id),
            "day": day,
            "label_id": label_ids,
            "start_time": "",
            "end_time": "",
            "begdate": "",
            "enddate": "",
        }
        confirm_payload = {
            "segment": "",
            "day": day,
            "start_time": "",
            "end_time": "",
        }

        if reserve_type == "1":
            times = date_row.get("times") or []
            if not times:
                raise RuntimeError(f"{day} 未返回可预约时段")
            active_slots = [item for item in times if str(item.get("status", "1")) == "1"] or times
            first_slot = active_slots[0]
            if preferred_min is not None:
                slot_rows: list[tuple[int, int, dict[str, Any]]] = []
                for item in active_slots:
                    start_min = self._time_to_minutes(item.get("start"))
                    end_min = self._time_to_minutes(item.get("end"))
                    if start_min is None or end_min is None:
                        continue
                    slot_rows.append((start_min, end_min, item))
                if slot_rows:
                    matched = None
                    for start_min, end_min, item in slot_rows:
                        if start_min <= preferred_min <= end_min:
                            matched = item
                            break
                    if matched is None:
                        later = [item for start_min, _, item in slot_rows if start_min >= preferred_min]
                        if later:
                            matched = later[0]
                        else:
                            matched = slot_rows[-1][2]
                    first_slot = matched
            seat_query["start_time"] = self._to_hhmm(first_slot.get("start"))
            seat_query["end_time"] = self._to_hhmm(first_slot.get("end"))
            confirm_payload["segment"] = str(first_slot.get("id") or "")
            if not confirm_payload["segment"]:
                raise RuntimeError("预约时段参数缺失(segment)")
        elif reserve_type == "2":
            times = date_row.get("times") or []
            if not times:
                raise RuntimeError(f"{day} 未返回可预约时点")
            time_value = times[0]
            if preferred_min is not None:
                points: list[tuple[int, Any]] = []
                for item in times:
                    if isinstance(item, dict):
                        compare_hhmm = self._to_hhmm(item.get("time") or item.get("start") or item.get("end"))
                    else:
                        compare_hhmm = self._to_hhmm(item)
                    point_min = self._time_to_minutes(compare_hhmm)
                    if point_min is None:
                        continue
                    points.append((point_min, item))
                if points:
                    points.sort(key=lambda x: x[0])
                    exact = [item for point_min, item in points if point_min == preferred_min]
                    if exact:
                        time_value = exact[0]
                    else:
                        later = [item for point_min, item in points if point_min >= preferred_min]
                        time_value = later[0] if later else points[-1][1]
            if isinstance(time_value, dict):
                time_value = time_value.get("time") or time_value.get("start") or time_value.get("end") or ""
            hhmm = self._to_hhmm(time_value)
            if not hhmm:
                raise RuntimeError("时点预约参数缺失")
            seat_query["start_time"] = hhmm
            seat_query["end_time"] = hhmm
            confirm_payload["end_time"] = hhmm
        elif reserve_type == "3":
            start_time = self._to_hhmm(date_row.get("def_start_time") or date_row.get("start_time"))
            end_time = self._to_hhmm(date_row.get("def_end_time") or date_row.get("end_time"))
            if not start_time or not end_time:
                raise RuntimeError("预约时间参数缺失")
            if preferred_min is not None:
                start_min = self._time_to_minutes(start_time)
                end_min = self._time_to_minutes(end_time)
                if start_min is not None and end_min is not None:
                    if preferred_min < start_min or preferred_min >= end_min:
                        raise RuntimeError(
                            f"期望时间 {preferred_hhmm} 不在可预约区间 {start_time}-{end_time}"
                        )
                    start_time = self._minutes_to_hhmm(preferred_min)
            seat_query["start_time"] = start_time
            seat_query["end_time"] = end_time
            confirm_payload["start_time"] = start_time
            confirm_payload["end_time"] = end_time
        else:
            # 兜底：优先取 times[0]，否则取默认时间
            times = date_row.get("times") or []
            if times and isinstance(times[0], dict):
                seat_query["start_time"] = self._to_hhmm(times[0].get("start"))
                seat_query["end_time"] = self._to_hhmm(times[0].get("end"))
                confirm_payload["segment"] = str(times[0].get("id") or "")
            if not seat_query["start_time"]:
                seat_query["start_time"] = self._to_hhmm(date_row.get("def_start_time") or date_row.get("start_time"))
            if not seat_query["end_time"]:
                seat_query["end_time"] = self._to_hhmm(date_row.get("def_end_time") or date_row.get("end_time"))
            if not confirm_payload["segment"]:
                confirm_payload["start_time"] = seat_query["start_time"]
                confirm_payload["end_time"] = seat_query["end_time"]

        return {
            "seat_query": seat_query,
            "confirm_path": "/v4/space/confirm",
            "confirm_payload": confirm_payload,
            "confirm_crypto": True,
            "reserve_type": reserve_type,
            "space_type": space_type,
            "preferred_time": preferred_hhmm,
        }

    def _query_seats(self, seat_query_payload: dict[str, Any]) -> list[dict[str, Any]]:
        resp = self._post_json("/v4/Space/seat", seat_query_payload)
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "查询座位失败"))
        return ((resp.get("data") or {}).get("list") or [])

    def _find_target_seat(self, seats: list[dict[str, Any]], seat_no: str) -> dict[str, Any] | None:
        target_raw = str(seat_no).strip()
        target_norm = self._normalize_seat_no(target_raw)
        for seat in seats:
            values = [seat.get("no"), seat.get("name")]
            for raw in values:
                text = str(raw or "").strip()
                if not text:
                    continue
                if text == target_raw or self._normalize_seat_no(text) == target_norm:
                    return seat
        return None

    @classmethod
    def _normalize_record_type(cls, record_type: str | int | None) -> str:
        key = str(record_type or "1").strip().lower()
        return cls.RECORD_TYPE_ALIASES.get(key, "1")

    def list_seat_records(
        self,
        record_type: str | int = "1",
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"records": []})

        page_value = max(1, int(page))
        limit_value = max(1, min(100, int(limit)))
        type_value = self._normalize_record_type(record_type)

        try:
            resp = self._post_json(
                "/v4/member/seat",
                {
                    "type": type_value,
                    "page": page_value,
                    "limit": limit_value,
                },
            )
            if resp.get("code") != 0:
                return {
                    "success": False,
                    "msg": self._resp_msg(resp, "查询预约记录失败"),
                    "record_type": type_value,
                    "records": [],
                }
            data = resp.get("data") or {}
            records = data.get("data") or []
            total = data.get("total")
            if total is None:
                total = len(records)
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "record_type": type_value,
                "page": page_value,
                "limit": limit_value,
                "total": int(total),
                "records": records,
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询预约记录异常: {exc}", "records": []}

    def list_current_appointments(self) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"appointments": []})

        try:
            resp = self._post_json("/v4/index/subscribe", {})
            if resp.get("code") != 0:
                return {
                    "success": False,
                    "msg": self._resp_msg(resp, "查询当前预约失败"),
                    "appointments": [],
                }

            appointments = resp.get("data") or []
            if not isinstance(appointments, list):
                appointments = []

            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "appointments": appointments,
                "total": len(appointments),
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询当前预约异常: {exc}", "appointments": []}

    def sign_in_current_record(
        self,
        record: dict[str, Any],
        points: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_type = str(record.get("type") or "")
        record_id = str(record.get("id") or "").strip()
        if record_type not in self.SIGNIN_RECORD_TYPES:
            return {"success": False, "msg": f"当前记录类型不支持签到: {record_type or '未知'}"}
        if not record_id:
            return {"success": False, "msg": "当前记录缺少 id，无法签到"}

        action = self._resolve_signin_action(record)
        if not action:
            return {"success": False, "msg": "当前记录不处于可签到状态"}

        sign_path = "/v4/space/signin" if record_type == "1" else "/v4/space/studySign"
        payload = {
            "id": record_id,
            "points": self._normalize_points(points),
        }
        if record_type != "1":
            payload = {
                "seat_id": record_id,
                "points": self._normalize_points(points),
            }

        try:
            resp = self._post_json(sign_path, payload, is_crypto=True)
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "签到失败"),
                "code": resp.get("code"),
                "action": action,
                "record_id": record_id,
                "record_type": record_type,
                "sign_path": sign_path,
                "record": self._current_record_summary(record),
            }
        except Exception as exc:
            return {"success": False, "msg": f"签到异常: {exc}"}

    def auto_sign_in(
        self,
        record_id: str = "",
        points: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.list_current_appointments()
        if not current.get("success"):
            return {
                "success": False,
                "msg": current.get("msg", "查询当前预约失败"),
                "appointments": current.get("appointments", []),
            }

        appointments = current.get("appointments") or []
        summaries = [self._current_record_summary(item) for item in appointments if isinstance(item, dict)]

        candidates: list[dict[str, Any]] = []
        for item in appointments:
            if not isinstance(item, dict):
                continue
            if record_id and str(item.get("id") or "").strip() != str(record_id).strip():
                continue
            if self._resolve_signin_action(item):
                candidates.append(item)

        if not candidates:
            target_text = f"记录 {record_id}" if str(record_id or "").strip() else "当前预约"
            return {
                "success": False,
                "msg": f"{target_text} 中没有可签到的座位预约",
                "appointments": summaries,
            }

        candidates.sort(
            key=lambda item: 0 if self._resolve_signin_action(item) == "return_signin" else 1
        )
        result = self.sign_in_current_record(candidates[0], points=points)
        result["appointments"] = summaries
        result["candidate_count"] = len(candidates)
        return result

    @staticmethod
    def _seminar_pick_day_row(rows: list[dict[str, Any]], target_date: str = "") -> dict[str, Any] | None:
        date_text = str(target_date or "").strip()
        if date_text:
            for row in rows:
                if str(row.get("date") or "") == date_text:
                    return row
        return rows[0] if rows else None

    @staticmethod
    def _seminar_clean_member_ids(member_ids: list[Any], self_id: str = "") -> list[str]:
        current_id = str(self_id or "").strip()
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in member_ids:
            text = str(raw or "").strip()
            if not text or text == current_id or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return cleaned

    @staticmethod
    def _seminar_content_length(content: str) -> int:
        return len(re.sub(r"\s+", "", str(content or "")))

    @staticmethod
    def _seminar_record_summary(record: dict[str, Any]) -> dict[str, Any]:
        room_name = (
            record.get("nameMerge")
            or record.get("name")
            or record.get("area")
            or record.get("areaName")
            or ""
        )
        return {
            "id": str(record.get("id") or ""),
            "area_id": str(record.get("area_id") or record.get("areaId") or ""),
            "room_name": str(room_name or ""),
            "title": str(record.get("title") or ""),
            "status": str(record.get("status") or ""),
            "status_name": str(record.get("status_name") or record.get("statusName") or ""),
            "show_time": str(record.get("show_time") or record.get("showTime") or ""),
            "begin_time": str(record.get("begin_time") or record.get("beginTime") or ""),
            "end_time": str(record.get("end_time") or record.get("endTime") or ""),
            "owner": str(record.get("owner") or ""),
            "member_name": str(record.get("member_name") or record.get("memberName") or ""),
            "member_id": str(record.get("member_id") or record.get("memberId") or ""),
        }

    def seminar_sift_dates(self) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"dates": []})

        try:
            resp = self._post_json("/v4/seminar/siftdate", {})
            dates = resp.get("data") or []
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室日期失败"), "dates": []}
            return {"success": True, "msg": self._resp_msg(resp, "操作成功"), "dates": dates}
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室日期异常: {exc}", "dates": []}

    def seminar_filter_options(self) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"filters": {}})

        try:
            resp = self._post_json("/v4/seminar/sift", {})
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室筛选项失败"), "filters": {}}
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "filters": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室筛选项异常: {exc}", "filters": {}}

    def seminar_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"rooms": []})

        try:
            resp = self._post_json("/v4/seminar/list", payload)
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室列表失败"), "rooms": []}
            data = resp.get("data") or {}
            rooms = data.get("data") or []
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "rooms": rooms,
                "total": int(data.get("total") or len(rooms)),
                "page": int(payload.get("page") or 1),
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室列表异常: {exc}", "rooms": []}

    def seminar_detail(self, area_id: str) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        area_text = str(area_id or "").strip()
        if not area_text:
            return {"success": False, "msg": "area_id 不能为空"}

        try:
            resp = self._post_json("/v4/seminar/detail", {"id": area_text})
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室详情失败")}
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "detail": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室详情异常: {exc}"}

    def seminar_apply_info(self, area_id: str, day: str = "") -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        area_text = str(area_id or "").strip()
        if not area_text:
            return {"success": False, "msg": "area_id 不能为空"}

        query_day = str(day or dt.date.today().strftime("%Y-%m-%d")).strip()

        try:
            resp = self._post_json("/v4/seminar/seminar", {"id": area_text, "day": query_day})
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室预约信息失败")}
            data = resp.get("data") or {}
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "apply_info": data,
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室预约信息异常: {exc}"}

    def seminar_validate_member(
        self,
        area_id: str,
        member_id: str,
        begin_time: str,
        finish_time: str,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        payload = {
            "area_id": str(area_id or "").strip(),
            "member_id": str(member_id or "").strip(),
            "begin_time": str(begin_time or "").strip(),
            "finish_time": str(finish_time or "").strip(),
        }
        if not all(payload.values()):
            return {"success": False, "msg": "成员校验参数不完整"}

        try:
            resp = self._post_json("/v4/seminar/members", payload)
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "成员校验失败"),
                "code": resp.get("code"),
                "member": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"成员校验异常: {exc}"}

    def reserve_seminar_room(
        self,
        area_id: str,
        target_date: str = "",
        start_time: str = "",
        end_time: str = "",
        end_date: str = "",
        title: str = "",
        title_id: str = "",
        content: str = "",
        mobile: str = "",
        member_ids: list[Any] | None = None,
        self_id: str = "",
        is_open: int = 0,
        cate_id: str = "",
        time_ranges: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        area_text = str(area_id or "").strip()
        if not area_text:
            return {"success": False, "msg": "area_id 不能为空"}

        content_text = str(content or "").strip()
        if self._seminar_content_length(content_text) <= 10:
            return {"success": False, "msg": "申请内容必须多于 10 个字"}

        mobile_text = str(mobile or "").strip()
        if not re.fullmatch(r"\d{11}", mobile_text):
            return {"success": False, "msg": "mobile 必须为 11 位手机号"}

        apply_info_result = self.seminar_apply_info(area_text, day=str(target_date or ""))
        if not apply_info_result.get("success"):
            return apply_info_result

        apply_info = apply_info_result.get("apply_info") or {}
        detail = apply_info.get("detail") or {}
        axis = apply_info.get("axis") or {}
        date_rows = axis.get("list") or []
        selected_row = self._seminar_pick_day_row(date_rows, target_date=str(target_date or ""))
        if not selected_row:
            return {"success": False, "msg": "未找到可预约日期"}

        start_date_text = str(selected_row.get("date") or target_date or "").strip()
        end_date_text = str(end_date or start_date_text).strip()
        if not start_date_text:
            return {"success": False, "msg": "无法确定预约日期"}

        detail_min_person = int(detail.get("minPerson") or 4 or 4)
        detail_max_person = int(detail.get("maxPerson") or 10 or 10)
        cleaned_members = self._seminar_clean_member_ids(member_ids or [], self_id=self_id)
        total_people = len(cleaned_members) + 1
        if total_people < detail_min_person:
            return {
                "success": False,
                "msg": f"研讨室预约至少需要 {detail_min_person} 人，当前仅 {total_people} 人",
            }
        if total_people > detail_max_person:
            return {
                "success": False,
                "msg": f"研讨室预约最多 {detail_max_person} 人，当前共 {total_people} 人",
            }

        readonly_title = str(detail.get("readonlyTitle") or "")
        title_text = str(title or "").strip()
        title_id_text = str(title_id or "").strip()
        if readonly_title == "1":
            if not title_id_text:
                return {
                    "success": False,
                    "msg": "该研讨室要求从预设主题中选择 title_id",
                    "titles": detail.get("titles") or [],
                }
        elif not title_text:
            return {"success": False, "msg": "title 不能为空"}

        earlier_periods = str(detail.get("earlierPeriods") or "")
        submit_times = list(time_ranges or [])
        if not submit_times:
            start_hhmm = self._to_hhmm(start_time or "")
            end_hhmm = self._to_hhmm(end_time or "")
            if not start_hhmm or not end_hhmm:
                return {"success": False, "msg": "start_time/end_time 不能为空"}
            if self._time_to_minutes(start_hhmm) is None or self._time_to_minutes(end_hhmm) is None:
                return {"success": False, "msg": "start_time/end_time 格式必须为 HH:MM"}
            if self._time_to_minutes(start_hhmm) >= self._time_to_minutes(end_hhmm):
                return {"success": False, "msg": "start_time 必须早于 end_time"}
            submit_times = [{"start_time": start_hhmm, "end_time": end_hhmm}]

        if earlier_periods == "3" and not str(cate_id or "").strip():
            categories = axis.get("category") or []
            if categories:
                cate_id = str(categories[0].get("id") or "")
            if not str(cate_id or "").strip():
                return {"success": False, "msg": "该研讨室预约需要 cate_id"}

        first_begin = f"{start_date_text} {submit_times[0].get('start_time', '')}".strip()
        first_finish = f"{end_date_text if len(submit_times) == 1 else start_date_text} {submit_times[0].get('end_time', '')}".strip()
        validated_members: list[dict[str, Any]] = []
        for member_id in cleaned_members:
            checked = self.seminar_validate_member(
                area_id=area_text,
                member_id=member_id,
                begin_time=first_begin,
                finish_time=first_finish,
            )
            if not checked.get("success"):
                return {
                    "success": False,
                    "msg": f"成员 {member_id} 校验失败: {checked.get('msg', '')}",
                    "member_id": member_id,
                }
            validated_members.append(checked.get("member") or {})

        payload = {
            "area_id": area_text,
            "start_date": start_date_text,
            "end_date": end_date_text,
            "title": title_text,
            "title_id": title_id_text,
            "content": content_text,
            "open": int(is_open or 0),
            "team": ",".join(cleaned_members),
            "mobile": mobile_text,
            "time": submit_times,
            "file": list(files or []),
        }
        if str(cate_id or "").strip():
            payload["cate_id"] = str(cate_id).strip()

        submit_path = "/v4/seminar/confirm" if earlier_periods == "0" else "/v4/seminar/submit"
        try:
            resp = self._post_json(submit_path, payload)
            response_data = resp.get("data") or {}
            record_id = ""
            if isinstance(response_data, dict):
                for key in ("id", "record_id", "recordId", "book_id", "bookId", "apply_id", "applyId"):
                    value = str(response_data.get(key) or "").strip()
                    if value:
                        record_id = value
                        break
            room_name = str(
                detail.get("name")
                or detail.get("enname")
                or detail.get("title")
                or apply_info.get("name")
                or ""
            ).strip()
            record_type = str(detail.get("type_id") or detail.get("typeId") or "1").strip() or "1"
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "研讨室预约失败"),
                "code": resp.get("code"),
                "submit_path": submit_path,
                "record_id": record_id,
                "record_type": record_type if record_type in {"1", "2"} else "1",
                "room_name": room_name,
                "payload_summary": {
                    "area_id": area_text,
                    "start_date": start_date_text,
                    "end_date": end_date_text,
                    "time": submit_times,
                    "team_count": len(cleaned_members),
                    "total_people": total_people,
                    "cate_id": str(cate_id or ""),
                },
                "detail_summary": {
                    "room_name": room_name,
                    "type_id": str(detail.get("type_id") or detail.get("typeId") or ""),
                    "min_person": detail.get("minPerson"),
                    "max_person": detail.get("maxPerson"),
                },
                "validated_members": validated_members,
                "data": response_data,
            }
        except Exception as exc:
            return {"success": False, "msg": f"研讨室预约异常: {exc}"}

    def list_seminar_records(
        self,
        record_type: str | int = "1",
        page: int = 1,
        limit: int = 20,
        mode: str = "books",
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"records": []})

        type_value = str(record_type or "1").strip() or "1"
        if type_value not in {"1", "2"}:
            return {"success": False, "msg": "record_type 仅支持 1(普通空间) 或 2(大型空间)", "records": []}

        page_value = max(1, int(page))
        limit_value = max(1, min(100, int(limit)))
        mode_text = str(mode or "books").strip().lower()
        list_path = "/v4/seminar/books" if mode_text != "reneges" else "/v4/seminar/reneges"

        payload = {
            "type": type_value,
            "page": page_value,
            "limit": limit_value,
        }

        try:
            resp = self._post_json(list_path, payload)
            if resp.get("code") != 0:
                return {
                    "success": False,
                    "msg": self._resp_msg(resp, "查询研讨室预约记录失败"),
                    "record_type": type_value,
                    "mode": mode_text,
                    "records": [],
                }
            data = resp.get("data") or {}
            records = data.get("data") or []
            total = data.get("total")
            if total is None:
                total = len(records)
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "record_type": type_value,
                "mode": mode_text,
                "page": page_value,
                "limit": limit_value,
                "total": int(total),
                "records": records,
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室预约记录异常: {exc}", "records": []}

    def cancel_seminar_record(
        self,
        record_id: str | int,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_id_text = str(record_id or "").strip()
        if not record_id_text:
            return {"success": False, "msg": "record_id 不能为空"}

        cancel_path = "/v4/seminar/cancel"

        try:
            resp = self._post_json(cancel_path, {"id": record_id_text})
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp),
                "code": resp.get("code"),
                "record_id": record_id_text,
                "cancel_path": cancel_path,
            }
        except Exception as exc:
            return {"success": False, "msg": f"取消研讨室预约异常: {exc}"}

    def sign_in_seminar_record(
        self,
        record_id: str | int,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_id_text = str(record_id or "").strip()
        if not record_id_text:
            return {"success": False, "msg": "record_id 不能为空"}

        sign_path = "/v4/seminar/signin"

        try:
            resp = self._post_json(sign_path, {"id": record_id_text})
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "研讨室签到失败"),
                "code": resp.get("code"),
                "record_id": record_id_text,
                "sign_path": sign_path,
                "data": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"研讨室签到异常: {exc}"}

    def cancel_seat_record(
        self,
        record_id: str | int,
        record_type: str | int = "1",
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_id_text = str(record_id or "").strip()
        if not record_id_text:
            return {"success": False, "msg": "record_id 不能为空"}

        type_value = self._normalize_record_type(record_type)
        cancel_path = "/v4/space/cancel" if type_value == "1" else "/v4/space/studyCancel"

        try:
            resp = self._post_json(cancel_path, {"id": record_id_text})
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp),
                "code": resp.get("code"),
                "record_id": record_id_text,
                "record_type": type_value,
                "cancel_path": cancel_path,
            }
        except Exception as exc:
            return {"success": False, "msg": f"取消预约异常: {exc}"}

    def reserve(
        self,
        location_name: str,
        seat_no: str,
        target_date: str,
        preferred_time: str | None = None,
    ) -> dict[str, Any]:
        try:
            dt.date.fromisoformat(target_date)
        except ValueError:
            return {"success": False, "msg": "target_date 格式必须为 YYYY-MM-DD"}

        # 避免使用过期 token 直接进入预约流程
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        try:
            area_id, area_name = self._resolve_area(location_name, target_date)
            space_map = self._get_space_map(area_id)
            plan = self._build_reservation_plan(area_id, space_map, target_date, preferred_time=preferred_time)
            seats = self._query_seats(plan["seat_query"])
            if not seats:
                return {"success": False, "msg": f"区域 {area_name} 在 {target_date} 没有可查询座位"}

            target_seat = self._find_target_seat(seats, seat_no)
            if not target_seat:
                return {"success": False, "msg": f"在区域 {area_name} 未找到座位号: {seat_no}"}

            if str(target_seat.get("status")) != "1":
                return {
                    "success": False,
                    "msg": f"座位 {target_seat.get('no') or seat_no} 当前不可预约",
                }

            confirm_payload = dict(plan["confirm_payload"])
            confirm_payload["seat_id"] = str(target_seat.get("id"))
            confirm_resp = self._post_json(
                plan["confirm_path"],
                confirm_payload,
                is_crypto=bool(plan.get("confirm_crypto")),
            )
            success = confirm_resp.get("code") == 0
            return {
                "success": success,
                "msg": self._resp_msg(confirm_resp),
                "applied_time": {
                    "preferred_time": plan.get("preferred_time", ""),
                    "start_time": (plan.get("seat_query") or {}).get("start_time", ""),
                    "end_time": (plan.get("seat_query") or {}).get("end_time", ""),
                    "reserve_type": plan.get("reserve_type", ""),
                    "space_type": plan.get("space_type", ""),
                },
            }
        except Exception as exc:
            return {"success": False, "msg": f"预约流程异常: {exc}"}
