from __future__ import annotations

from campus_core.bot import HenuCampusBot
from campus_core.hebao import _HebaoClient
from henu_mcp.core.cas_session import CAS_COOKIE_DOMAIN, CAS_COOKIE_NAMES
from henu_mcp.core.course_schedule import HenuXkClient


def _cookie_locations(client) -> set[tuple[str, str, str]]:
    return {
        (cookie.name, cookie.domain.lstrip("."), cookie.path)
        for cookie in client.session.cookies
    }


def test_all_cas_clients_inject_the_shared_jar_on_the_ids_domain() -> None:
    shared = {
        name: f"value-{name}"
        for name in CAS_COOKIE_NAMES
    }

    academic = HenuXkClient(
        "student",
        "password",
        {**shared, "JSESSIONID": "academic-private"},
    )
    library = HenuCampusBot(
        "student",
        "password",
        {**shared, "_v4_token": "library-token", "JSESSIONID": "library-private"},
        shared,
    )
    hebao = _HebaoClient(
        "student",
        "password",
        "hebao-token",
        shared,
    )

    for client in (academic, library, hebao):
        locations = _cookie_locations(client)
        assert all(
            (name, CAS_COOKIE_DOMAIN, "/") in locations
            for name in CAS_COOKIE_NAMES
        )

    assert academic.get_cookies() == {**shared, "JSESSIONID": "academic-private"}
    assert library.get_cas_cookies() == shared
    assert hebao.get_cas_cookies() == shared
    assert library.token == "library-token"
    assert hebao.token == "hebao-token"


def test_service_credentials_never_enter_the_shared_cas_jar() -> None:
    library = HenuCampusBot(
        "student",
        "password",
        {
            "CASTGC": "shared",
            "_v4_token": "library-token",
            "JSESSIONID": "library-private",
        },
    )

    assert library.get_cas_cookies() == {"CASTGC": "shared"}
    assert library.token == "library-token"


def test_hebao_does_not_accept_error_or_missing_success_as_logged_in(monkeypatch):
    from types import SimpleNamespace
    for status, payload in [(503, {'code': 0}), (200, {}), (200, {'code': 500}), (200, {'code': False})]:
        monkeypatch.setattr('requests.Session.get', lambda *a, status=status, payload=payload, **k:
                            SimpleNamespace(status_code=status, json=lambda: payload, url='https://ids.henu.edu.cn/authserver/login', text=''))
        client = _HebaoClient('student', '', 'cached-token')
        assert client.login() is False


def test_hebao_login_exchanges_ticket_through_official_oauth_form(monkeypatch):
    from types import SimpleNamespace

    client = _HebaoClient("student", "", cas_cookies={"CASTGC": "saved"})
    calls = []

    def get(url, **kwargs):
        calls.append(("GET", url))
        if url.startswith(client.cas_login_url):
            return SimpleNamespace(url=client.cas_return_url + "?ticket=ST-fixture", text="")
        assert url == client.base_url + "/admin/user/info"
        assert kwargs["headers"]["Authorization"] == "Bearer fixture-token"
        return SimpleNamespace(status_code=200, json=lambda: {"code": 0, "data": {"userinfo": {"studentNo": "student"}}})

    def post(url, **kwargs):
        calls.append(("POST", url))
        assert url == client.base_url + "/auth/oauth/token"
        assert kwargs["data"] == {"grant_type": "cas", "ticket": "ST-fixture", "redirectUrl": client.cas_return_url}
        assert kwargs["headers"]["Content-Type"].startswith("application/x-www-form-urlencoded")
        assert kwargs["allow_redirects"] is False
        return SimpleNamespace(status_code=200, json=lambda: {"access_token": "fixture-token", "token_type": "bearer"})

    monkeypatch.setattr(client.session, "get", get)
    monkeypatch.setattr(client.session, "post", post)
    assert client.login() is True
    assert client.get_token() == "Bearer fixture-token"
    assert len(calls) == 3


def test_hebao_failed_exchange_does_not_resubmit_campus_password(monkeypatch):
    from types import SimpleNamespace

    client = _HebaoClient("student", "must-not-submit")
    calls = []
    def get(url, **kwargs):
        assert not calls
        calls.append("cas")
        return SimpleNamespace(url=client.cas_return_url + "?ticket=ST-fixture", text="")
    def post(url, **kwargs):
        assert url == client.base_url + "/auth/oauth/token"
        calls.append("exchange")
        return SimpleNamespace(status_code=401, json=lambda: {"error": "invalid_grant"})
    monkeypatch.setattr(client.session, "get", get)
    monkeypatch.setattr(client.session, "post", post)
    assert client.login() is False
    assert calls == ["cas", "exchange"]
    assert client.get_token() == ""


def test_hebao_oauth_does_not_accept_another_student_or_invalid_payload(monkeypatch):
    from types import SimpleNamespace

    for payload in [
        {"code": 0, "data": {"userinfo": {"studentNo": "another-student"}}},
        {"code": False, "data": {"userinfo": {"studentNo": "student"}}},
        {"code": 0, "data": []},
    ]:
        client = _HebaoClient("student", "")
        def get(url, **kwargs):
            if url.startswith(client.cas_login_url):
                return SimpleNamespace(url=client.cas_return_url + "?ticket=ST-fixture", text="")
            return SimpleNamespace(status_code=200, json=lambda: payload)
        monkeypatch.setattr(client.session, "get", get)
        monkeypatch.setattr(client.session, "post", lambda *a, **k: SimpleNamespace(
            status_code=200, json=lambda: {"access_token": "fixture-token", "token_type": "bearer"}))
        assert client.login() is False
        assert client.get_token() == ""
        assert "Authorization" not in client.session.headers
