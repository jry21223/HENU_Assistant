from __future__ import annotations

import json

import pytest

from henu_mcp.core.secure_storage import (
    CredentialDecryptionError,
    decrypt_value,
    encrypt_value,
    load_encrypted_profile,
    save_encrypted_profile,
)


def test_v2_round_trip_uses_stable_configured_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HENU_MASTER_KEY", "test-master-key")
    token = encrypt_value("secret")
    assert token.startswith("enc:v2:")
    assert decrypt_value(token) == "secret"

    profile = tmp_path / "profile.json"
    save_encrypted_profile(profile, {"student_id": "1", "password": "secret"})
    on_disk = json.loads(profile.read_text(encoding="utf-8"))
    assert on_disk["password"].startswith("enc:v2:")
    assert load_encrypted_profile(profile)["password"] == "secret"


def test_wrong_master_key_fails_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("HENU_MASTER_KEY", "key-a")
    token = encrypt_value("secret")
    monkeypatch.setenv("HENU_MASTER_KEY", "key-b")
    with pytest.raises(CredentialDecryptionError):
        decrypt_value(token)
