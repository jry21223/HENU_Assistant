import base64
import hashlib
import hmac
import json

import pytest

from henu_plugin.kit_client import kit_request, KitError


def test_kit_request_signs_body_and_rejects_redirects(monkeypatch):
    settings = {
        "core_url": "https://core.test",
        "client_id": "henu-bot-test",
        "key_id": "test",
        "secret": "x" * 32,
    }

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, _):
            yield b'{"data":{"bound":false}}'

    def post(url, **kwargs):
        assert url == "https://core.test/api/v1/qq-bindings/status"
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == (3, 8)
        assert kwargs["auth"] == ("henu-bot-test", "x" * 32)
        assert json.loads(kwargs["data"]) == {"subject": "alice"}
        headers = kwargs["headers"]
        canonical = "\n".join(
            (
                "POST",
                "/api/v1/qq-bindings/status",
                headers["X-Timestamp"],
                headers["X-Nonce"],
                hashlib.sha256(kwargs["data"]).hexdigest(),
            )
        )
        expected = (
            base64.urlsafe_b64encode(
                hmac.digest(b"x" * 32, canonical.encode(), "sha256")
            )
            .decode()
            .rstrip("=")
        )
        assert headers["X-Signature"] == expected
        return Response()

    monkeypatch.setattr("requests.post", post)
    assert kit_request(settings, "status", {"subject": "alice"}) == {"bound": False}
    Response.status_code = 302
    with pytest.raises(KitError):
        kit_request(settings, "status", {"subject": "alice"})
