import base64
import hashlib
import hmac
import json

import pytest

from henu_plugin.kit_client import kit_request, kit_settings, KitError


def test_kit_settings_accepts_public_core_proxy_path(monkeypatch, tmp_path):
    config = tmp_path / ".env"
    config.write_text(
        "HENU_KIT_CORE_URL=https://henukit.cn/account-auth\n"
        "HENU_KIT_PORTAL_URL=https://henukit.cn\n"
        "HENU_KIT_CLIENT_ID=henu-bot-test\n"
        "HENU_KIT_KEY_ID=key-test\n"
        f"HENU_KIT_SECRET={'x' * 32}\n"
        "HENU_KIT_BOT_UUID=bot-test\n"
    )
    monkeypatch.setattr("henu_plugin.kit_client.ENV_FILE", config)
    for name in ("CORE_URL", "PORTAL_URL", "CLIENT_ID", "KEY_ID", "SECRET", "BOT_UUID"):
        monkeypatch.delenv("HENU_KIT_" + name, raising=False)

    settings = kit_settings()
    assert settings["core_url"] == "https://henukit.cn/account-auth"
    assert settings["portal_url"] == "https://henukit.cn"

    monkeypatch.setenv("HENU_KIT_CORE_URL", "https://henukit.cn/other")
    with pytest.raises(ValueError):
        kit_settings()
    for wrong_core in (
        "https://elsewhere.test/account-auth",
        "https://henukit.cn:444/account-auth",
        "https://henukit.cn/account-auth?",
        "https://henukit.cn/account-auth#",
    ):
        monkeypatch.setenv("HENU_KIT_CORE_URL", wrong_core)
        with pytest.raises(ValueError):
            kit_settings()
    monkeypatch.delenv("HENU_KIT_CORE_URL")
    monkeypatch.setenv("HENU_KIT_PORTAL_URL", "https://henukit.cn/account-auth")
    with pytest.raises(ValueError):
        kit_settings()


@pytest.mark.parametrize(
    ("core_url", "expected_url"),
    [
        ("https://core.test", "https://core.test/api/v1/qq-bindings/status"),
        (
            "https://henukit.cn/account-auth",
            "https://henukit.cn/account-auth/api/v1/qq-bindings/status",
        ),
    ],
)
def test_kit_request_signs_body_and_rejects_redirects(
    monkeypatch, core_url, expected_url
):
    settings = {
        "core_url": core_url,
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
        assert url == expected_url
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


def test_member_lookup_requires_owner_boolean_and_signed_uid(monkeypatch):
    from henu_plugin.kit_entitlement import membership_request

    settings = {
        "url": "https://membership.test",
        "client_id": "bot-reader",
        "key_id": "key",
        "secret": "m" * 40,
    }
    uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield json.dumps({"data": {"user_id": uid, "lifetime": True}}).encode()

    def get(url, **kwargs):
        assert url == "https://membership.test/api/v1/bot/membership-entitlement"
        assert kwargs["allow_redirects"] is False
        headers = kwargs["headers"]
        assert headers["X-Actor-User-Id"] == uid
        canonical = "\n".join(
            (
                "GET",
                "/api/v1/bot/membership-entitlement",
                headers["X-Timestamp"],
                headers["X-Nonce"],
                hashlib.sha256(b"").hexdigest(),
                uid,
            )
        )
        assert headers["X-Signature"] == base64.urlsafe_b64encode(
            hmac.new(b"m" * 40, canonical.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        return Response()

    monkeypatch.setattr("requests.get", get)
    assert membership_request(settings, uid) is True


@pytest.mark.parametrize("member", [False, "true", None])
def test_lifetime_gate_rejects_nonmembers_and_malformed_owner_responses(
    monkeypatch, member
):
    import asyncio
    from henu_plugin.library_automation import lifetime_account

    uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    values = {
        "CORE_URL": "https://core.test",
        "PORTAL_URL": "https://portal.test",
        "CLIENT_ID": "bind-bot",
        "KEY_ID": "key",
        "SECRET": "c" * 40,
        "BOT_UUID": "bot",
        "ENTITLEMENT_URL": "https://owner.test",
        "ENTITLEMENT_CLIENT_ID": "reader-bot",
        "ENTITLEMENT_KEY_ID": "key",
        "ENTITLEMENT_SECRET": "m" * 40,
    }
    for name, value in values.items():
        monkeypatch.setenv("HENU_KIT_" + name, value)

    class Response:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def iter_content(self, size):
            yield json.dumps({"data": self.data}).encode()

    def post(url, **kwargs):
        assert json.loads(kwargs["data"]) == {"subject": "alice"}
        return Response({"bound": True, "user_id": uid})

    def get(url, **kwargs):
        assert kwargs["headers"]["X-Actor-User-Id"] == uid
        return Response({"user_id": uid, "lifetime": member})

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr("requests.get", get)
    with pytest.raises(KitError):
        asyncio.run(lifetime_account("bot", "alice"))
