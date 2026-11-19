"""完美校园原生登录的合成协议测试（不访问网络、不使用真实凭据）。"""

import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from Crypto.Cipher import DES3

from campus_core.wmxy_auth import (
    CHANGE_KEY_URL,
    IV,
    LOGIN_URL,
    AuthError,
    exchange_secret,
    login_password,
    login_token,
)

KEY = "0123456789abcdef01234567"
SESSION = "synthetic-session"


def des(data: bytes, key: str = KEY, *, decrypt: bool = False) -> bytes:
    cipher = DES3.new(key.encode()[:24], DES3.MODE_CBC, IV)
    if decrypt:
        padded = cipher.decrypt(data)
        return padded[:-padded[-1]]
    pad = 8 - len(data) % 8
    return cipher.encrypt(data + bytes([pad]) * pad)


def make_server(user, *, bad_sign=False, seen=None, failure=None):
    def handle(request):
        if request.url == httpx.URL(CHANGE_KEY_URL):
            public = serialization.load_der_public_key(
                base64.b64decode(json.loads(request.content)["key"]))
            payload = json.dumps({"key": KEY, "session": SESSION}).encode()
            sealed = public.encrypt(payload, padding.PKCS1v15())
            return httpx.Response(200, content=base64.encodebytes(sealed))

        assert request.url == httpx.URL(LOGIN_URL)
        assert request.headers["Content-Type"] == "application/text"
        assert request.headers["campusSign"] == hashlib.sha256(request.content).hexdigest()
        outer = json.loads(request.content)
        assert outer["session"] == SESSION
        inner = json.loads(des(base64.b64decode(outer["data"]), decrypt=True))
        if seen is not None:
            seen.append(inner)
        payload = failure or {"code_": "0", "result_": True,
                              "data": base64.encodebytes(des(json.dumps({"user": user}).encode())).decode()}
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {"campusSign": "0" * 64 if bad_sign else hashlib.sha256(body).hexdigest()}
        return httpx.Response(200, content=body, headers=headers)

    return handle


def run(handle):
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_password_login_encrypts_each_char_and_returns_user():
    seen = []
    with run(make_server({"token": "t-1", "customId": "817", "id": 12345}, seen=seen)) as http:
        session = exchange_secret(http)
        user = login_password(http, account="13800000000", password="ab3", session=session)
    assert (session.key, session.session) == (KEY, SESSION)
    assert user["token"] == "t-1"
    inner = seen[0]
    assert inner["type"] == "1" and inner["userName"] == "13800000000"
    assert inner["requestMethod"] == "cam_iface46/loginnew.action"
    assert len(inner["password"]) == 3
    assert [des(base64.b64decode(item), decrypt=True).decode() for item in inner["password"]] == ["a", "b", "3"]


def test_token_refresh_uses_type_3_without_password():
    seen = []
    with run(make_server({"token": "t-2", "customId": "817", "id": 12345}, seen=seen)) as http:
        session = exchange_secret(http)
        user = login_token(http, user_id="12345", user_name="13800000000",
                           token="old-token", session=session)
    assert user["token"] == "t-2"
    assert seen[0]["type"] == "3" and seen[0]["token"] == "old-token"
    assert seen[0]["userId"] == "12345" and seen[0]["userName"] == "13800000000"
    assert "password" not in seen[0]


def test_response_signature_mismatch_is_rejected():
    with run(make_server({"token": "t"}, bad_sign=True)) as http:
        session = exchange_secret(http)
        with pytest.raises(AuthError, match="签名"):
            login_password(http, account="a", password="b", session=session)


def test_rotated_token_failure_surfaces_business_message():
    failure = {"code_": "100600", "result_": False, "message_": "登录失效，请重新登录"}
    with run(make_server({"token": "t"}, failure=failure)) as http:
        session = exchange_secret(http)
        with pytest.raises(AuthError, match="100600"):
            login_token(http, user_id="1", user_name="u", token="stale", session=session)


def test_device_id_is_always_sent():
    seen = []
    with run(make_server({"token": "t"}, seen=seen)) as http:
        session = exchange_secret(http)
        login_token(http, user_id="1", user_name="u", token="old",
                    session=session, device={"deviceId": "fixed-device"})
    assert seen[0]["deviceId"] == "fixed-device"
