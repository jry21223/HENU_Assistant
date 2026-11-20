"""完美校园原生登录与 token 续期；只用于本人授权账号的只读电费查询。

协议来自 `wmxy-electricity/docs/protocol-audit.md` 与 `analysis/business/sources`。
密码只在调用时使用，不落盘；token 由调用方自行保存。
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass

import httpx
from Crypto.Cipher import DES3
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AUTH_ROOT = "https://app.17wanxiao.com/campus/"
CHANGE_KEY_URL = AUTH_ROOT + "cam_iface46/exchangeSecretkey.action"
LOGIN_URL = AUTH_ROOT + "cam_iface46/loginnew.action"
IV = b"66666666"
RSA_BLOCK = 128
DEFAULT_DEVICE = {
    "appCode": "M002", "deviceId": "", "netWork": "wifi", "qudao": "xiaomi",
    "shebeixinghao": "sdk_gphone64_arm64", "systemType": "android",
    "telephoneInfo": "11", "telephoneModel": "sdk_gphone64_arm64", "wanxiaoVersion": 1050819101,
}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class CampusSession:
    key: str
    session: str


def _android_b64(data: bytes) -> str:
    """Android Base64.DEFAULT：76 列折行，末尾带换行。"""
    return base64.encodebytes(data).decode("ascii")


def _des(data: bytes, key: str, *, decrypt: bool = False) -> bytes:
    """DESede/CBC 加 PKCS5 填充，对应 Java 的 PKCS5Padding。"""
    cipher = DES3.new(key.encode("utf-8")[:24], DES3.MODE_CBC, IV)
    if decrypt:
        if not data:
            raise ValueError("empty ciphertext")
        padded = cipher.decrypt(data)
        pad = padded[-1]
        if not 1 <= pad <= 8 or pad > len(padded):
            raise ValueError("invalid PKCS5 padding")
        return padded[:-pad]
    pad = 8 - len(data) % 8
    return cipher.encrypt(data + bytes([pad]) * pad)


def exchange_secret(http: httpx.Client, *, root: str = AUTH_ROOT) -> CampusSession:
    """RSA-1024 交换：上传公钥，取回 3DES 密钥与会话号。"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    body = json.dumps({"key": _android_b64(public_der)}, separators=(",", ":")).encode("utf-8")
    try:
        response = http.post(root + "cam_iface46/exchangeSecretkey.action", content=body,
                             headers={"Content-Type": "application/text"})
    except httpx.RequestError:
        raise AuthError("无法连接完美校园登录接口。") from None
    if response.status_code != 200:
        raise AuthError(f"密钥交换返回 HTTP {response.status_code}。")
    ciphertext = base64.b64decode(response.content)
    plain = b"".join(
        private_key.decrypt(ciphertext[start:start + RSA_BLOCK], padding.PKCS1v15())
        for start in range(0, len(ciphertext), RSA_BLOCK)
    )
    try:
        data = json.loads(plain)
        return CampusSession(str(data["key"]), str(data["session"]))
    except (ValueError, KeyError):
        raise AuthError("密钥交换响应无法解析。") from None


def _envelope(inner: dict, session: CampusSession) -> tuple[bytes, str]:
    data = _android_b64(_des(json.dumps(inner, separators=(",", ":"), ensure_ascii=False).encode(), session.key))
    body = json.dumps({"session": session.session, "data": data}, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return body, hashlib.sha256(body).hexdigest()


def _open(response: httpx.Response, session: CampusSession) -> dict:
    if response.status_code != 200:
        raise AuthError(f"登录接口返回 HTTP {response.status_code}。")
    if response.headers.get("campusSign", "").lower() != hashlib.sha256(response.content).hexdigest():
        raise AuthError("登录响应签名不匹配。")
    try:
        outer = response.json()
    except ValueError:
        raise AuthError("登录响应无法解析。") from None
    # 实测外层键为 code_/message_/result_；内层 data 仅在成功时出现。
    code = outer.get("code_", outer.get("code"))
    result = outer.get("result_", outer.get("result"))
    message = outer.get("message_", outer.get("message"))
    if str(code or "0") != "0" or result is False:
        raise AuthError(f"{message or '登录失败。'}（code {code}）")
    try:
        return json.loads(_des(base64.b64decode(outer["data"]), session.key, decrypt=True).decode("utf-8"))
    except (ValueError, KeyError):
        raise AuthError("登录响应缺少有效数据。") from None


def _post_login(http: httpx.Client, session: CampusSession, inner: dict, *, root: str = AUTH_ROOT) -> dict:
    body, sign = _envelope(inner, session)
    try:
        response = http.post(root + "cam_iface46/loginnew.action", content=body,
                             headers={"Content-Type": "application/text", "campusSign": sign})
    except httpx.RequestError:
        raise AuthError("无法连接完美校园登录接口。") from None
    return _open(response, session)


def _post_enveloped(http: httpx.Client, session: CampusSession, method: str, inner: dict,
                    *, root: str = AUTH_ROOT) -> dict:
    """同封装的其他登录前接口；成功返回外层 JSON，data 已解密为 data_plain。"""
    body, sign = _envelope(inner, session)
    try:
        response = http.post(root + method, content=body,
                             headers={"Content-Type": "application/text", "campusSign": sign})
    except httpx.RequestError:
        raise AuthError("无法连接完美校园接口。") from None
    if response.status_code != 200:
        raise AuthError(f"接口返回 HTTP {response.status_code}。")
    if response.headers.get("campusSign", "").lower() != hashlib.sha256(response.content).hexdigest():
        raise AuthError("接口响应签名不匹配。")
    try:
        outer = response.json()
    except ValueError:
        raise AuthError("接口响应无法解析。") from None
    code = outer.get("code_", outer.get("code"))
    if str(code or "0") != "0" and outer.get("result_", outer.get("result")) is not True:
        raise AuthError(f"{outer.get('message_') or outer.get('message') or '接口失败。'}（code {code}）")
    if outer.get("data"):
        try:
            outer["data_plain"] = json.loads(
                _des(base64.b64decode(outer["data"]), session.key, decrypt=True).decode("utf-8"))
        except (ValueError, KeyError):
            pass
    return outer


def _device_fields(device: dict | None) -> dict:
    fields = {**DEFAULT_DEVICE, **(device or {})}
    # 实测缺少 deviceId 时服务端直接 500；调用方应传入固定值以保持设备一致。
    if not fields.get("deviceId"):
        fields["deviceId"] = str(uuid.uuid4())
    fields["requestMethod"] = "cam_iface46/loginnew.action"
    return {key: value for key, value in fields.items() if value not in ("", None)}


def login_password(http: httpx.Client, *, account: str, password: str, session: CampusSession,
                   device: dict | None = None) -> dict:
    """密码登录，返回服务端 user 对象（含 token、customId、id）。"""
    if not account.strip() or not password:
        raise AuthError("需要账号和密码。")
    inner = {
        **_device_fields(device), "type": "1", "userName": account,
        "password": [_android_b64(_des(char.encode("utf-8"), session.key)) for char in password],
    }
    return _post_login(http, session, inner).get("user") or {}


def login_token(http: httpx.Client, *, user_id: str, user_name: str, token: str, session: CampusSession,
                device: dict | None = None) -> dict:
    """token 续期（type=3），返回新的 user 对象。不需要密码。"""
    if not token or not user_id or not user_name:
        raise AuthError("续期需要 token、userId 和 userName。")
    inner = {**_device_fields(device), "type": "3", "userId": str(user_id),
             "userName": user_name, "token": token}
    return _post_login(http, session, inner).get("user") or {}


SMS_SEND_METHOD = "cam_iface46/gainMatrixCaptcha543.action"


def send_sms_code(http: httpx.Client, *, mobile: str, session: CampusSession,
                  device: dict | None = None, action: str = "registAndLogin",
                  root: str = AUTH_ROOT) -> dict:
    """向账号绑定手机发送登录验证码。实测：confirm=true 才真正下发。"""
    if not mobile.strip():
        raise AuthError("需要手机号。")
    inner = {"action": action, "confirm": True, "deviceId": _device_fields(device)["deviceId"],
             "mobile": mobile.strip(), "type": "sms"}
    return _post_enveloped(http, session, SMS_SEND_METHOD, inner, root=root)


def login_sms(http: httpx.Client, *, mobile: str, code: str, session: CampusSession,
              device: dict | None = None) -> dict:
    """短信验证码登录（type=2），成功后设备与该账号绑定。"""
    if not mobile.strip() or not code.strip():
        raise AuthError("需要手机号和验证码。")
    inner = {**_device_fields(device), "type": "2", "userName": mobile.strip(), "sms": code.strip()}
    return _post_login(http, session, inner).get("user") or {}


def login_and_refresh(http: httpx.Client, *, token: str, user_id: str, user_name: str,
                      device: dict | None = None) -> tuple[CampusSession, dict]:
    """完整续期：换密钥后走 type=3。返回新会话与新 token 所在的 user。"""
    session = exchange_secret(http)
    return session, login_token(http, user_id=user_id, user_name=user_name, token=token,
                                session=session, device=device)
