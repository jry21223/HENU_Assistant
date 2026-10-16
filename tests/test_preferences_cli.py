from __future__ import annotations

import json
import sys

import pytest

import henu_cli


def run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["henu_cli.py", *args])
    henu_cli.main()
    return json.loads(capsys.readouterr().out)


def test_snake_case_preferences_commands_persist_and_clear(
    account_files, monkeypatch, capsys
):
    saved = run_cli(
        monkeypatch,
        capsys,
        "preferences_set",
        "--library_location",
        "library",
        "--library_seat_no",
        "001",
    )
    assert saved["success"] is True
    assert run_cli(monkeypatch, capsys, "preferences_get")["preferences"] == {
        "library_location": "library",
        "library_seat_no": "001",
    }
    assert (
        run_cli(monkeypatch, capsys, "preferences_clear", "--keys", "library_seat_no")[
            "preferences"
        ]["library_seat_no"]
        == ""
    )


@pytest.mark.parametrize(
    "args",
    [
        ["preferences_clear"],
        ["preferences_clear", "--keys", "typo"],
        ["preferences_clear", "--keys", "library_location", "library_location"],
        ["preferences_get", "extra"],
        ["preferences_set", "--library_location", "A", "--library_location", "B"],
        ["preferences_set", "--library_loc", "A"],
    ],
)
def test_malformed_preference_cli_never_mutates(
    account_files, monkeypatch, capsys, args
):
    before = account_files["profile_file"].read_bytes()
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, capsys, *args)
    assert error.value.code == 2
    assert account_files["profile_file"].read_bytes() == before
