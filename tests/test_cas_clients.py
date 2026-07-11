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
