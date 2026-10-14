from __future__ import annotations

import json

from henu_mcp.core.period_times import (
    extract_period_times_from_text,
    extract_period_times_from_xiqueer_json,
    is_hhmm,
    normalize_teaching_period_times,
)


def test_hhmm_validation_uses_a_24_hour_clock() -> None:
    assert is_hhmm("00:00")
    assert is_hhmm("23:59")
    assert not is_hhmm("24:00")
    assert not is_hhmm("29:59")
    assert not is_hhmm("9:00")


def test_period_extractors_reject_times_past_midnight() -> None:
    payload = json.dumps(
        {
            "sksj": [
                {"jieci": "第1节", "time": "8:00", "shichang": "45"},
                {"jieci": "第2节", "time": "23:30", "shichang": "45"},
            ]
        },
        ensure_ascii=False,
    )

    assert extract_period_times_from_xiqueer_json(payload) == {
        "1": {"start": "08:00", "end": "08:45"}
    }
    assert extract_period_times_from_text("第3节 10:00-10:45") == {
        "3": {"start": "10:00", "end": "10:45"}
    }
    assert extract_period_times_from_text("第4节 24:00-24:45") == {}


def test_normalization_filters_known_midday_short_periods() -> None:
    period_times = {
        str(index): {"start": f"{hour:02d}:00", "end": f"{hour:02d}:45"}
        for index, hour in enumerate(
            (8, 9, 10, 11, 14, 15, 16, 17, 18, 19),
            start=1,
        )
    }
    period_times.update(
        {
            "11": {"start": "13:00", "end": "13:30"},
            "12": {"start": "13:30", "end": "14:00"},
        }
    )

    normalized, meta = normalize_teaching_period_times(period_times)

    assert list(normalized) == [str(index) for index in range(1, 11)]
    assert meta["removed_midday_count"] == 2
    assert all(item["start"] not in {"13:00", "13:30"} for item in normalized.values())
