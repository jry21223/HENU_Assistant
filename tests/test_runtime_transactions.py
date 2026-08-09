from __future__ import annotations

import multiprocessing
from pathlib import Path


def _save_group(root: str, barrier, index: int) -> None:
    from henu_mcp import api
    from henu_mcp.runtime import FixedFilesystemRuntime

    barrier.wait(timeout=10)
    with FixedFilesystemRuntime(Path(root)).activate("shared"):
        result = api.seminar_group(
            action="save",
            group_name=f"group-{index}",
            member_ids=f"{index}01,{index}02,{index}03",
        )
    if not result.get("success"):
        raise RuntimeError(str(result))


def test_multiprocess_profile_updates_do_not_lose_fields(tmp_path: Path) -> None:
    from henu_mcp import api
    from henu_mcp.runtime import FixedFilesystemRuntime

    context = multiprocessing.get_context("spawn")
    process_count = 4
    barrier = context.Barrier(process_count)
    processes = [
        context.Process(target=_save_group, args=(str(tmp_path), barrier, index))
        for index in range(process_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert [process.exitcode for process in processes] == [0] * process_count
    with FixedFilesystemRuntime(tmp_path).activate("shared"):
        result = api.seminar_group(action="list")

    assert result["success"] is True
    assert {group["group_name"] for group in result["groups"]} == {
        f"group-{index}" for index in range(process_count)
    }


def test_temporary_runtime_isolates_scope_state() -> None:
    from henu_mcp import api
    from henu_mcp.runtime import TemporaryFilesystemRuntime

    with TemporaryFilesystemRuntime() as runtime:
        with runtime.activate("student-a"):
            saved = api.seminar_group(
                action="save",
                group_name="only-a",
                member_ids="101,102,103",
            )
            assert saved["success"] is True

        with runtime.activate("student-b"):
            assert api.seminar_group(action="list")["groups"] == []
            saved = api.seminar_group(
                action="save",
                group_name="only-b",
                member_ids="201,202,203",
            )
            assert saved["success"] is True

        with runtime.activate("student-a"):
            groups = api.seminar_group(action="list")["groups"]

    assert [group["group_name"] for group in groups] == ["only-a"]

