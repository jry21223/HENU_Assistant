from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from campus_core.empty_classroom.client import EmptyClassroomClient
from campus_core.empty_classroom.models import Term


def classroom_client(context):
    xk = SimpleNamespace(
        session=Mock(),
        base_url="https://xk.henu.edu.cn",
        fetch_user_context=Mock(return_value=context),
    )
    return EmptyClassroomClient(xk), xk


def test_current_term_comes_from_authenticated_school_context_not_latest_list(
    monkeypatch,
):
    client, _ = classroom_client(
        {"authenticated": True, "current_xn": "2026", "current_xq": "0"}
    )
    monkeypatch.setattr(
        client,
        "fetch_terms",
        lambda: [
            Term("2026,1", "下一学期", "2026", "1"),
            Term("2026,0", "2026-2027学年第一学期", "2026", "0"),
        ],
    )
    result = client.resolve_term()
    assert result["success"] is True
    assert result["term_code"] == "2026,0"
    assert result["term_name"] == "2026-2027学年第一学期"
    assert result["source"] == "school_current"


def test_explicit_historical_term_does_not_get_overwritten():
    client, xk = classroom_client(
        {"authenticated": True, "current_xn": "2026", "current_xq": "0"}
    )
    assert client.resolve_term("2025,1") == {
        "success": True,
        "term_code": "2025,1",
        "source": "explicit",
    }
    xk.fetch_user_context.assert_not_called()


@pytest.mark.parametrize(
    "context",
    [
        {"authenticated": False, "current_xn": "2026", "current_xq": "0"},
        {"authenticated": True},
        {"authenticated": True, "current_xn": "2026", "current_xq": "0"},
    ],
)
def test_unverified_current_term_does_not_guess_from_the_year_or_first_option(
    monkeypatch, context
):
    client, _ = classroom_client(context)
    monkeypatch.setattr(
        client, "fetch_terms", lambda: [Term("2026,1", "下一学期", "2026", "1")]
    )
    result = client.resolve_term()
    assert result["success"] is False
    assert result["missing_params"] == ["term_code"]


def test_query_without_term_uses_current_term_and_only_reports_remaining_gaps(
    monkeypatch,
):
    from henu_mcp.tools import server_impl

    client, _ = classroom_client(
        {"authenticated": True, "current_xn": "2026", "current_xq": "0"}
    )
    monkeypatch.setattr(
        client,
        "fetch_terms",
        lambda: [Term("2026,0", "2026-2027学年第一学期", "2026", "0")],
    )
    monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: client)
    query = Mock(
        return_value=SimpleNamespace(
            total=1, to_dict=lambda: {"rooms": [{"roomId": "A", "roomName": "教室A"}]}
        )
    )
    monkeypatch.setattr(server_impl, "query_free_classrooms", query)
    result = server_impl.empty_classroom_query(
        campus_code="02", week=5, day_of_week=5, period=3
    )
    assert result["success"] is True
    assert query.call_args.kwargs["term_code"] == "2026,0"
    assert query.call_args.kwargs["campus_code"] == "02"
    assert result["term_context"]["source"] == "school_current"
    missing = server_impl.empty_classroom_query(campus_code="02")
    assert missing["missing_params"] == ["week", "period"]
    assert missing["term_context"]["term_code"] == "2026,0"


def test_sync_and_day_matrix_resolve_term_without_a_manual_code(monkeypatch):
    from henu_mcp.tools import server_impl

    client, _ = classroom_client(
        {"authenticated": True, "current_xn": "2026", "current_xq": "0"}
    )
    monkeypatch.setattr(
        client, "fetch_terms", lambda: [Term("2026,0", "当前学期", "2026", "0")]
    )
    monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: client)
    sync = Mock(
        return_value=SimpleNamespace(
            sync_status="skipped", error="", to_dict=lambda: {}
        )
    )
    monkeypatch.setattr(server_impl, "_sync_schedule_impl", sync)
    result = server_impl.empty_classroom_sync(campus_code="02", building_code="0007")
    assert result["success"] is True
    assert sync.call_args.kwargs["term_code"] == "2026,0"
    query = Mock(return_value=SimpleNamespace(total=0, rooms=[]))
    monkeypatch.setattr(server_impl, "query_free_classrooms", query)
    result = server_impl.empty_classroom_query(
        view="day_matrix", week=5, campus_code="02"
    )
    assert result["success"] is True
    assert len(result["data"]["periods"]) == 5
    assert all(c.kwargs["term_code"] == "2026,0" for c in query.call_args_list)


def test_failed_term_resolution_stops_before_querying_rooms(monkeypatch):
    from henu_mcp.tools import server_impl

    client, _ = classroom_client({"authenticated": True})
    monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: client)
    query = Mock()
    monkeypatch.setattr(server_impl, "query_free_classrooms", query)
    result = server_impl.empty_classroom_query(
        week=5, day_of_week=5, period=3, campus_code="02"
    )
    assert result["error_code"] == "current_term_unavailable"
    query.assert_not_called()
