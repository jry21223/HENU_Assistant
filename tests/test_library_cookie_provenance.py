"""Unattended writes require sessions established by the owning account facade."""

import json
from urllib.parse import urlsplit

import pytest
import requests


@pytest.mark.parametrize(
    "artifact", ["library_cookie_file", "cas_cookie_file", "xk_cookie_file"]
)
@pytest.mark.parametrize("owner", ["", "another-student"])
def test_public_library_query_never_reuses_an_unowned_or_foreign_session(
    account_files, monkeypatch, artifact, owner
):
    from henu_mcp.tools import server_impl as api

    jar = (
        {"_v4_token": "untrusted-library"}
        if artifact == "library_cookie_file"
        else {"CASTGC": "untrusted-cas"}
    )
    if owner:
        jar["_subject_student_id"] = owner
    account_files[artifact].write_text(json.dumps(jar))
    observed = []

    def request(session, method, url, **kwargs):
        observed.append((dict(session.headers), session.cookies.get_dict()))
        parsed = urlsplit(url)
        if parsed.netloc == "zwyy.henu.edu.cn":
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({"code": 0, "data": {"area": []}}).encode()
            return response
        raise requests.Timeout("synthetic fresh authentication unavailable")

    monkeypatch.setattr(requests.Session, "request", request)
    api.library_query(view="locations", target_date="2026-10-31")
    assert "untrusted-library" not in str(observed)
    assert "untrusted-cas" not in str(observed)
