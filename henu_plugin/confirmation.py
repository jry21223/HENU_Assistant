"""Two-step confirmation primitives for external campus write operations."""
from __future__ import annotations

import hashlib
import secrets
import shlex
import time
from dataclasses import dataclass
from typing import Any

CONFIRM_OPTION = "--confirm-token"
PENDING_STORAGE_PREFIX = "user:{}:pending_operation"
WRITE_TOOL_NAMES = frozenset(
    {
        "library_reserve",
        "library_auto_signin",
        "library_cancel",
        "seminar_reserve",
        "seminar_signin",
        "seminar_cancel",
    }
)


@dataclass(frozen=True)
class ConfirmationCheck:
    ok: bool
    message: str = ""


def split_confirm_token(command: Any) -> tuple[str, str]:
    """Remove --confirm-token from a CLI command and return (command, token)."""
    raw = str(command or "").strip()
    if not raw:
        return "", ""
    try:
        argv = shlex.split(raw)
    except ValueError:
        return raw, ""

    clean: list[str] = []
    token = ""
    index = 0
    while index < len(argv):
        item = argv[index]
        option, separator, value = item.partition("=")
        if option == CONFIRM_OPTION:
            if separator:
                token = value.strip()
            elif index + 1 < len(argv):
                token = argv[index + 1].strip()
                index += 1
            index += 1
            continue
        clean.append(item)
        index += 1

    return " ".join(shlex.quote(item) for item in clean), token


def operation_fingerprint(storage_key: str, canonical_command: str) -> str:
    material = f"{storage_key}\n{canonical_command}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def pending_storage_key(storage_key: str) -> str:
    return PENDING_STORAGE_PREFIX.format(storage_key)


def create_pending_operation(
    *,
    storage_key: str,
    canonical_command: str,
    query_id: int,
    ttl_seconds: int = 300,
    now: float | None = None,
) -> dict[str, Any]:
    current = time.time() if now is None else float(now)
    ttl = max(30, min(600, int(ttl_seconds)))
    return {
        "schema": "henu.pending-operation.v1",
        "token": secrets.token_urlsafe(18),
        "fingerprint": operation_fingerprint(storage_key, canonical_command),
        "command": canonical_command,
        "created_query_id": int(query_id),
        "created_at": current,
        "expires_at": current + ttl,
    }


def validate_pending_operation(
    pending: Any,
    *,
    token: str,
    storage_key: str,
    canonical_command: str,
    query_id: int,
    now: float | None = None,
) -> ConfirmationCheck:
    if not isinstance(pending, dict) or pending.get("schema") != "henu.pending-operation.v1":
        return ConfirmationCheck(False, "没有待确认的操作，请重新发起。")
    if not secrets.compare_digest(str(pending.get("token") or ""), str(token or "")):
        return ConfirmationCheck(False, "确认令牌无效，请重新发起。")
    if int(pending.get("created_query_id") or -1) == int(query_id):
        return ConfirmationCheck(False, "必须由用户在下一条消息中明确确认，不能在同一轮自动提交。")
    current = time.time() if now is None else float(now)
    if current > float(pending.get("expires_at") or 0):
        return ConfirmationCheck(False, "确认令牌已过期，请重新发起。")
    expected = operation_fingerprint(storage_key, canonical_command)
    if not secrets.compare_digest(str(pending.get("fingerprint") or ""), expected):
        return ConfirmationCheck(False, "确认参数与原操作不一致，请重新发起。")
    return ConfirmationCheck(True)
