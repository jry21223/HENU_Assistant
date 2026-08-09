"""Versioned encryption for locally stored account credentials.

Set ``HENU_MASTER_KEY`` when credentials must survive host migrations. Without
it, v2 uses a stable host/user fingerprint; process IDs are never used for new
ciphertexts. The historical process-dependent material is retained only for
decrypting existing ``enc:`` values.
"""

import base64
import getpass
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Any

import campus_core.atomic_io as atomic_io

# 延迟导入 cryptography，仅在需要时加载
_fernet = None
_V2_PREFIX = "enc:v2:"
_LEGACY_PREFIX = "enc:"
_MASTER_KEY_ENV = "HENU_MASTER_KEY"


class CredentialDecryptionError(RuntimeError):
    """Stored credentials cannot be decrypted with the available key material."""


def _get_fernet():
    """延迟加载 Fernet"""
    global _fernet
    if _fernet is None:
        try:
            from cryptography.fernet import Fernet
            _fernet = Fernet
        except ImportError:
            raise ImportError(
                "加密功能需要 cryptography 库。请运行: pip install cryptography"
            )
    return _fernet


def _legacy_machine_fingerprint() -> str:
    """Return the exact historical material used by unversioned values."""
    components = [
        platform.node(),  # 主机名
        platform.system(),  # 操作系统
        platform.machine(),  # 机器类型
        str(os.getuid() if hasattr(os, 'getuid') else os.getpid()),  # 用户ID或进程ID
    ]

    # 添加用户主目录路径作为额外因子
    home = str(Path.home())
    components.append(home)

    # 组合生成指纹
    combined = "|".join(components)
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()


def _stable_machine_fingerprint() -> str:
    components = [
        platform.node(),
        platform.system(),
        platform.machine(),
        getpass.getuser(),
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
    key_bytes = hashlib.sha256(
        f"henu-assistant:{version}:{source}".encode("utf-8")
    ).digest()
    return base64.urlsafe_b64encode(key_bytes)


def _legacy_derive_key() -> bytes:
    """Derive the historical double-SHA Fernet key without changing it."""
    fingerprint = _legacy_machine_fingerprint()
    # 使用 SHA256 派生 32 字节密钥
    key_bytes = hashlib.sha256(fingerprint.encode('utf-8')).digest()
    # 转换为 URL-safe base64 格式
    return base64.urlsafe_b64encode(key_bytes)


def encrypt_value(plaintext: str) -> str:
    """
    加密字符串值。

    Args:
        plaintext: 明文字符串

    Returns:
        加密后的字符串（包含前缀标识）
    """
    if not plaintext:
        return ""

    Fernet = _get_fernet()
    key = _derive_key()
    f = Fernet(key)
    encrypted = f.encrypt(plaintext.encode('utf-8'))
    return _V2_PREFIX + encrypted.decode('utf-8')


def _decrypt_with_key(token: str, key: bytes) -> str:
    Fernet = _get_fernet()
    return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_value(encrypted: str) -> str:
    """
    解密字符串值。

    Args:
        encrypted: 加密的字符串（带 enc: 前缀）

    Returns:
        解密后的明文字符串
    """
    if not encrypted:
        return ""

    text = str(encrypted)
    # 如果没有加密前缀，可能是旧版明文数据
    if not text.startswith(_LEGACY_PREFIX):
        return text

    if text.startswith(_V2_PREFIX):
        token = text[len(_V2_PREFIX):]
        try:
            return _decrypt_with_key(token, _derive_key())
        except Exception as exc:
            raise CredentialDecryptionError(
                "账号密码无法解密；请检查 HENU_MASTER_KEY 是否与加密时一致。"
            ) from exc

    token = text[len(_LEGACY_PREFIX):]
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
    """检查值是否已加密"""
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


def migrate_profile(profile_path: Path) -> dict[str, Any]:
    """
    迁移配置文件：加密明文密码。

    Args:
        profile_path: 配置文件路径

    Returns:
        迁移后的配置数据
    """
    data = _read_profile(profile_path)
    if not data:
        return {}

    changed = False

    # 加密明文密码
    password = data.get("password")
    if password and not is_encrypted(str(password)):
        data["password"] = encrypt_value(str(password))
        data["credential_key_version"] = "v2"
        changed = True

    if changed:
        atomic_io.atomic_write_json(profile_path, data)

    return data


def load_encrypted_profile(profile_path: Path) -> dict[str, Any]:
    """
    加载并解密配置文件。

    Args:
        profile_path: 配置文件路径

    Returns:
        解密后的配置数据
    """
    # 先尝试迁移
    data = migrate_profile(profile_path)

    if not data:
        return {}

    # 解密密码
    password = data.get("password")
    if password and is_encrypted(str(password)):
        data["password"] = decrypt_value(str(password))

    return data


def save_encrypted_profile(profile_path: Path, data: dict[str, Any]) -> None:
    """
    加密并保存配置文件。

    Args:
        profile_path: 配置文件路径
        data: 配置数据（密码将被加密存储）
    """
    # 复制数据，避免修改原始对象
    save_data = dict(data)

    # 加密密码
    password = save_data.get("password")
    if password and not is_encrypted(str(password)):
        save_data["password"] = encrypt_value(str(password))
    save_data["credential_key_version"] = "v2"

    atomic_io.atomic_write_json(profile_path, save_data)
