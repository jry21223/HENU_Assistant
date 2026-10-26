"""Read-only Account Portfolio entitlement lookup using an isolated service key."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shlex
import time
from urllib.parse import urlsplit
from uuid import UUID

import requests
from henu_plugin.kit_client import ENV_FILE, KitError

PATH = "/api/v1/bot/membership-entitlement"


def entitlement_settings():
    fields = ("URL", "CLIENT_ID", "KEY_ID", "SECRET")
    values = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().startswith("HENU_KIT_ENTITLEMENT_"):
                parts = shlex.split(value, comments=True)
                values[key.strip()] = parts[0] if parts else ""
    except FileNotFoundError:
        pass
    settings = {
        field.lower(): os.environ.get(
            "HENU_KIT_ENTITLEMENT_" + field,
            values.get("HENU_KIT_ENTITLEMENT_" + field, ""),
        ).strip()
        for field in fields
    }
    parsed = urlsplit(settings["url"])
    if (
        not all(settings.values())
        or len(settings["secret"]) < 32
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "?" in settings["url"]
        or "#" in settings["url"]
    ):
        raise KitError("UNAVAILABLE")
    settings["url"] = settings["url"].rstrip("/")
    return settings


def membership_request(settings, uid):
    uid = str(UUID(uid))
    stamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
    canonical = "\n".join(
        ("GET", PATH, stamp, nonce, hashlib.sha256(b"").hexdigest(), uid)
    )
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
        with requests.get(
            settings["url"] + PATH,
            timeout=(3, 8),
            stream=True,
            allow_redirects=False,
            auth=(settings["client_id"], settings["secret"]),
            headers={
                "X-Service-Id": settings["client_id"],
                "X-Key-Id": settings["key_id"],
                "X-Actor-User-Id": uid,
                "X-Timestamp": stamp,
                "X-Nonce": nonce,
                "X-Signature": signature,
            },
        ) as response:
            if response.status_code != 200:
                raise KitError("UNAVAILABLE")
            raw = bytearray()
            for chunk in response.iter_content(4096):
                raw.extend(chunk)
                if len(raw) > 8192:
                    raise KitError("UNAVAILABLE")
            data = json.loads(raw)["data"]
            if data.get("user_id") != uid or type(data.get("lifetime")) is not bool:
                raise KitError("UNAVAILABLE")
            return data["lifetime"]
    except (
        requests.RequestException,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        raise KitError("UNAVAILABLE") from exc
