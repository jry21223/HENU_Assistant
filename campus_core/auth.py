from __future__ import annotations

import base64
import datetime as dt
import json
import math
import random
import re
import time
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from .time_utils import _now_dt, TimeUtilsMixin


class AuthMixin:
    def get_cookies(self) -> dict[str, Any]:
        cookies = self.session.cookies.get_dict()
        if self.token:
            cookies["_v4_token"] = self.token
        return cookies

    def get_cas_cookies(self) -> dict[str, Any]:
        cas_cookies = {}
        for cookie in self.session.cookies:
            if cookie.domain == "ids.henu.edu.cn" or cookie.name in {"CASTGC", "TGC"}:
                cas_cookies[cookie.name] = cookie.value
        return cas_cookies

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
        date_text = _now_dt().strftime("%Y%m%d")
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
        text = str(url or "").strip()
        if not text:
            return ""

        pieces: list[str] = []
        try:
            parsed = urlsplit(text)
            pieces.extend([parsed.query, parsed.fragment])
            if "?" in parsed.fragment:
                pieces.append(parsed.fragment.split("?", 1)[1])
        except Exception:
            pass

        pieces.extend([text, unquote(text)])
        seen: set[str] = set()
        for raw_piece in pieces:
            piece = str(raw_piece or "")
            if not piece or piece in seen:
                continue
            seen.add(piece)

            candidates = [piece]
            decoded = unquote(piece)
            if decoded != piece:
                candidates.append(decoded)

            for candidate in candidates:
                query_text = candidate.lstrip("?#")
                if "?" in query_text:
                    query_text = query_text.split("?", 1)[1]

                parsed_query = parse_qs(query_text, keep_blank_values=True)
                for key in ("ticket", "cas", "TICKET", "CAS"):
                    values = parsed_query.get(key)
                    if values and str(values[0] or "").strip():
                        return unquote(str(values[0])).strip()

                match = re.search(r"(?:^|[?&#/])(?:ticket|cas)=([^&#]+)", candidate, re.I)
                if match:
                    return unquote(match.group(1)).strip()

        return ""

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
            "需要验证码",
            "请输入验证码",
            "短信验证",
            "手机验证",
        ):
            if keyword in plain:
                return keyword
        if re.search(r'<img[^>]*captcha[^>]*>|<input[^>]*captcha[^>]*>|id=["\']captcha["\']', text, re.I):
            return "需要验证码"
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
                        final_url = str(login_resp.url or "")
                        url_hint = f"，最终 URL: {final_url[:200]}" if final_url else ""
                        self._set_last_error(f"CAS 登录未返回 ticket{url_hint}")
                    return False
                return self._exchange_cas_ticket(cas_ticket)
            except Exception as exc:
                self._set_last_error(f"提交 CAS 登录失败: {self._exc_text(exc)}")
                return False
        finally:
            if original_content_type:
                self.session.headers["Content-Type"] = original_content_type
