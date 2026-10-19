from __future__ import annotations

import pytest

from campus_core.empty_classroom.models import Building
from henu_mcp.tools import server_impl as api


class SchoolMetadata:
    def __init__(self):
        self.requests = []

    def fetch_buildings(self, campus_code):
        self.requests.append(('buildings', campus_code))
        return [Building('0013', '十号楼', '01')] if campus_code == '01' else []

    def get_campus_map(self):
        return {}

    def get_building_map(self, campus_code):
        return {}

    def fetch_classrooms(self, campus_code, building_code, **kwargs):
        self.requests.append(('classrooms', campus_code, building_code))
        return []


@pytest.fixture
def school(monkeypatch):
    boundary = SchoolMetadata()
    monkeypatch.setattr(api, '_get_empty_classroom_client', lambda: boundary)
    return boundary


def test_building_without_campus_is_rejected(school):
    result = api.empty_classroom_query(view='classrooms', building_code='0013')

    assert result.get('code') == 'building_requires_campus'
    assert school.requests == []


def test_building_from_another_campus_is_rejected(school):
    result = api.empty_classroom_query(
        view='classrooms', campus_code='02', building_code='0013'
    )

    assert result.get('code') == 'building_not_in_campus'
    assert not any(r[0] == 'classrooms' for r in school.requests)


def test_explicit_campus_code_resolves_building_name(school, monkeypatch, tmp_path):
    from campus_core.resource_registry import storage
    from campus_core.resource_registry.models import ResourceRecord
    from campus_core.resource_registry.registry import upsert_resource

    monkeypatch.setattr(storage, '_resources_path', lambda: tmp_path / 'resources.json')
    monkeypatch.setattr(storage, '_aliases_path', lambda: tmp_path / 'aliases.json')
    monkeypatch.setattr(storage, '_sync_state_path', lambda: tmp_path / 'sync.json')
    monkeypatch.setattr(storage, '_source_mappings_path', lambda: tmp_path / 'sources.json')
    (tmp_path / 'sync.json').write_text('{"seed_classrooms": {"status": "loaded"}}')
    upsert_resource(ResourceRecord(
        resource_id='xk:building:01:0013', resource_type='building',
        display_name='十号楼', canonical_name='10号楼', aliases=['十号楼', '10号楼'],
        location={'campusCode': '01', 'buildingCode': '0013'}, source={'system': 'xk'},
    ))

    result = api.empty_classroom_query(
        view='classrooms', campus_code='01', building_text='十号楼'
    )

    assert result['success'] is True
    assert ('classrooms', '01', '0013') in school.requests


@pytest.mark.parametrize('building_code', ['', '0013'])
def test_explicit_building_filter_reaches_school(school, building_code):
    result = api.empty_classroom_query(
        view='classrooms', campus_code='01', building_code=building_code
    )

    assert result['success'] is True
    assert ('classrooms', '01', building_code) in school.requests
    if not building_code:
        assert not any(r[0] == 'buildings' for r in school.requests)


def test_building_metadata_failure_does_not_leak_details(school):
    def fail(_campus):
        raise OSError('synthetic-secret-in-school-error')

    school.fetch_buildings = fail
    result = api.empty_classroom_query(
        view='classrooms', campus_code='01', building_code='0013'
    )

    assert result['success'] is False
    assert result['code'] == 'building_validation_failed'
    assert 'synthetic-secret' not in str(result)
    assert not any(r[0] == 'classrooms' for r in school.requests)


def test_building_name_ignores_other_campus_alias(school, monkeypatch, tmp_path):
    from campus_core.resource_registry import storage
    from campus_core.resource_registry.models import ResourceRecord
    from campus_core.resource_registry.registry import upsert_resource

    for name, file in [('_resources_path', 'resources.json'), ('_aliases_path', 'aliases.json'),
                       ('_sync_state_path', 'sync.json'), ('_source_mappings_path', 'sources.json')]:
        monkeypatch.setattr(storage, name, lambda file=file: tmp_path / file)
    (tmp_path / 'sync.json').write_text('{"seed_classrooms": {"status": "loaded"}}')
    for campus, code in [('02', 'other'), ('01', '0013')]:
        upsert_resource(ResourceRecord(
            resource_id=f'henu:building:{campus}:{code}', resource_type='building',
            display_name='同名楼', canonical_name='同名楼', aliases=['同名楼'],
            location={'campusCode': campus, 'buildingCode': code}, source={'system': 'xk'},
        ))
    result = api.empty_classroom_query(view='classrooms', campus_code='01', building_text='同名楼')
    assert result['success'] is True
    assert ('classrooms', '01', '0013') in school.requests


def test_cache_only_building_query_never_contacts_school(monkeypatch, tmp_path):
    from campus_core import storage_paths
    from campus_core.resource_registry.models import ResourceRecord
    from campus_core.resource_registry.registry import upsert_resource

    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    upsert_resource(ResourceRecord(
        resource_id='henu:building:01:0013', resource_type='building',
        display_name='十号楼', canonical_name='10号楼', aliases=['10号楼'],
        location={'campusCode': '01', 'buildingCode': '0013'}, source={'system': 'xk'},
    ))
    monkeypatch.setattr(api, '_get_empty_classroom_client', lambda: pytest.fail('school login'))
    result = api.empty_classroom_query(
        view='free', term_code='2026,1', week=1, day_of_week=1, period=1,
        campus_code='01', building_code='0013', freshness='cache_only',
    )
    assert result['success'] is True
    assert result['freshness']['liveChecked'] is False


def test_cache_only_missing_building_evidence_fails_locally(monkeypatch, tmp_path):
    from campus_core import storage_paths

    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    monkeypatch.setattr(api, '_get_empty_classroom_client', lambda: pytest.fail('school login'))
    result = api.empty_classroom_query(
        view='free', term_code='2026,1', week=1, day_of_week=1, period=1,
        campus_code='01', building_code='not-cached', freshness='cache_only',
    )
    assert result['code'] == 'building_validation_unavailable'


def test_detected_campus_in_building_text_cannot_override_explicit_campus(school, monkeypatch, tmp_path):
    from campus_core import storage_paths
    from campus_core.resource_registry.models import ResourceRecord
    from campus_core.resource_registry.registry import upsert_resource

    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    state = tmp_path / 'data/shared/resource_registry/sync_state.json'
    state.parent.mkdir(parents=True)
    state.write_text('{"seed_classrooms": {"status": "loaded"}}')
    upsert_resource(ResourceRecord(
        resource_id='henu:building:02:0013', resource_type='building',
        display_name='金明校区独有楼', canonical_name='独有楼', aliases=['独有楼'],
        location={'campusCode': '02', 'buildingCode': '0013'}, source={'system': 'xk'},
    ))
    result = api.empty_classroom_query(
        view='classrooms', campus_code='01', building_text='金明校区独有楼'
    )
    assert result['success'] is False
    assert not any(r[0] == 'classrooms' for r in school.requests)
