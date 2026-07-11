from __future__ import annotations

from dataclasses import dataclass

from henu_mcp.core.cas_session import (
    CAS_COOKIE_DOMAIN,
    apply_cas_cookies,
    extract_cas_cookies,
    merge_cas_cookies,
)


@dataclass
class _Cookie:
    name: str
    value: str
    domain: str = CAS_COOKIE_DOMAIN


class _CookieJar:
    def __init__(self) -> None:
        self.values: list[tuple[str, str, str, str]] = []

    def set(self, name: str, value: str, *, domain: str, path: str) -> None:
        self.values.append((name, value, domain, path))


class _Session:
    def __init__(self) -> None:
        self.cookies = _CookieJar()


def test_extract_cas_cookies_keeps_only_the_shared_whitelist() -> None:
    cookies = {
        "CASTGC": "castgc",
        "TGC": "tgc",
        "happyVoyage": "lang",
        "platformMultilingual": "zh",
        "JSESSIONID": "service-session",
        "_v4_token": "library-token",
        "Authorization": "hebao-token",
    }

    assert extract_cas_cookies(cookies) == {
        "CASTGC": "castgc",
        "TGC": "tgc",
        "happyVoyage": "lang",
        "platformMultilingual": "zh",
    }


def test_extract_and_merge_accept_cookie_jars_and_prefer_fresher_values() -> None:
    older = [
        _Cookie("CASTGC", "old"),
        _Cookie("CASTGC", "wrong-domain", "xk.henu.edu.cn"),
        _Cookie("JSESSIONID", "private", "xk.henu.edu.cn"),
    ]
    fresher = {"CASTGC": "new", "TGC": "ticket"}

    assert merge_cas_cookies(older, fresher) == {
        "CASTGC": "new",
        "TGC": "ticket",
    }


def test_apply_cas_cookies_uses_the_ids_domain_and_root_path() -> None:
    session = _Session()

    apply_cas_cookies(
        session,
        {"CASTGC": "shared", "_v4_token": "must-not-leak"},
    )

    assert session.cookies.values == [
        ("CASTGC", "shared", CAS_COOKIE_DOMAIN, "/")
    ]
