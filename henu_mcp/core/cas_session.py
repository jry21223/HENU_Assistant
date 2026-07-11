from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


CAS_COOKIE_DOMAIN = "ids.henu.edu.cn"
CAS_COOKIE_NAMES = frozenset(
    {"CASTGC", "TGC", "happyVoyage", "platformMultilingual"}
)


def extract_cas_cookies(cookies: Any) -> dict[str, Any]:
    """Return the reusable IDS CAS subset without exposing service cookies."""
    if cookies is None:
        return {}
    if isinstance(cookies, Mapping) and not hasattr(cookies, "get_dict"):
        items: Iterable[tuple[Any, Any, str]] = (
            (name, value, "")
            for name, value in cookies.items()
        )
    else:
        items = (
            (
                getattr(cookie, "name", ""),
                getattr(cookie, "value", None),
                str(getattr(cookie, "domain", "") or "").lstrip("."),
            )
            for cookie in cookies
        )
    return {
        str(name): value
        for name, value, domain in items
        if (
            str(name) in CAS_COOKIE_NAMES
            and value not in (None, "")
            and domain in {"", CAS_COOKIE_DOMAIN}
        )
    }


def merge_cas_cookies(*sources: Any) -> dict[str, Any]:
    """Merge CAS jars left-to-right, letting fresher sources win."""
    merged: dict[str, Any] = {}
    for source in sources:
        merged.update(extract_cas_cookies(source))
    return merged


def apply_cas_cookies(session: Any, cookies: Any) -> None:
    """Inject reusable CAS cookies with the correct IDS domain and path."""
    for name, value in extract_cas_cookies(cookies).items():
        session.cookies.set(
            name,
            value,
            domain=CAS_COOKIE_DOMAIN,
            path="/",
        )
