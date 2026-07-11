from __future__ import annotations

from typing import Any

import requests

from henu_mcp.core.cas_session import CAS_COOKIE_NAMES, apply_cas_cookies

from .auth import AuthMixin
from .hebao import HebaoMixin
from .locations import LocationMixin
from .seat_reservation import SeatReservationMixin
from .seminar import SeminarMixin
from .time_utils import TimeUtilsMixin


class HenuCampusBot(
    AuthMixin,
    TimeUtilsMixin,
    LocationMixin,
    SeatReservationMixin,
    SeminarMixin,
    HebaoMixin,
):
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

    def __init__(
        self,
        username: str,
        password: str,
        saved_cookies: dict[str, Any] | None = None,
        cas_cookies: dict[str, Any] | None = None,
        hebao_token: str | None = None,
    ):
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
                "Referer": f"{self.base_url}/",
            }
        )

        if saved_cookies:
            cookie_data = dict(saved_cookies)
            self.token = str(cookie_data.pop("_v4_token", "") or "")
            saved_cas_cookies = {
                name: cookie_data.pop(name)
                for name in tuple(cookie_data)
                if name in CAS_COOKIE_NAMES
            }
            if cookie_data:
                self.session.cookies.update(cookie_data)
            apply_cas_cookies(self.session, saved_cas_cookies)

        apply_cas_cookies(self.session, cas_cookies)

        self._set_auth_header()
        self._init_hebao(hebao_token, cas_cookies)
