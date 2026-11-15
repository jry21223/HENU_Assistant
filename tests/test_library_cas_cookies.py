"""Replay the official CAS callback with requests' actual cookie/redirect logic."""

from email.message import Message
from types import SimpleNamespace
from urllib.parse import urlsplit
import json

import requests
from campus_core import HenuCampusBot


def test_expired_library_cookie_is_replaced_during_official_cas_callback(monkeypatch):
    client = HenuCampusBot(
        "fixture-student",
        "",
        {
            "PHPSESSID": "expired-session",
            "CASTGC": "fixture-sso",
            "_subject_student_id": "fixture-student",
        },
    )
    calls = []

    def send(adapter, request, **kwargs):
        calls.append(request.url)
        r = requests.Response()
        r.request = request
        r.url = request.url
        r.status_code = 200
        r._content = b"{}"
        r.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=Message()))
        url = urlsplit(request.url)
        if url.netloc == "ids.henu.edu.cn":
            assert "PHPSESSID" not in request.headers.get("Cookie", "")
            r.status_code = 302
            r.headers["Location"] = (
                "https://zwyy.henu.edu.cn/v4/login/cas?ticket=ST-fixture"
            )
        elif url.path == "/v4/login/cas" and url.query:
            r.status_code = 302
            r.headers["Location"] = "/v4/login/cas"
            r.headers["Set-Cookie"] = "PHPSESSID=fresh-session; Path=/"
            r.raw._original_response.msg["Set-Cookie"] = r.headers["Set-Cookie"]
        elif url.path == "/v4/login/cas":
            cookie = request.headers.get("Cookie", "")
            assert (
                cookie.count("PHPSESSID=") == 1 and "PHPSESSID=fresh-session" in cookie
            )
            assert "_subject_student_id" not in cookie
            r.status_code = 302
            r.headers["Location"] = "/h5/index.html#/cas/?cas=opaque-library-code"
        elif url.path == "/h5/index.html":
            pass
        elif url.path == "/v4/login/user":
            assert json.loads(request.body) == {"cas": "opaque-library-code"}
            r._content = b'{"code":0,"data":{"member":{"token":"fixture-token"}}}'
        else:
            raise AssertionError(request.url)
        return r

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    assert client.login() is True, client.get_last_error()
    assert client.token == "fixture-token"
    assert len(calls) == 5
