from __future__ import annotations

from typing import Any


_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "cookie",
        "cookies",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "ticket",
        "castgc",
        "tgc",
        "ssessionid",
        "jsessionid",
        "sessionid",
        "route",
        "deskey",
    }
)


def sanitize_public_result(value: Any) -> Any:
    """Return a JSON-safe result with credential/session fields redacted."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in _SENSITIVE_KEYS:
                result[key] = "<redacted>"
            else:
                result[key] = sanitize_public_result(item)
        return result
    if isinstance(value, list):
        return [sanitize_public_result(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_public_result(item) for item in value]
    return value
