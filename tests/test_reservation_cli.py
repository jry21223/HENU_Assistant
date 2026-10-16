from __future__ import annotations

import json
import sys

import henu_cli


def test_cli_reservation_does_not_invent_a_start_time(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "henu_cli.py", "library_reserve", "--location", "区域", "--seat_no", "001",
        "--target_date", "2026-11-01", "--preferred_end_time", "12:00",
    ])
    henu_cli.main()
    assert json.loads(capsys.readouterr().out)["code"] == "missing_preferred_time"


def test_cli_reservation_forwards_the_explicit_end_policy(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "henu_cli.py", "library_reserve", "--location", "区域", "--seat_no", "001",
        "--target_date", "2026-11-01", "--preferred_time", "08:00", "--end_policy", "ask",
    ])
    henu_cli.main()
    assert json.loads(capsys.readouterr().out)["code"] == "end_time_confirmation_required"
