from __future__ import annotations

import pytest
import requests
import os
from pathlib import Path
from henu_mcp.core.secure_storage import load_encrypted_profile, save_encrypted_profile
from henu_mcp.tools import server_impl as api


def test_preferences_round_trip_preserves_credentials(account_files):
    before = load_encrypted_profile(account_files["profile_file"])

    saved = api.preferences_set(
        library_location="fixture-library", library_seat_no="001"
    )

    assert saved["success"] is True
    assert api.preferences_get()["preferences"] == {
        "library_location": "fixture-library",
        "library_seat_no": "001",
    }
    assert (
        load_encrypted_profile(account_files["profile_file"])["password"]
        == before["password"]
    )
    assert "password" not in repr(saved)
    assert api.show_account()["account"]["student_id"] == "old-student"


def test_changing_location_discards_the_previous_seat_and_alias(account_files):
    api.preferences_set(library_location="old-library", library_seat_no="009")
    profile = load_encrypted_profile(account_files["profile_file"])
    profile.update({"seat_no": "legacy-seat", "fixture_unrelated": "keep"})
    save_encrypted_profile(account_files["profile_file"], profile)

    api.preferences_set(library_location="new-library")

    assert api.preferences_get()["preferences"] == {
        "library_location": "new-library",
        "library_seat_no": "",
    }
    assert (
        load_encrypted_profile(account_files["profile_file"])["fixture_unrelated"]
        == "keep"
    )


def test_explicit_clear_removes_legacy_alias_without_clearing_other_fields(
    account_files,
):
    profile = load_encrypted_profile(account_files["profile_file"])
    profile.update(
        {"library_location": "library", "library_seat_no": "001", "seat_no": "legacy"}
    )
    save_encrypted_profile(account_files["profile_file"], profile)

    cleared = api.preferences_clear(keys=["library_seat_no"])

    assert cleared["success"] is True
    assert api.preferences_get()["preferences"] == {
        "library_location": "library",
        "library_seat_no": "",
    }
    assert api.show_account()["account"]["student_id"] == "old-student"


@pytest.mark.parametrize("keys", [[], ["password"], ["library_location", "typo"]])
def test_invalid_clear_never_mutates_profile(account_files, keys):
    before = account_files["profile_file"].read_bytes()

    assert api.preferences_clear(keys=keys)["success"] is False

    assert account_files["profile_file"].read_bytes() == before


@pytest.mark.parametrize(
    "updates", [{}, {"library_location": ""}, {"library_seat_no": "  "}]
)
def test_empty_set_is_not_a_clear_operation(account_files, updates):
    before = account_files["profile_file"].read_bytes()
    assert api.preferences_set(**updates)["success"] is False
    assert account_files["profile_file"].read_bytes() == before


def test_same_location_keeps_seat_and_explicit_new_seat_wins(account_files):
    api.preferences_set(library_location="A", library_seat_no="001")
    api.preferences_set(library_location="A")
    assert api.preferences_get()["preferences"]["library_seat_no"] == "001"
    api.preferences_set(library_location="B", library_seat_no="002")
    assert api.preferences_get()["preferences"] == {
        "library_location": "B",
        "library_seat_no": "002",
    }


def test_preferences_without_credentials_never_authorize_booking(
    account_files, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("preferences or incomplete booking reached school HTTP")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    account_files["profile_file"].unlink()

    assert (
        api.preferences_set(library_location="library", library_seat_no="001")[
            "success"
        ]
        is True
    )
    assert api.preferences_get()["preferences"]["library_seat_no"] == "001"
    assert api.library_reserve()["code"] == "missing_target_date"


def test_mcp_contract_exposes_only_the_two_preferences():
    for name in ("preferences_get", "preferences_set", "preferences_clear"):
        assert api.mcp._tool_manager.get_tool(name) is not None
    schema = api.mcp._tool_manager.get_tool("preferences_set").parameters
    assert set(schema["properties"]) == {"library_location", "library_seat_no"}


def test_preference_storage_failure_preserves_credentials(account_files, monkeypatch):
    before = account_files["profile_file"].read_bytes()
    write_text, replace = Path.write_text, os.replace

    def fail_write(path, *args, **kwargs):
        if path == account_files["profile_file"]:
            path.write_bytes(b'{"partial":')
            raise OSError("offline disk failure")
        return write_text(path, *args, **kwargs)

    def fail_replace(source, destination):
        if Path(destination) == account_files["profile_file"]:
            raise OSError("offline atomic replace failure")
        return replace(source, destination)

    monkeypatch.setattr(Path, "write_text", fail_write)
    monkeypatch.setattr(os, "replace", fail_replace)

    result = api.preferences_set(library_location="new-library")

    assert result["success"] is False
    assert account_files["profile_file"].read_bytes() == before
