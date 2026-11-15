"""Public query-to-image contracts for complete, stale and partial room states."""
import copy
import io
from types import SimpleNamespace

from PIL import Image, ImageChops
import pytest

from henu_plugin.classroom_map import build_classroom_image, render_classroom_map
from henu_plugin.classroom_floorplans import _room_display_label, period_label, plans


def test_period_labels_include_the_official_twenty_four_hour_ranges():
    assert [period_label(period, campus='明伦校区') for period in range(1, 6)] == [
        '第1大节（08:00–09:40）', '第2大节（10:00–12:30）',
        '第3大节（14:05–16:40）', '第4大节（17:00–18:40）',
        '第5大节（19:10–21:40）',
    ]
    assert period_label(2, '第二大节', campus='金明校区') == '第二大节（10:00–12:30）'
    assert period_label(2, '第二大节', campus='03') == '第二大节（10:20–12:45）'
    assert period_label(3, campus='郑州校区') == '第3大节（13:30–16:05）'
    assert period_label(4, campus='龙子湖校区') == '第4大节（16:15–17:45）'
    assert period_label(2, '第二大节', campus='04') == '第二大节（10:00–12:30）'
    assert period_label(2, '第二大节') == '第二大节（10:00–12:30）'
    assert period_label(2, '第二大节', '09:30', '11:00', campus='龙子湖校区') == '第二大节（09:30–11:00）'


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


def zhengzhou_day_matrix():
    scope = {'termCode': '2026,1', 'week': 9, 'dayOfWeek': 2,
             'campusCode': '03', 'buildingCode': '26'}
    periods = []
    for period in range(1, 6):
        query = {**scope, 'period': period, 'campusName': '郑州校区',
                 'buildingName': '九章学堂北楼B座'}
        periods.append({'success': True, 'period': period, 'periodName': f'第{period}大节',
                        'query': query, 'rooms': [], 'total': 0,
                        'freshness': {'liveChecked': True, 'roomStates': {
                            'complete': True, 'scope': query, 'free': [], 'occupied': []}}})
    return {'success': True, '_resolved_tool_name': 'empty_classroom_query',
            'data': {**scope, 'campusName': '郑州校区', 'buildingName': '九章学堂北楼B座',
                     'periods': periods}}


def test_complete_result_uses_green_only_for_free_and_gray_for_other_rooms():
    image = Image.open(io.BytesIO(render_classroom_map(result())))
    assert image.width == 1280
    bounds = ImageChops.difference(image, Image.new('RGB', image.size, '#f6f8fa')).getbbox()
    assert image.height - bounds[3] == 48
    assert image.getpixel((195, 1155)) == (35, 155, 112)  # 3101: free
    assert image.getpixel((405, 1155)) == (225, 230, 236)  # 3102: occupied
    assert image.getpixel((615, 1155)) == (225, 230, 236)  # 3103: no school coverage
    from henu_plugin.classroom_map import classroom_summary
    assert '第2大节（10:00–12:30）' in '\n'.join(classroom_summary(result()).pages())


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
    assert image.getpixel((195, 1155)) == (225, 230, 236)
    assert image.getpixel((405, 1155)) == (225, 230, 236)


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


def test_day_image_keeps_five_independent_states_and_bottom_padding():
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
    assert image.width == 1280
    bounds = ImageChops.difference(image, Image.new('RGB', image.size, '#f6f8fa')).getbbox()
    assert image.height - bounds[3] == 48
    assert image.getpixel((233, 1190)) == (35, 155, 112)
    assert image.getpixel((254, 1190)) == (225, 230, 236)


def test_zhengzhou_day_image_preserves_campus_scope_and_schedule():
    image = Image.open(io.BytesIO(render_classroom_map(zhengzhou_day_matrix())))
    assert image.info['Title'] == '郑州校区 · 九章学堂北楼B座'
    entry = plans()['03-26']
    assert _room_display_label(entry, entry['rooms'][0]) == '301'


def test_classroom_summary_omits_location_defensive_copy():
    from henu_plugin.classroom_map import classroom_summary
    text = '\n'.join(classroom_summary(result(), image_ok=False, image_failed=True).pages())
    assert '暂无位置平面图' not in text
    assert '位置以现场为准' not in text
    assert '以现场为准' not in text


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
    assert image.getpixel((195, 1155)) == (225, 230, 236)


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
    assert image.getpixel((195, 1155)) == (35, 155, 112)
