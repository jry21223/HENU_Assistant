"""Signed, service-only KIT binding client; never exposed as an LLM tool."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shlex
import time
from urllib.parse import urlsplit

import requests

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
FIELDS = ("CORE_URL", "PORTAL_URL", "CLIENT_ID", "KEY_ID", "SECRET", "BOT_UUID")


def kit_settings():
    values = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, raw = line.partition("=")
            if sep and key.strip() in {"HENU_KIT_" + field for field in FIELDS}:
                parts = shlex.split(raw, comments=True)
                values[key.strip()] = parts[0] if parts else ""
    except FileNotFoundError:
        pass
    settings = {
        field.lower(): os.environ.get(
            "HENU_KIT_" + field, values.get("HENU_KIT_" + field, "")
        ).strip()
        for field in FIELDS
    }
    if not any(settings.values()):
        return None
    if not all(settings.values()) or len(settings["secret"]) < 32:
        raise ValueError("incomplete KIT configuration")
    # Production routes Core through /account-auth and strips that prefix
    # before forwarding. The request signature still covers Core's path.
    for key in ("core_url", "portal_url"):
        parsed = urlsplit(settings[key])
        is_origin = parsed.path in ("", "/")
        is_core_proxy = (
            key == "core_url"
            and parsed.netloc == "henukit.cn"
            and parsed.path in ("/account-auth", "/account-auth/")
        )
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "?" in settings[key]
            or "#" in settings[key]
            or not (is_origin or is_core_proxy)
        ):
            raise ValueError("KIT requires an approved HTTPS endpoint")
        settings[key] = settings[key].rstrip("/")
    return settings


class KitError(RuntimeError):
    def __init__(self, code="UNAVAILABLE"):
        self.code = code
        super().__init__(code)


def kit_request(settings, action, payload):
    if action not in {"start", "pending", "confirm", "status", "resolve", "unlink"}:
        raise KitError("INVALID_ACTION")
    path = "/api/v1/qq-bindings/" + action
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    stamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
    canonical = "\n".join(("POST", path, stamp, nonce, hashlib.sha256(raw).hexdigest()))
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                settings["secret"].encode(), canonical.encode(), hashlib.sha256
            ).digest()
        )
        .decode()
        .rstrip("=")
    )
    try:
        with requests.post(
            settings["core_url"] + path,
            data=raw,
            timeout=(3, 8),
            allow_redirects=False,
            stream=True,
            auth=(settings["client_id"], settings["secret"]),
            headers={
                "Content-Type": "application/json",
                "X-Service-Id": settings["client_id"],
                "X-Key-Id": settings["key_id"],
                "X-Timestamp": stamp,
                "X-Nonce": nonce,
                "X-Signature": signature,
            },
        ) as response:
            content = bytearray()
            for chunk in response.iter_content(4096):
                content.extend(chunk)
                if len(content) > 8192:
                    raise KitError()
            envelope = json.loads(content)
            if response.status_code != 200:
                code = envelope.get("error", {}).get("code", "UNAVAILABLE")
                raise KitError(
                    code
                    if code
                    in {
                        "NOT_BOUND",
                        "ALREADY_BOUND",
                        "LINK_EXPIRED",
                        "APPROVAL_EXPIRED",
                        "WEB_APPROVAL_REQUIRED",
                        "RATE_LIMITED",
                    }
                    else "UNAVAILABLE"
                )
            data = envelope.get("data")
            if not isinstance(data, dict):
                raise KitError()
            return data
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        raise KitError() from exc
