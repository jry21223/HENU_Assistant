"""服务器 A（LangBot 插件）侧的桥客户端。

经公网以 AES-256-GCM 加密信封调用守护进程（服务器 B）的 bridge.py：
配置推送、实时状态、扫码登录。不依赖 HTTPS——信封本身提供
保密性、完整性与来源认证；时间戳+nonce 防重放。

配置（环境变量，与 HENU_MASTER_KEY 同一 .env）：
  HENU_BRIDGE_URL      例如 http://<B公网IP>:8300
  HENU_BRIDGE_SECRET   与 B 侧 config.json bridge.secret 相同
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Any

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_INFO = b"henu-bridge-v1:"
DEFAULT_TIMEOUT = 3.0
TS_WINDOW_SECONDS = 300


class BridgeError(RuntimeError):
    """桥调用失败（未配置、网络不可达、认证被拒、响应不合法）。"""


def bridge_settings() -> dict[str, str] | None:
    """未配置时返回 None——桥是可选增强，不影响本地命令成功。"""
    url = os.environ.get("HENU_BRIDGE_URL", "").strip().rstrip("/")
    secret = os.environ.get("HENU_BRIDGE_SECRET", "").strip()
    if not url or not secret:
        return None
    return {"url": url, "secret": secret}


def canonical_hash(config: Any) -> str:
    """与 B 侧 bridge.config_hash 完全一致的规范化哈希。输入应为 sanitize 后的配置。"""
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(KEY_INFO + secret.encode("utf-8")).digest()


def _seal(secret: str, path: str, payload: dict[str, Any]) -> bytes:
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    aad = f"{path}|{ts}|{nonce}".encode("utf-8")
    gcm_nonce = secrets.token_bytes(12)
    ct = AESGCM(_derive_key(secret)).encrypt(gcm_nonce, json.dumps(payload).encode("utf-8"), aad)
    return json.dumps({"v": 1, "ts": ts, "nonce": nonce,
                       "ct": base64.b64encode(gcm_nonce + ct).decode("ascii")}).encode("utf-8")


def _open(secret: str, path: str, body: bytes) -> dict[str, Any]:
    try:
        envelope = json.loads(body.decode("utf-8"))
        ts = int(envelope["ts"])
        nonce = str(envelope["nonce"])
        blob = base64.b64decode(envelope["ct"], validate=True)
    except Exception as exc:
        raise BridgeError(f"桥响应信封不合法: {exc}") from exc
    if abs(int(time.time()) - ts) > TS_WINDOW_SECONDS:
        raise BridgeError("桥响应时间戳超出窗口")
    aad = f"{path}|{ts}|{nonce}".encode("utf-8")
    try:
        plain = AESGCM(_derive_key(secret)).decrypt(blob[:12], blob[12:], aad)
    except Exception as exc:
        raise BridgeError("桥响应解密失败") from exc
    try:
        return json.loads(plain.decode("utf-8"))
    except Exception as exc:
        raise BridgeError("桥响应明文不是合法 JSON") from exc


def call(path: str, payload: dict[str, Any], timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    settings = bridge_settings()
    if settings is None:
        raise BridgeError("桥未配置（HENU_BRIDGE_URL / HENU_BRIDGE_SECRET）")
    body = _seal(settings["secret"], path, payload)
    try:
        response = requests.post(
            settings["url"] + path, data=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise BridgeError(f"桥不可达: {type(exc).__name__}") from exc
    if response.status_code == 401:
        try:
            msg = response.json().get("msg", "")
        except ValueError:
            msg = response.text[:80]
        raise BridgeError(f"桥拒绝认证: {msg}")
    if response.status_code != 200:
        raise BridgeError(f"桥返回 HTTP {response.status_code}")
    return _open(settings["secret"], path, response.content)


def push_config(openid: str, sanitized_config: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    """推送净化后的完整配置（含真实令牌，仅在加密信封内传输）。"""
    return call(
        "/v1/config",
        {"openid": openid, "config": sanitized_config, "hash": canonical_hash(sanitized_config)},
        timeout=timeout,
    )


def fetch_status(openid: str, timeout: float = 2.5) -> dict[str, Any]:
    return call("/v1/status", {"openid": openid}, timeout=timeout)


def login_start(openid: str, timeout: float = 12.0) -> dict[str, Any]:
    return call("/v1/login/start", {"openid": openid}, timeout=timeout)


def login_password(openid: str, account: str, password: str, timeout: float = 10.0) -> dict[str, Any]:
    """启动守护进程侧的账号密码登录（无头浏览器自动过验证码）。"""
    return call(
        "/v1/login/password",
        {"openid": openid, "account": account, "password": password},
        timeout=timeout,
    )


def login_result(session_id: str, timeout: float = 3.0) -> dict[str, Any]:
    return call("/v1/login/result", {"session_id": session_id}, timeout=timeout)
