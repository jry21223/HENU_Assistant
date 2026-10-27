"""School directory identity never supplies missing course occupancy."""
import copy
import io
from types import SimpleNamespace

from PIL import Image
import pytest

from campus_core.empty_classroom.models import Classroom
from campus_core.empty_classroom import query


def test_query_preserves_school_ids_without_treating_uncovered_rooms_as_free(tmp_path, monkeypatch):
    from campus_core import storage_paths
    monkeypatch.setattr(storage_paths, '_BASE_DIR', tmp_path)
    empty_slots = ''.join(f'<div class="div1" id="0{day}{period}"></div>'
                          for day in range(1, 8) for period in range(1, 6))
    client = SimpleNamespace(
        fetch_schedule_html=lambda **kw: f'<html><p>教室：金明综合楼6101(178)</p><table id="mytable0"><tr><td>{empty_slots}</td></tr></table></html>',
        fetch_classrooms=lambda **kw: [
            Classroom('0000213', '金明综合楼6101', '02', '', '21', '', 178, '多媒体教室'),
            Classroom('0000217', '金明综合楼6305', '02', '', '21', '', 300, '多媒体教室'),
            Classroom('0000222', '金明综合楼6405', '02', '', '21', '', 300, '多媒体教室')],
        get_campus_map=lambda: {'02': '金明校区'},
        get_building_map=lambda code: {'21': '金明综合楼6'})
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
