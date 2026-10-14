from __future__ import annotations

import base64
import hashlib
import json

from henu_mcp.core.kingo_auth import (
    kingo_encode_params,
    kingo_str_enc,
    kingo_token,
    login_via_kingo,
    password_expression,
)


REFERENCE_RAW = (
    "_u=MjUxMDI1MDc4Mzs7U0VTUw==&_p=DemoPass123&randnumber="
    "&isPasswordPolicy=1"
)
REFERENCE_KEY = "93446178378580382645177"
REFERENCE_HEX = (
    "6899EF1C617FECB2CF26332A11E66EEF8EBFC19FB38B7A48B61F70B9235E3BB4"
    "C8BDC26EE74A55892ADA2C00E3F824240BFF6998CF4B85AFB4E94BE3448049C1"
    "43BDE562DE7E5B7CA41171BCDE8A7A3569075C5496DC559302D39D4EBAFC2517"
    "D4BC2CA140EC9AC6ECB29DFCFB188A6CE8A4166A98F78C927BAE241F7775FB07"
    "4C436F505639BE0A51C0CF73529DD14EEE072CAFCCBAC5F0"
)


class Response:
    def __init__(self, text: str):
        self.content = text.encode()


class Session:
    def __init__(self, login_html: str, logon_payload: dict):
        self.login_html = login_html
        self.logon_payload = logon_payload
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []

    def get(self, url: str, **_kwargs):
        self.get_calls.append(url)
        if "getTempDeskey" in url:
            return Response(REFERENCE_KEY)
        if "getTempNowtime" in url:
            return Response("1700000000000")
        if "cas/login.action" in url:
            return Response(self.login_html)
        return Response("")

    def post(self, url: str, data: dict, **_kwargs):
        self.post_calls.append((url, data))
        return Response(json.dumps(self.logon_payload))


def decode(response: Response) -> str:
    return response.content.decode()


def test_kingo_crypto_matches_browser_javascript_vector() -> None:
    assert kingo_str_enc(REFERENCE_RAW, REFERENCE_KEY) == REFERENCE_HEX
    assert kingo_encode_params(REFERENCE_RAW, REFERENCE_KEY) == base64.b64encode(
        REFERENCE_HEX.encode()
    ).decode()
    expected = hashlib.md5(
        (
            hashlib.md5(REFERENCE_RAW.encode()).hexdigest()
            + hashlib.md5(b"1700000000000").hexdigest()
        ).encode()
    ).hexdigest()
    assert kingo_token(REFERENCE_RAW, "1700000000000") == expected
    assert password_expression("DemoPass123!") == 15


def test_kingo_login_posts_once_and_verifies_home() -> None:
    session = Session(
        '<script>var _ssessionid="SESSION";</script>'
        '<input id="hid_flag" value="1"><input id="randnumber" value="">',
        {"status": 200, "result": "/frame/homes.action"},
    )
    result = login_via_kingo(
        session=session,
        base_url="https://xk.example",
        username="student",
        password="DemoPass123!",
        decode_text=decode,
        check_logged_in=lambda: True,
    )

    assert result.success is True
    assert result.mode == "xk_kingo"
    assert result.degraded is True
    assert len(session.post_calls) == 1
    assert session.post_calls[0][0].endswith("/cas/logon.action")
    assert any(url.endswith("/frame/homes.action") for url in session.get_calls)


def test_kingo_captcha_stops_before_dynamic_fields_or_post() -> None:
    session = Session(
        '<script>var _ssessionid="SESSION";</script>'
        '<input id="hid_flag" value="0"><input id="randnumber" value="">',
        {},
    )
    result = login_via_kingo(
        session=session,
        base_url="https://xk.example",
        username="student",
        password="password",
        decode_text=decode,
        check_logged_in=lambda: False,
    )

    assert result.success is False
    assert result.error_code == "captcha_required"
    assert session.post_calls == []
    assert not any("getTemp" in url for url in session.get_calls)
