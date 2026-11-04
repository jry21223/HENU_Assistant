"""Classroom resource identities must come from unambiguous school metadata."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from campus_core import storage_paths
from campus_core.empty_classroom import query
from campus_core.empty_classroom.models import Classroom
from campus_core.resource_registry import ensure_classroom_resource, get_resource


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)


def directory(rid, name, campus='01', building='0003'):
    return dict(roomId=rid, roomName=name, campusCode=campus, buildingCode=building)


def test_blank_ids_use_exact_school_directory_and_keep_occupancy_independent(isolated):
    names=['综合教学楼101','综合教学楼102','综合教学楼103']
    source='<html>'+''.join(f'<p>教室：{name}(40)</p><table id="mytable{i}"><tr><td>'
        f'<div class="div1" id="{i}33"></div></td></tr></table>' for i,name in enumerate(names))+'</html>'
    client=SimpleNamespace(fetch_schedule_html=lambda **kw: source,
        fetch_classrooms=lambda **kw: [Classroom(str(i),name,'01','','0003','',40,'')
            for i,name in enumerate(names+['综合教学楼999'],1)],
        get_campus_map=lambda:{'01':'明伦校区'},get_building_map=lambda c:{'0003':'综合教学楼'})
    result=query.query_free_classrooms(client,'2026,0',10,3,3,'01','0003',freshness='live').to_dict()
    rooms=result['rooms']
    assert len(rooms)==3 and len({r['resourceId'] for r in rooms})==3
    assert [r['roomId'] for r in rooms]==['1','2','3']
    for room in rooms:
        assert room['location']['roomName']==room['roomName']
        assert room['canonicalName']==room['roomName']
        assert room['resourceId'].endswith(':'+room['roomId'])
        assert room['sourceMapping']['source_id']==room['roomId']
    assert result['freshness']['roomStates']['free']==names
    assert '综合教学楼999' not in result['freshness']['roomStates']['free']


@pytest.mark.parametrize('catalog', [[],
    [directory('1','综合教学楼101','02')],
    [directory('1','综合教学楼101',building='other')],
    [directory('1','综合教学楼101'),directory('2','综合教学楼101')],
    [directory('1','综合教学楼101'),directory('1','综合教学楼102')]])
def test_missing_cross_scope_and_ambiguous_catalog_do_not_invent_identity(isolated,catalog):
    rooms=[dict(roomId='',roomName='综合教学楼101',campusCode='01',buildingCode='0003')]
    query._attach_catalog_room_ids(rooms,catalog)
    query._enrich_rooms_with_resource_id(rooms,{'01':'明伦校区'},{'0003':'综合教学楼'})
    assert rooms[0]['roomId']=='' and 'resourceId' not in rooms[0]


@pytest.mark.parametrize('room_id',['','   ',None])
def test_registry_refuses_missing_school_id(isolated,room_id):
    with pytest.raises(ValueError):
        ensure_classroom_resource('01','明伦校区','0003','综合教学楼',room_id,'综合教学楼101')
    assert get_resource('henu:classroom:xk:01:0003:') is None


def test_valid_existing_school_id_is_stable_but_conflicting_name_is_refused(isolated):
    first=ensure_classroom_resource('01','明伦校区','0003','综合教学楼','001','综合教学楼101')
    assert ensure_classroom_resource('01','明伦校区','0003','综合教学楼','001','综合教学楼101')==first
    with pytest.raises(ValueError):
        ensure_classroom_resource('01','明伦校区','0003','综合教学楼','001','综合教学楼102')
    assert get_resource(first.resource_id).location['roomName']=='综合教学楼101'


@pytest.mark.parametrize('bad', ['blank','wrong_id','wrong_room','wrong_scope'])
def test_legacy_cached_identity_cannot_replay_another_room_metadata(isolated,bad):
    scope=dict(termCode='2026,0',week=10,dayOfWeek=3,period=3,campusCode='01',buildingCode='0003')
    room=dict(roomId='001',roomName='综合教学楼101',campusCode='01',buildingCode='0003',
        resourceId='henu:classroom:xk:01:0003:001',canonicalName='综合教学楼101',aliases=['101'],
        sourceMapping={'system':'xk'},location=dict(roomName='综合教学楼101',campusCode='01',buildingCode='0003'))
    if bad=='blank':room['roomId']='';room['resourceId']='henu:classroom:xk:01:0003:'
    if bad=='wrong_id':room['resourceId']='henu:classroom:xk:01:0003:other'
    if bad=='wrong_room':room['location']['roomName']='综合教学楼999'
    if bad=='wrong_scope':room['location']['buildingCode']='other'
    cached=dict(query=scope,total=1,rooms=[room],freshness=dict(fetchedAt=datetime.now(timezone.utc).isoformat()))
    before=copy.deepcopy(cached)
    actual=query._build_result_from_cache(cached,'cache_first',False,False,300,scope).to_dict()
    assert actual['total']==1 and actual['rooms'][0]['roomName']=='综合教学楼101'
    assert not any(k in actual['rooms'][0] for k in ['resourceId','canonicalName','aliases','location','sourceMapping'])
    assert cached==before
