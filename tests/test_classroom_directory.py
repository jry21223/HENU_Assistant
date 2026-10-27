"""School directory identity never supplies missing course occupancy."""
import copy
import io
import os
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from campus_core.empty_classroom.models import Classroom
from campus_core.empty_classroom import query


@pytest.fixture
def school_client():
    empty_slots = ''.join(f'<div class="div1" id="0{day}{period}"></div>'
                          for day in range(1, 8) for period in range(1, 6))
    return SimpleNamespace(
        fetch_schedule_html=lambda **kw: f'<html><p>教室：金明综合楼6101(178)</p><table id="mytable0"><tr><td>{empty_slots}</td></tr></table></html>',
        fetch_classrooms=lambda **kw: [
            Classroom('0000213', '金明综合楼6101', '02', '', '21', '', 178, '多媒体教室'),
            Classroom('0000217', '金明综合楼6305', '02', '', '21', '', 300, '多媒体教室'),
            Classroom('0000222', '金明综合楼6405', '02', '', '21', '', 300, '多媒体教室')],
        get_campus_map=lambda: {'02': '金明校区'},
        get_building_map=lambda code: {'21': '金明综合楼6'})


def test_query_preserves_school_ids_without_treating_uncovered_rooms_as_free(tmp_path, monkeypatch, school_client):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    client = school_client
    args = dict(client=client, term_code='2026,0', week=9, day_of_week=2, period=2,
                campus_code='02', building_code='21')
    live = query.query_free_classrooms(**args, freshness='live').to_dict()
    assert [r['roomName'] for r in live['rooms']] == ['金明综合楼6101']
    proof = live['freshness']['roomStates']
    assert proof['free'] == ['金明综合楼6101']
    assert proof['occupied'] == []
    assert [(r['roomName'], r['roomId']) for r in proof['catalogUncovered']] == [
        ('金明综合楼6305', '0000217'), ('金明综合楼6405', '0000222')]
    cached = query.query_free_classrooms(**args, freshness='cache_first').to_dict()
    assert cached['freshness']['roomStates']['catalogUncovered'] == proof['catalogUncovered']


def test_optional_directory_failure_does_not_discard_valid_course_result(tmp_path, monkeypatch, school_client):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    def malformed_directory(**kwargs):
        raise ValueError('malformed school room capacity')
    school_client.fetch_classrooms = malformed_directory
    row = query.query_free_classrooms(client=school_client, term_code='2026,0', week=9,
        day_of_week=2, period=2, campus_code='02', building_code='21', freshness='live').to_dict()
    assert row['freshness']['roomStates']['complete'] is True
    assert row['freshness']['roomStates']['free'] == ['金明综合楼6101']
    assert row['freshness']['roomStates']['catalogUncovered'] == []


def test_mixed_directory_is_not_treated_as_same_scope_proof(tmp_path, monkeypatch, school_client):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    original = school_client.fetch_classrooms
    school_client.fetch_classrooms = lambda **kw: original(**kw) + [
        # Real client stamps requested scope onto XML entries. The names must
        # independently identify the Jinming building rather than trusting it.
        Classroom('foreign', '明伦综合楼101', '02', '', '21', '', 20, '多媒体教室')]
    row = query.query_free_classrooms(client=school_client, term_code='2026,0', week=9,
        day_of_week=2, period=2, campus_code='02', building_code='21', freshness='live').to_dict()
    assert row['freshness']['roomStates']['free'] == ['金明综合楼6101']
    assert row['freshness']['roomStates']['catalogUncovered'] == []


def directory_result():
    from test_classroom_states import result
    row = result()
    row['query'].update(buildingCode='21', buildingName='金明综合楼6')
    proof = row['freshness']['roomStates']
    proof['scope'] = {**proof['scope'], 'buildingCode': '21'}
    proof['free'] = ['金明综合楼6101']
    proof['occupied'] = ['金明综合楼6102']
    proof['catalogUncovered'] = [
        {'roomName': '金明综合楼6305', 'roomId': '0000217', 'campusCode': '02', 'buildingCode': '21'},
        {'roomName': '金明综合楼6405', 'roomId': '0000222', 'campusCode': '02', 'buildingCode': '21'}]
    row['rooms'] = [{'roomName': '金明综合楼6101', 'campusCode': '02', 'buildingCode': '21'}]
    return row


def test_live_card_and_image_explain_matched_identity_without_inventing_course_state():
    from henu_plugin.classroom_map import classroom_summary, render_classroom_map
    row = directory_result()
    text = '\n'.join(classroom_summary(row, image_ok=True).pages())
    assert '6305、6405' in text
    assert '课表未覆盖' in text
    image = Image.open(io.BytesIO(render_classroom_map(row)))
    assert image.size == (1280, 1550)
    assert '6305、6405' in image.info['CatalogUncovered']
    assert image.getpixel((195, 1274)) == (35, 155, 112)  # 6101 stays free
    assert image.getpixel((775, 760)) == (225, 230, 236)  # 6305 stays unknown


@pytest.mark.parametrize('invalid', ['stale', 'scope', 'room_scope', 'room_number', 'id_conflict'])
def test_bad_or_expired_catalog_does_not_claim_identity_match(invalid):
    from henu_plugin.classroom_map import classroom_summary
    row = copy.deepcopy(directory_result())
    proof = row['freshness']['roomStates']
    if invalid == 'stale':
        row['freshness']['ageSeconds'] = 301
    elif invalid == 'scope':
        proof['scope']['buildingCode'] = '17'
    elif invalid == 'room_scope':
        for item in proof['catalogUncovered']:
            item['campusCode'] = '01'
    elif invalid == 'room_number':
        for item in proof['catalogUncovered']:
            item['roomName'] = '金明综合楼2306'
    else:
        proof['catalogUncovered'][1]['roomId'] = '0000217'
    text = '\n'.join(classroom_summary(row, image_ok=True).pages())
    assert '教室已确认' not in text


def test_fresh_school_result_stays_fresh_across_worker_timezones(tmp_path, monkeypatch, school_client):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    args = dict(client=school_client, term_code='2026,0', week=9, day_of_week=2,
                period=3, campus_code='02', building_code='21')
    try:
        with monkeypatch.context() as clock:
            clock.setenv('TZ', 'Asia/Shanghai')
            time.tzset()
            live = query.query_free_classrooms(**args, freshness='live').to_dict()
            clock.setenv('TZ', 'UTC')
            time.tzset()
            cached = query.query_free_classrooms(**args, freshness='cache_first').to_dict()
            assert cached['freshness']['stale'] is False
            assert 0 <= cached['freshness']['ageSeconds'] <= 300
            assert cached['freshness']['roomStates']['catalogUncovered'] == live['freshness']['roomStates']['catalogUncovered']
    finally:
        time.tzset()


def test_legacy_unzoned_cache_is_unverified_in_cache_only_and_refreshed_in_cache_first(tmp_path, monkeypatch, school_client):
    from campus_core import storage_paths
    from campus_core.empty_classroom import storage
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    args = dict(client=school_client, term_code='2026,0', week=9, day_of_week=2,
                period=3, campus_code='02', building_code='21')
    live = query.query_free_classrooms(**args, freshness='live').to_dict()
    live['freshness']['fetchedAt'] = live['freshness']['fetchedAt'].split('+')[0]
    key = storage.compute_query_hash('2026,0', 9, 2, 3, '02', '21')
    storage.save_query_cache(key, live)
    # Rebuilding result metadata must preserve the age of the actual school
    # timetable, not renew its lifetime using the newer result-cache file.
    source_time = time.time() - 120
    for path in tmp_path.rglob('*'):
        if path.is_file() and any(part in path.parts for part in ['parsed_schedule', 'schedule_html']):
            os.utime(path, (source_time, source_time))
    only = query.query_free_classrooms(**args, freshness='cache_only').to_dict()
    assert only['freshness']['stale'] is True
    refreshed = query.query_free_classrooms(**args, freshness='cache_first').to_dict()
    assert refreshed['freshness']['stale'] is False
    assert refreshed['freshness']['fetchedAt'].endswith('+00:00')
    assert 119 <= refreshed['freshness']['ageSeconds'] <= 125
