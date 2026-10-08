from __future__ import annotations

from pathlib import Path

import pytest

from henu_mcp.tools import server_impl


@pytest.fixture
def isolated_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    task_file = tmp_path / "seminar_signin_tasks.json"
    monkeypatch.setattr(server_impl, "SEMINAR_SIGNIN_TASK_FILE", task_file)
    monkeypatch.setattr(server_impl, "HenuCampusBot", object)
    return task_file


def _task(status: str) -> dict[str, object]:
    return {
        "task_id": "task-1",
        "record_id": "record-1",
        "record_type": "1",
        "status": status,
        "attempts": 0,
    }


def test_recovered_processing_claim_is_not_submitted_again(
    isolated_tasks: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_impl._save_seminar_signin_tasks([_task("processing")])
    monkeypatch.setattr(
        server_impl,
        "_build_library_bot",
        lambda *_args: pytest.fail("a recovered claim must not log in or submit"),
    )

    result = server_impl._process_seminar_signin_tasks(due_only=False)

    assert result["processed_count"] == 0
    tasks = server_impl._load_seminar_signin_tasks()
    assert tasks[0]["status"] == "uncertain"
    assert "避免重复签到" in tasks[0]["last_msg"]


def test_claim_is_durable_before_external_signin(
    isolated_tasks: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_impl._save_seminar_signin_tasks([_task("pending")])
    monkeypatch.setattr(
        server_impl,
        "_effective_profile",
        lambda: {"student_id": "student", "password": "secret"},
    )
    monkeypatch.setattr(server_impl, "_save_library_cookies", lambda *_args: None)

    observed_statuses: list[str] = []

    class Bot:
        def list_seminar_records(self, **_arguments):
            return {
                "success": True,
                "records": [{"id": "record-1", "type": "1"}],
            }

        def sign_in_seminar_record(self, _record_id: str):
            tasks = server_impl._load_seminar_signin_tasks()
            observed_statuses.append(str(tasks[0]["status"]))
            return {"success": True, "msg": "签到成功"}

        def get_cookies(self):
            return {}

    monkeypatch.setattr(server_impl, "_build_library_bot", lambda *_args: Bot())

    result = server_impl._process_seminar_signin_tasks(due_only=False)

    assert result["success_count"] == 1
    assert observed_statuses == ["processing"]
    assert server_impl._load_seminar_signin_tasks()[0]["status"] == "success"


@pytest.mark.parametrize(
    "broken",
    ("{not-json", "[]", '{"tasks": {}}', '{"tasks": [1]}'),
)
def test_existing_invalid_task_state_fails_closed_without_overwrite_or_network(
    isolated_tasks: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
) -> None:
    isolated_tasks.write_text(broken, encoding="utf-8")
    monkeypatch.setattr(
        server_impl,
        "_build_library_bot",
        lambda *_args: pytest.fail("invalid task state must fail before network"),
    )

    with pytest.raises(server_impl.SeminarTaskStateError):
        server_impl._process_seminar_signin_tasks(due_only=False)
    with pytest.raises(server_impl.SeminarTaskStateError):
        server_impl._upsert_seminar_signin_task(_task("pending"))

    assert isolated_tasks.read_text(encoding="utf-8") == broken


def test_unreadable_task_state_fails_closed(
    isolated_tasks: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_tasks.write_text('{"tasks": []}', encoding="utf-8")
    original_read_text = Path.read_text

    def unreadable(path: Path, *args, **kwargs):
        if path == isolated_tasks:
            raise OSError("simulated task read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(server_impl.SeminarTaskStateError, match="无法读取"):
        server_impl._load_seminar_signin_tasks()
