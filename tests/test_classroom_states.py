"""Public query-to-image contracts for complete, stale and partial room states."""
import copy
import io
from types import SimpleNamespace

from PIL import Image
import pytest

from henu_plugin.classroom_map import build_classroom_image, render_classroom_map


def result():
    scope = {'termCode': '2026,1', 'week': 9, 'dayOfWeek': 2, 'period': 2,
             'campusCode': '02', 'buildingCode': '18'}
    return {'success': True, '_resolved_tool_name': 'empty_classroom_query',
            'query': {**scope, 'campusName': '金明校区', 'buildingName': '金明综合楼3'},
            'rooms': [{'roomName': '金明综合楼3101', 'campusCode': '02', 'buildingCode': '18'}],
            'total': 1, 'freshness': {'liveChecked': True, 'stale': False,
                'fetchedAt': '2026-10-27T04:00:00', 'ageSeconds': 0, 'ttlSeconds': 300,
                'roomStates': {'complete': True, 'scope': scope,
                    'free': ['金明综合楼3101'], 'occupied': ['金明综合楼3102']}}}


def test_complete_result_uses_green_only_for_free_and_gray_for_other_rooms():
    image = Image.open(io.BytesIO(render_classroom_map(result())))
    assert image.size == (1280, 1550)
    assert image.getpixel((195, 1180)) == (35, 155, 112)  # 3101: free
    assert image.getpixel((195, 1274)) == (225, 230, 236)  # 3102: occupied
    assert image.getpixel((340, 1180)) == (225, 230, 236)  # 3103: no school coverage


@pytest.mark.parametrize('change', ['stale', 'partial', 'refreshFailed', 'age', 'missing_coverage', 'wrong_scope', 'truncated', 'conflict'])
def test_unreliable_data_never_marks_rooms_free_or_occupied(change):
    row = result()
    if change in {'stale', 'partial', 'refreshFailed'}:
        row['freshness'][change] = True
    elif change == 'age':
        row['freshness']['ageSeconds'] = 301
    elif change == 'missing_coverage':
        row['freshness'].pop('roomStates')
    elif change == 'wrong_scope':
        row['freshness']['roomStates']['scope']['buildingCode'] = '17'
    elif change == 'truncated':
        row['total'] = 2
    else:
        row['freshness']['roomStates']['occupied'].append('金明综合楼3101')
    image = Image.open(io.BytesIO(render_classroom_map(row)))
    assert image.getpixel((195, 1180)) == (225, 230, 236)
    assert image.getpixel((195, 1274)) == (225, 230, 236)


def test_failed_query_does_not_fabricate_a_status_image():
    row = result()
    row['success'] = False
    assert build_classroom_image(row) is None


def test_query_emits_explicit_unfiltered_room_coverage_and_preserves_it_in_cache(tmp_path, monkeypatch):
    from campus_core import storage_paths
    from campus_core.empty_classroom import query, storage
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    parsed = {'classrooms': [{'room_name': '金明综合楼3101', 'room_id': 'r1', 'capacity': 20},
                             {'room_name': '金明综合楼3102', 'room_id': 'r2', 'capacity': 90}],
              'cells': [{'roomName': '金明综合楼3102', 'roomId': 'r2', 'dayOfWeek': 2,
                         'period': 2, 'weeks': [9]}]}
    def sync(**kwargs):
        storage.save_parsed_schedule(parsed, '2026,1', '02', '18')
        return SimpleNamespace(sync_status='success', error='')
    monkeypatch.setattr(query, 'sync_schedule', sync)
    client = SimpleNamespace(get_campus_map=lambda: {'02': '金明校区'},
                             get_building_map=lambda code: {'18': '金明综合楼3'})
    args = dict(client=client, term_code='2026,1', week=9, day_of_week=2, period=2,
                campus_code='02', building_code='18', min_capacity=80)
    live = query.query_free_classrooms(**args, freshness='live').to_dict()
    proof = live['freshness']['roomStates']
    assert proof['complete'] is True
    assert proof['free'] == ['金明综合楼3101']
    assert proof['occupied'] == ['金明综合楼3102']
    assert live['rooms'] == []  # Capacity filtering must not turn a free room into occupied.
    cached = query.query_free_classrooms(**args, freshness='cache_first').to_dict()
    assert cached['freshness']['roomStates'] == proof


def test_day_image_keeps_five_independent_states_and_original_canvas():
    base = result()
    periods = []
    for period in range(1, 6):
        slot = copy.deepcopy(base)
        slot['period'] = period
        slot['query']['period'] = period
        slot['freshness']['roomStates']['scope']['period'] = period
        if period == 2:
            slot['freshness']['stale'] = True
        periods.append(slot)
    row = {'success': True, '_resolved_tool_name': 'empty_classroom_query',
           'data': {'campusCode': '02', 'campusName': '金明校区', 'buildingCode': '18',
                    'buildingName': '金明综合楼3', 'periods': periods}}
    image = Image.open(io.BytesIO(render_classroom_map(row)))
    assert image.size == (1280, 1720)
    assert image.getpixel((197, 1212)) == (35, 155, 112)
    assert image.getpixel((218, 1212)) == (225, 230, 236)


@pytest.mark.parametrize('freshness', ['live', 'cache_first'])
def test_schedule_parse_errors_make_coverage_incomplete(tmp_path, monkeypatch, freshness):
    from campus_core import storage_paths
    from campus_core.empty_classroom import query, storage
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    parsed = {'classrooms': [{'room_name': '金明综合楼3101', 'room_id': 'r1'}],
              'cells': [], 'parse_errors': ['unrecognized occupied cell']}
    storage.save_parsed_schedule(parsed, '2026,1', '02', '18')
    storage.save_schedule_html('<html>cached schedule</html>', '2026,1', '02', '18')
    calls = []
    def sync(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(sync_status='success', error='')
    monkeypatch.setattr(query, 'sync_schedule', sync)
    client = SimpleNamespace(get_campus_map=lambda: {'02': '金明校区'},
                             get_building_map=lambda code: {'18': '金明综合楼3'})
    data = query.query_free_classrooms(client=client, term_code='2026,1', week=9, day_of_week=2,
        period=2, campus_code='02', building_code='18', freshness=freshness).to_dict()
    assert data['freshness']['partial'] is True
    assert data['freshness']['roomStates']['complete'] is False
    assert len(calls) == (1 if freshness == 'live' else 0)
    data.update(success=True, _resolved_tool_name='empty_classroom_query')
    image = Image.open(io.BytesIO(render_classroom_map(data)))
    assert image.getpixel((195, 1180)) == (225, 230, 236)


def test_filtered_image_summary_explains_whole_building_states():
    from henu_plugin.classroom_map import classroom_summary
    row = result()
    row['freshness']['roomStates']['filtered'] = True
    row['query']['minCapacity'] = 80
    row['rooms'] = []
    row['total'] = 0
    text = '\n'.join(classroom_summary(row, image_ok=True).pages())
    assert '整栋课程状态' in text and '筛选' in text
    image = Image.open(io.BytesIO(render_classroom_map(row)))
    assert image.getpixel((195, 1180)) == (35, 155, 112)
