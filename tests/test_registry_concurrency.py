from __future__ import annotations

import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from campus_core import storage_paths
from campus_core.resource_registry.models import ResourceRecord


def _record(index: int) -> ResourceRecord:
    return ResourceRecord(
        resource_id=f"henu:campus:{index}",
        resource_type="campus",
        display_name=f"Campus {index}",
        canonical_name=f"campus-{index}",
        aliases=[f"alias-{index}"],
        source={"system": "fixture", "source_id": str(index)},
        location={"campusCode": str(index)},
    )


def _process_upserts(root: str, barrier, worker: int, count: int) -> None:
    from campus_core import storage_paths as child_storage_paths
    from campus_core.resource_registry.registry import upsert_resource

    child_storage_paths.set_base_dir(Path(root))
    barrier.wait(timeout=10)
    for offset in range(count):
        upsert_resource(_record(worker * count + offset))


def test_threaded_upserts_preserve_resources_and_all_indexes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from campus_core.resource_registry import storage
    from campus_core.resource_registry.registry import upsert_resource

    real_read = storage._read_json

    def slow_read(path: Path):
        payload = real_read(path)
        time.sleep(0.005)
        return payload

    monkeypatch.setattr(storage, "_read_json", slow_read)
    records = [_record(index) for index in range(8)]
    barrier = threading.Barrier(len(records))

    def upsert(record: ResourceRecord) -> None:
        barrier.wait(timeout=5)
        upsert_resource(record)

    with storage_paths.activated_base_dir(tmp_path):
        threads = [threading.Thread(target=upsert, args=(record,)) for record in records]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        resources = storage.load_resources()
        aliases = storage.load_alias_index()
        mappings = storage.load_source_mappings()

    assert set(resources) == {record.resource_id for record in records}
    for index, record in enumerate(records):
        assert aliases[f"alias-{index}"] == [record.resource_id]
        assert mappings["fixture"][str(index)] == record.resource_id


def test_multiprocess_upserts_do_not_lose_records(tmp_path: Path) -> None:
    from campus_core.resource_registry import storage

    context = multiprocessing.get_context("spawn")
    process_count = 4
    records_per_process = 5
    barrier = context.Barrier(process_count)
    processes = [
        context.Process(
            target=_process_upserts,
            args=(str(tmp_path), barrier, worker, records_per_process),
        )
        for worker in range(process_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)

    assert [process.exitcode for process in processes] == [0] * process_count
    with storage_paths.activated_base_dir(tmp_path):
        resources = storage.load_resources()
        aliases = storage.load_alias_index()
        mappings = storage.load_source_mappings()

    expected_count = process_count * records_per_process
    assert len(resources) == expected_count
    assert len(aliases) >= expected_count
    assert len(mappings["fixture"]) == expected_count


def test_resource_and_indexes_commit_as_one_snapshot(tmp_path: Path, monkeypatch) -> None:
    from campus_core.resource_registry import storage
    from campus_core.resource_registry.registry import upsert_resource

    writes: list[tuple[Path, dict]] = []
    real_write = storage._write_json

    def record_write(path: Path, payload: dict) -> None:
        writes.append((path, json.loads(json.dumps(payload))))
        real_write(path, payload)

    monkeypatch.setattr(storage, "_write_json", record_write)
    record = _record(42)
    with storage_paths.activated_base_dir(tmp_path):
        upsert_resource(record)

    assert len(writes) == 1
    path, state = writes[0]
    assert path.name == "registry_state.json"
    assert state["resources"][record.resource_id]["resourceId"] == record.resource_id
    assert state["aliases"]["alias-42"] == [record.resource_id]
    assert state["source_mappings"]["fixture"]["42"] == record.resource_id


@pytest.mark.parametrize(
    "broken_state",
    (
        "{not-json",
        json.dumps({"version": 999, "resources": {"new": {"resourceId": "new"}}}),
    ),
)
def test_existing_broken_canonical_state_never_falls_back_or_gets_overwritten(
    tmp_path: Path,
    broken_state: str,
) -> None:
    from campus_core.resource_registry import storage
    from campus_core.resource_registry.registry import upsert_resource

    with storage_paths.activated_base_dir(tmp_path):
        registry_dir = storage._registry_dir()
        state_path = registry_dir / "registry_state.json"
        state_path.write_text(broken_state, encoding="utf-8")
        (registry_dir / "resources.json").write_text(
            json.dumps({"legacy": {"resourceId": "legacy"}}),
            encoding="utf-8",
        )

        with pytest.raises(storage.RegistryStateError):
            storage.load_resources()
        with pytest.raises(storage.RegistryStateError):
            upsert_resource(_record(77))

        assert state_path.read_text(encoding="utf-8") == broken_state


def test_existing_unreadable_canonical_state_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from campus_core.resource_registry import storage

    with storage_paths.activated_base_dir(tmp_path):
        state_path = storage._state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(storage._empty_state()), encoding="utf-8")
        real_read_text = Path.read_text

        def fail_state_read(path: Path, *args, **kwargs):
            if path == state_path:
                raise OSError("simulated canonical read failure")
            return real_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_state_read)
        with pytest.raises(storage.RegistryStateError, match="cannot read canonical"):
            storage.load_resources()


@pytest.mark.parametrize("broken_legacy", ("{not-json", "[]"))
def test_broken_legacy_state_cannot_create_a_partial_canonical_migration(
    tmp_path: Path,
    broken_legacy: str,
) -> None:
    from campus_core.resource_registry import storage
    from campus_core.resource_registry.registry import upsert_resource

    with storage_paths.activated_base_dir(tmp_path):
        registry_dir = storage._registry_dir()
        (registry_dir / "resources.json").write_text(broken_legacy, encoding="utf-8")
        (registry_dir / "aliases.json").write_text(
            json.dumps({"legacy": ["legacy-id"]}),
            encoding="utf-8",
        )

        with pytest.raises(storage.RegistryStateError, match="legacy registry"):
            storage.load_resources()
        with pytest.raises(storage.RegistryStateError, match="legacy registry"):
            upsert_resource(_record(78))

        assert not (registry_dir / "registry_state.json").exists()


def test_unreadable_legacy_state_cannot_create_a_canonical_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from campus_core.resource_registry import storage

    with storage_paths.activated_base_dir(tmp_path):
        resources_path = storage._resources_path()
        resources_path.parent.mkdir(parents=True, exist_ok=True)
        resources_path.write_text("{}", encoding="utf-8")
        real_read_text = Path.read_text

        def fail_legacy_read(path: Path, *args, **kwargs):
            if path == resources_path:
                raise OSError("simulated legacy read failure")
            return real_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_legacy_read)
        with pytest.raises(storage.RegistryStateError, match="cannot read legacy"):
            storage.load_resources()
        assert not storage._state_path().exists()


def test_upsert_replaces_derived_alias_and_source_indexes(tmp_path: Path) -> None:
    from campus_core.resource_registry import storage
    from campus_core.resource_registry.registry import upsert_resource

    resource_id = "henu:campus:rename"
    original = ResourceRecord(
        resource_id=resource_id,
        resource_type="campus",
        display_name="Old Campus",
        canonical_name="old-campus",
        aliases=["old-alias"],
        source={"system": "fixture", "source_id": "old-source"},
    )
    replacement = ResourceRecord(
        resource_id=resource_id,
        resource_type="campus",
        display_name="New Campus",
        canonical_name="new-campus",
        aliases=["new-alias"],
        source={"system": "fixture", "source_id": "new-source"},
    )

    with storage_paths.activated_base_dir(tmp_path):
        upsert_resource(original)
        upsert_resource(replacement)

        assert storage.lookup_by_alias("old-alias") == []
        assert storage.lookup_by_alias("old-campus") == []
        assert storage.resolve_source_id("fixture", "old-source") == ""
        assert storage.lookup_by_alias("new-alias") == [resource_id]
        assert storage.lookup_by_alias("new-campus") == [resource_id]
        assert storage.resolve_source_id("fixture", "new-source") == resource_id
