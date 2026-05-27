from __future__ import annotations

import base64
import hashlib
import math
import random
import re
from html import unescape
from typing import Any
from urllib.parse import urlencode

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from .auth import AuthMixin


class _HebaoClient:
    """河宝社区 API 客户端"""

    # 请假状态映射
    LEAVE_STATUS_MAP = {
        1: "待审批",
        2: "催办中",
        3: "已批准",
        4: "已驳回",
        5: "即将休假",
        6: "休假中",
        7: "销假逾期",
        8: "已完成",
        9: "审批逾期",
        10: "销假中",
    }

    # 请假类型映射
    LEAVE_TYPE_MAP = {
        "事假": "事假",
        "病假": "病假",
        "公假": "公假",
        "婚假": "婚假",
        "产假": "产假",
        "丧假": "丧假",
    }

    def __init__(self, username: str, password: str, saved_token: str | None = None, cas_cookies: dict[str, Any] | None = None):
        self.username = str(username).strip()
        self.password = password or ""
        self.base_url = "https://yzsfz.henu.edu.cn/fapi"
        self.cas_login_url = "https://ids.henu.edu.cn/authserver/login"
        self.cas_return_url = "https://yzsfz.henu.edu.cn/h5/cas"
        self.token = saved_token or ""
        self.last_error = ""

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://yzsfz.henu.edu.cn/h5/home",
            "TENANT-ID": "",
        })

        if self.token:
            self.session.headers["Authorization"] = self.token

        # Load shared CAS cookies (CASTGC) for login reuse
        if cas_cookies:
            for name, value in cas_cookies.items():
                self.session.cookies.set(name, value, domain="ids.henu.edu.cn", path="/")

    def get_token(self) -> str:
        return self.token

    def get_cas_cookies(self) -> dict[str, Any]:
        """Get CAS cookies (CASTGC) for sharing with other systems."""
        cas_cookies = {}
        for cookie in self.session.cookies:
            if cookie.domain == "ids.henu.edu.cn" or cookie.name in {"CASTGC", "TGC"}:
                cas_cookies[cookie.name] = cookie.value
        return cas_cookies

    def _random_string(self, length: int) -> str:
        chars = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
        return "".join(chars[math.floor(random.random() * len(chars))] for _ in range(length))

    def _encrypt_password(self, password: str, salt: str) -> str:
        random_prefix = self._random_string(64)
        iv_str = self._random_string(16)
        text = random_prefix + password
        key_bytes = salt.encode("utf-8")
        iv_bytes = iv_str.encode("utf-8")
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        return base64.b64encode(cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))).decode("utf-8")

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
            "需要验证码",
            "请输入验证码",
            "短信验证",
            "手机验证",
        ):
            if keyword in plain:
                return keyword
        # 检测是否需要验证码（页面有验证码输入框但无明确错误提示）
        if re.search(r'<img[^>]*captcha[^>]*>|<input[^>]*captcha[^>]*>|id=["\']captcha["\']', text, re.I):
            return "需要验证码"
        return ""

    @staticmethod
    def _extract_cas_ticket(url: str) -> str:
        return AuthMixin._extract_cas_ticket(url)

    def _set_last_error(self, message: str) -> str:
        self.last_error = str(message or "").strip()
        return self.last_error

    def get_last_error(self) -> str:
        return str(self.last_error or "").strip()

    def _is_token_valid(self) -> bool:
        if not self.token:
            return False
        try:
            resp = self.session.get(
                f"{self.base_url}/activity-registration/registrationProgress/h5Task",
                timeout=15,
            )
            data = resp.json()
            return data.get("code") == 0 or data.get("code") != -1
        except Exception:
            return False

    def _exchange_cas_ticket(self, cas_ticket: str) -> bool:
        """Use CAS ticket to get authentication token"""
        if not cas_ticket:
            self._set_last_error("CAS 未返回有效 ticket")
            return False

        try:
            # The service expects the ticket as a query parameter
            resp = self.session.get(
                f"{self.cas_return_url}",
                params={"ticket": cas_ticket},
                allow_redirects=True,
                timeout=25,
            )

            # Check if we got a token in the URL or need to extract from response
            # The frontend stores token in localStorage from query params
            if "token=" in resp.url:
                match = re.search(r"[?&]token=([^&#]+)", resp.url)
                if match:
                    self.token = match.group(1)
                    self.session.headers["Authorization"] = self.token
                    self._set_last_error("")
                    return True

            # Check if we're already on a page that indicates successful login
            if "/home" in resp.url or resp.status_code == 200:
                # Try to extract token from response headers or cookies
                token = resp.headers.get("Authorization", "")
                if not token:
                    # Check for token in Set-Cookie or response body
                    for cookie in self.session.cookies:
                        if cookie.name in ("token", "Authorization"):
                            token = cookie.value
                            break

                if token:
                    self.token = token
                    self.session.headers["Authorization"] = self.token
                    self._set_last_error("")
                    return True

            self._set_last_error("CAS ticket 换取 token 失败")
            return False

        except Exception as exc:
            self._set_last_error(f"使用 CAS ticket 换取 token 失败: {exc}")
            return False

    def login(self) -> bool:
        """Login via CAS"""
        self._set_last_error("")

        # 1) Try cached token first
        if self._is_token_valid():
            self._set_last_error("")
            return True

        self.token = ""
        self.session.headers.pop("Authorization", None)

        service_url = self.cas_return_url
        cas_auth_url = f"{self.cas_login_url}?service={service_url}"

        # Save and remove Content-Type for form submission
        original_content_type = self.session.headers.pop("Content-Type", None)

        try:
            # 2) Try TGT auto-redirect first
            try:
                resp = self.session.get(cas_auth_url, allow_redirects=True, timeout=25)
                cas_ticket = self._extract_cas_ticket(resp.url)
                if cas_ticket and self._exchange_cas_ticket(cas_ticket):
                    return True
            except Exception as exc:
                self._set_last_error(f"访问 CAS 登录入口失败: {exc}")

            if not self.password:
                if not self.get_last_error():
                    self._set_last_error("缺少密码，无法执行 CAS 登录")
                return False

            # 3) Password login to CAS
            try:
                login_page = self.session.get(cas_auth_url, timeout=25)
            except Exception as exc:
                self._set_last_error(f"获取 CAS 登录页失败: {exc}")
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
                        # 提供更详细的错误信息，包括最终 URL
                        final_url = str(login_resp.url or "")
                        url_hint = f"，最终 URL: {final_url[:200]}" if final_url else ""
                        self._set_last_error(f"CAS 登录未返回 ticket{url_hint}")
                    return False

                return self._exchange_cas_ticket(cas_ticket)

            except Exception as exc:
                self._set_last_error(f"提交 CAS 登录失败: {exc}")
                return False
        finally:
            if original_content_type:
                self.session.headers["Content-Type"] = original_content_type

    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        allow_reauth: bool = True,
    ) -> dict[str, Any]:
        """Make GET request to API"""
        try:
            resp = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=25,
            )
            result = resp.json()

            # Token expired, try re-login
            if (
                allow_reauth
                and result.get("code") == -1
                and "authentication" in str(result.get("msg", "")).lower()
                and self.password
            ):
                if self.login():
                    retry_resp = self.session.get(
                        f"{self.base_url}{path}",
                        params=params,
                        timeout=25,
                    )
                    return retry_resp.json()

            return result
        except Exception as exc:
            return {"code": -1, "msg": str(exc), "data": None}

    def _post_json(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        allow_reauth: bool = True,
    ) -> dict[str, Any]:
        """Make POST request to API"""
        try:
            resp = self.session.post(
                f"{self.base_url}{path}",
                json=data,
                timeout=25,
            )
            result = resp.json()

            # Token expired, try re-login
            if (
                allow_reauth
                and result.get("code") == -1
                and "authentication" in str(result.get("msg", "")).lower()
                and self.password
            ):
                if self.login():
                    retry_resp = self.session.post(
                        f"{self.base_url}{path}",
                        json=data,
                        timeout=25,
                    )
                    return retry_resp.json()

            return result
        except Exception as exc:
            return {"code": -1, "msg": str(exc), "data": None}

    # ==================== Leave Management ====================

    def get_leave_list(
        self,
        student_no: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get leave request list for a student"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
                "records": [],
            }

        params = {
            "studentNo": student_no or self.username,
            "pageNo": page,
            "pageSize": page_size,
        }

        result = self._get_json("/studentleave/studentLeaveInfo/approveLeaveInfo", params=params)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查询请假记录失败"),
                "records": [],
            }

        data = result.get("data", {})
        records = data.get("list", [])

        # Enrich records with status name
        for record in records:
            status = record.get("status")
            record["status_name"] = self.LEAVE_STATUS_MAP.get(status, f"未知状态({status})")

        return {
            "success": True,
            "msg": "查询成功",
            "page": page,
            "page_size": page_size,
            "total": data.get("total", len(records)),
            "records": records,
        }

    def get_leave_detail(self, leave_id: str) -> dict[str, Any]:
        """Get leave request detail"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
            }

        result = self._get_json(f"/studentleave/studentLeaveInfo/{leave_id}")

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查询请假详情失败"),
            }

        data = result.get("data", {})
        status = data.get("status")
        data["status_name"] = self.LEAVE_STATUS_MAP.get(status, f"未知状态({status})")

        return {
            "success": True,
            "msg": "查询成功",
            "detail": data,
        }

    def submit_leave_request(
        self,
        leave_type: str,
        start_time: str,
        end_time: str,
        reason: str,
        attachment_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Submit a new leave request"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
            }

        data = {
            "leaveType": leave_type,
            "startTime": start_time,
            "endTime": end_time,
            "leaveReason": reason,
            "attachmentList": attachment_urls or [],
        }

        result = self._post_json("/studentleave/studentLeaveInfo/submit", data=data)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "提交请假申请失败"),
            }

        return {
            "success": True,
            "msg": "请假申请提交成功",
            "leave_id": result.get("data", {}).get("id"),
        }

    def cancel_leave_request(self, leave_id: str) -> dict[str, Any]:
        """Cancel a leave request"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
            }

        result = self._post_json(f"/studentleave/studentLeaveInfo/cancel/{leave_id}")

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "撤销请假申请失败"),
            }

        return {
            "success": True,
            "msg": "请假申请已撤销",
        }

    def confirm_leave_return(self, leave_id: str) -> dict[str, Any]:
        """Confirm return from leave (销假)"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
            }

        result = self._post_json(f"/studentleave/studentLeaveInfo/confirmReturn/{leave_id}")

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "销假失败"),
            }

        return {
            "success": True,
            "msg": "销假成功",
        }

    # ==================== Sign-in Management ====================

    def get_signin_task_list(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get sign-in task list"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
                "records": [],
            }

        params = {
            "pageNo": page,
            "pageSize": page_size,
        }

        result = self._get_json("/sign-in/stu-task/list", params=params)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查询签到任务失败"),
                "records": [],
            }

        data = result.get("data", {})
        records = data.get("list", [])

        return {
            "success": True,
            "msg": "查询成功",
            "page": page,
            "page_size": page_size,
            "total": data.get("total", len(records)),
            "records": records,
        }

    def do_signin(
        self,
        task_id: str,
        task_secondary_id: str = "",
        latitude: float = 0.0,
        longitude: float = 0.0,
        address: str = "",
        remark: str = "",
    ) -> dict[str, Any]:
        """Perform sign-in for a task"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
            }

        data = {
            "taskId": task_id,
            "taskSecondaryId": task_secondary_id,
            "latitude": latitude,
            "longitude": longitude,
            "address": address,
            "remark": remark,
        }

        result = self._post_json("/sign-in/student-task/signIn", data=data)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "签到失败"),
            }

        return {
            "success": True,
            "msg": "签到成功",
        }

    # ==================== Check Sleep Management ====================

    def get_checksleep_task_list(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get check-sleep task list"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
                "records": [],
            }

        params = {
            "pageNo": page,
            "pageSize": page_size,
        }

        result = self._get_json("/check-sleep/stu-task/list", params=params)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查询查寝任务失败"),
                "records": [],
            }

        data = result.get("data", {})
        records = data.get("list", [])

        return {
            "success": True,
            "msg": "查询成功",
            "page": page,
            "page_size": page_size,
            "total": data.get("total", len(records)),
            "records": records,
        }

    def do_checksleep(
        self,
        task_id: str,
        latitude: float = 0.0,
        longitude: float = 0.0,
        address: str = "",
        image_url: str = "",
    ) -> dict[str, Any]:
        """Perform check-sleep for a task"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
            }

        data = {
            "taskId": task_id,
            "latitude": latitude,
            "longitude": longitude,
            "address": address,
            "imageUrl": image_url,
        }

        result = self._post_json("/check-sleep/student-task/check", data=data)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查寝打卡失败"),
            }

        return {
            "success": True,
            "msg": "查寝打卡成功",
        }

    # ==================== Activity Management ====================

    def get_activity_list(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get activity list"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
                "records": [],
            }

        params = {
            "pageNo": page,
            "pageSize": page_size,
        }

        result = self._get_json("/activity-registration/registrationProgress/page/my-activity", params=params)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查询活动列表失败"),
                "records": [],
            }

        data = result.get("data", {})
        records = data.get("list", [])

        return {
            "success": True,
            "msg": "查询成功",
            "page": page,
            "page_size": page_size,
            "total": data.get("total", len(records)),
            "records": records,
        }

    def register_activity(self, activity_id: str) -> dict[str, Any]:
        """Register for an activity"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
            }

        result = self._post_json(f"/activity-registration/registration/register/{activity_id}")

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "活动报名失败"),
            }

        return {
            "success": True,
            "msg": "活动报名成功",
        }

    # ==================== Info Collection ====================

    def get_collection_list(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get info collection list"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
                "records": [],
            }

        params = {
            "pageNo": page,
            "pageSize": page_size,
        }

        result = self._get_json("/infocollection/questionnaire/studentQuestionnaireProgress", params=params)

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查询信息收集列表失败"),
                "records": [],
            }

        data = result.get("data", {})
        records = data.get("list", [])

        return {
            "success": True,
            "msg": "查询成功",
            "page": page,
            "page_size": page_size,
            "total": data.get("total", len(records)),
            "records": records,
        }

    # ==================== Statistics ====================

    def get_task_statistics(self) -> dict[str, Any]:
        """Get task statistics for current user"""
        if not self._is_token_valid() and not self.login():
            return {
                "success": False,
                "msg": self.get_last_error() or "未登录或登录失效",
                "statistics": {},
            }

        result = self._get_json("/activity-registration/registrationProgress/h5Task")

        if result.get("code") != 0:
            return {
                "success": False,
                "msg": result.get("msg", "查询任务统计失败"),
                "statistics": {},
            }

        return {
            "success": True,
            "msg": "查询成功",
            "statistics": result.get("data", {}),
        }


class HebaoMixin:
    def _init_hebao(self, saved_token: str | None = None, cas_cookies: dict[str, Any] | None = None) -> None:
        self._hebao_client = _HebaoClient(self.username, self.password, saved_token, cas_cookies)

    def hebao_login(self) -> bool:
        return self._hebao_client.login()

    def get_hebao_token(self) -> str:
        return self._hebao_client.get_token()

    def get_hebao_cas_cookies(self) -> dict[str, Any]:
        return self._hebao_client.get_cas_cookies()

    def get_hebao_last_error(self) -> str:
        return self._hebao_client.get_last_error()

    def get_leave_list(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hebao_client.get_leave_list(*args, **kwargs)

    def get_leave_detail(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hebao_client.get_leave_detail(*args, **kwargs)

    def get_task_statistics(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hebao_client.get_task_statistics(*args, **kwargs)

    def get_signin_task_list(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hebao_client.get_signin_task_list(*args, **kwargs)

    def get_checksleep_task_list(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hebao_client.get_checksleep_task_list(*args, **kwargs)

    def get_activity_list(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hebao_client.get_activity_list(*args, **kwargs)

    def get_collection_list(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hebao_client.get_collection_list(*args, **kwargs)
