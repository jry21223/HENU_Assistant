"""
安全存储模块 - 加密保存敏感数据（如密码）

使用 Fernet 对称加密，密钥通过机器指纹派生。
"""

import base64
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

# 延迟导入 cryptography，仅在需要时加载
_fernet = None


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


def _machine_fingerprint() -> str:
    """
    生成机器指纹用于派生加密密钥。
    使用多种系统信息组合，确保密钥在同一台机器上稳定。
    """
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


def _derive_key() -> bytes:
    """
    从机器指纹派生 Fernet 密钥。
    Fernet 需要 32 字节的 URL-safe base64 编码密钥。
    """
    fingerprint = _machine_fingerprint()
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
    return "enc:" + encrypted.decode('utf-8')


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

    # 如果没有加密前缀，可能是旧版明文数据
    if not encrypted.startswith("enc:"):
        return encrypted

    Fernet = _get_fernet()
    key = _derive_key()
    f = Fernet(key)

    try:
        ciphertext = encrypted[4:]  # 移除 "enc:" 前缀
        decrypted = f.decrypt(ciphertext.encode('utf-8'))
        return decrypted.decode('utf-8')
    except Exception:
        # 解密失败，可能密钥变化或数据损坏
        return ""


def is_encrypted(value: str) -> bool:
    """检查值是否已加密"""
    return bool(value) and value.startswith("enc:")


def migrate_profile(profile_path: Path) -> dict[str, Any]:
    """
    迁移配置文件：加密明文密码。

    Args:
        profile_path: 配置文件路径

    Returns:
        迁移后的配置数据
    """
    if not profile_path.exists():
        return {}

    try:
        data = json.loads(profile_path.read_text(encoding='utf-8'))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    changed = False

    # 加密明文密码
    password = data.get("password")
    if password and not is_encrypted(password):
        data["password"] = encrypt_value(password)
        changed = True

    if changed:
        profile_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

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
    if password and is_encrypted(password):
        data["password"] = decrypt_value(password)

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
    if password and not is_encrypted(password):
        save_data["password"] = encrypt_value(password)

    profile_path.write_text(
        json.dumps(save_data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )