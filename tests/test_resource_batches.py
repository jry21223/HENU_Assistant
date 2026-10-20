"""Public registry batch contract: indexes, failure atomicity and competing writers."""
import multiprocessing
from pathlib import Path

import pytest

from campus_core import storage_paths
from campus_core.resource_registry import ResourceRecord, get_resource, get_stats, search_resources, upsert_resource, upsert_resources
from campus_core.resource_registry.storage import RegistryStateError, resolve_source_id


def record(index, alias=None):
    return ResourceRecord(resource_id=f'fixture:campus:{index}', resource_type='campus',
                          display_name=f'Campus {index}', canonical_name=f'campus-{index}',
                          aliases=[alias or f'alias-{index}'], source={'system':'fixture','source_id':str(index)})


def writer(root, gate, offset, batch):
    storage_paths.set_base_dir(Path(root))
    gate.wait(timeout=15)
    records = [record(i) for i in range(offset,offset+4)]
    if batch:
        upsert_resources(records)
    else:
        for row in records:
            upsert_resource(row)


def test_batch_replaces_old_aliases_without_losing_other_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_paths,'_BASE_DIR',tmp_path)
    upsert_resources([record(1,'old-alias'), record(2,'shared-alias')])
    upsert_resources([record(1,'new-alias'), record(3,'shared-alias')])
    assert not search_resources(query='old-alias')
    assert [r.resource_id for r in search_resources(query='new-alias')]==['fixture:campus:1']
    assert {r.resource_id for r in search_resources(query='shared-alias')}=={'fixture:campus:2','fixture:campus:3'}
    assert resolve_source_id('fixture','1')=='fixture:campus:1'
    assert get_stats()['total']==3


def test_failed_batch_preserves_the_previous_complete_snapshot(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(storage_paths,'_BASE_DIR',tmp_path)
    upsert_resource(record(1))
    def fail(*args):
        raise OSError('synthetic rename failure')
    with monkeypatch.context() as failure:
        failure.setattr(os,'replace',fail)
        with pytest.raises(OSError):
            upsert_resources([record(2), record(3)])
    assert get_stats()['total']==1
    assert get_resource('fixture:campus:1')
    assert get_resource('fixture:campus:2') is None
    assert not search_resources(query='alias-3')
    assert resolve_source_id('fixture','3')==''


def test_batches_and_single_writers_share_cross_process_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_paths,'_BASE_DIR',tmp_path)
    ctx = multiprocessing.get_context('spawn')
    gate = ctx.Barrier(3)
    jobs=[ctx.Process(target=writer,args=(str(tmp_path),gate,i*4,i!=1)) for i in range(3)]
    for job in jobs:job.start()
    for job in jobs:job.join(timeout=25)
    assert [p.exitcode for p in jobs]==[0,0,0]
    assert get_stats()['total']==12
    for i in range(12):
        rid=f'fixture:campus:{i}'
        assert get_resource(rid)
        assert rid in [r.resource_id for r in search_resources(query=f'alias-{i}')]
        assert resolve_source_id('fixture',str(i))==rid


def test_batch_does_not_replace_a_corrupt_authoritative_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_paths,'_BASE_DIR',tmp_path)
    folder=tmp_path/'data/shared/resource_registry';folder.mkdir(parents=True)
    state=folder/'registry_state.json';state.write_text('{broken')
    with pytest.raises(RegistryStateError):upsert_resources([record(1),record(2)])
    assert state.read_text()=='{broken'


def test_repeated_resource_in_a_batch_keeps_its_final_aliases(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_paths,'_BASE_DIR',tmp_path)
    upsert_resource(record(1,'original-alias'))
    upsert_resources([record(1,'temporary-alias'),record(2,'shared-alias'),record(1,'final-alias')])
    assert not search_resources(query='original-alias')
    assert not search_resources(query='temporary-alias')
    assert [r.resource_id for r in search_resources(query='final-alias')]==['fixture:campus:1']
    assert get_stats()['total']==2
