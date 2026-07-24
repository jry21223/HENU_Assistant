"""Versioned encryption for locally materialized account credentials.

Set HENU_MASTER_KEY in production so encrypted profiles remain decryptable across
container or host migrations. Without it, a stable host/user fingerprint is
used for backward-compatible local deployments; process IDs are never used.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

_fernet = None
_V2_PREFIX = "enc:v2:"
_LEGACY_PREFIX = "enc:"
_MASTER_KEY_ENV = "HENU_MASTER_KEY"


class CredentialDecryptionError(RuntimeError):
    pass


def _get_fernet():
    global _fernet
    if _fernet is None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise ImportError(
                "加密功能需要 cryptography 库。请运行: pip install cryptography"
            ) from exc
        _fernet = Fernet
    return _fernet


def _stable_machine_fingerprint() -> str:
    components = [
        platform.node(),
        platform.system(),
        platform.machine(),
        getpass.getuser(),
        str(Path.home()),
    ]
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()


def _legacy_machine_fingerprint() -> str:
    # Exact legacy material, retained only for decrypting existing enc: values.
    components = [
        platform.node(),
        platform.system(),
        platform.machine(),
        str(os.getuid() if hasattr(os, "getuid") else os.getpid()),
        str(Path.home()),
    ]
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()


def _master_material() -> str:
    configured = os.environ.get(_MASTER_KEY_ENV, "")
    if configured:
        return f"env:{configured}"
    return f"host:{_stable_machine_fingerprint()}"


def _derive_key(material: str | None = None, *, version: str = "v2") -> bytes:
    source = material if material is not None else _master_material()
    digest = hashlib.sha256(
        f"henu-assistant:{version}:{source}".encode("utf-8")
    ).digest()
    return base64.urlsafe_b64encode(digest)


def _legacy_derive_key() -> bytes:
    fingerprint = _legacy_machine_fingerprint()
    # Match the previous double-SHA derivation exactly.
    return base64.urlsafe_b64encode(
        hashlib.sha256(fingerprint.encode("utf-8")).digest()
    )


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    Fernet = _get_fernet()
    token = Fernet(_derive_key()).encrypt(str(plaintext).encode("utf-8"))
    return _V2_PREFIX + token.decode("utf-8")


def _decrypt_with_key(token: str, key: bytes) -> str:
    Fernet = _get_fernet()
    return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_value(encrypted: str) -> str:
    if not encrypted:
        return ""
    text = str(encrypted)
    if not text.startswith(_LEGACY_PREFIX):
        return text

    if text.startswith(_V2_PREFIX):
        token = text[len(_V2_PREFIX) :]
        try:
            return _decrypt_with_key(token, _derive_key())
        except Exception as exc:
            raise CredentialDecryptionError(
                "账号密码无法解密；请检查 HENU_MASTER_KEY 是否与加密时一致。"
            ) from exc

    token = text[len(_LEGACY_PREFIX) :]
    errors: list[Exception] = []
    for key in (_legacy_derive_key(), _derive_key()):
        try:
            return _decrypt_with_key(token, key)
        except Exception as exc:
            errors.append(exc)
    raise CredentialDecryptionError(
        "旧版账号密码无法解密；主机指纹可能已变化，请重新绑定账号。"
    ) from errors[-1]


def is_encrypted(value: str) -> bool:
    return bool(value) and str(value).startswith(_LEGACY_PREFIX)


def _read_profile(profile_path: Path) -> dict[str, Any]:
    if not profile_path.exists():
        return {}
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"账号配置 JSON 损坏: {profile_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"账号配置必须是 JSON 对象: {profile_path}")
    return data


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def migrate_profile(profile_path: Path) -> dict[str, Any]:
    data = _read_profile(profile_path)
    if not data:
        return {}
    password = data.get("password")
    if password and not is_encrypted(str(password)):
        data["password"] = encrypt_value(str(password))
        data["credential_key_version"] = "v2"
        _atomic_write_json(profile_path, data)
    return data


def load_encrypted_profile(profile_path: Path) -> dict[str, Any]:
    data = migrate_profile(profile_path)
    if not data:
        return {}
    result = dict(data)
    password = result.get("password")
    if password and is_encrypted(str(password)):
        result["password"] = decrypt_value(str(password))
    return result


def save_encrypted_profile(profile_path: Path, data: dict[str, Any]) -> None:
    save_data = dict(data)
    password = save_data.get("password")
    if password and not is_encrypted(str(password)):
        save_data["password"] = encrypt_value(str(password))
    save_data["credential_key_version"] = "v2"
    _atomic_write_json(profile_path, save_data)
