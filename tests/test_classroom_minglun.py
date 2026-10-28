"""Photo layout behavior through the delivered PNG boundary."""
import io
from PIL import Image

from henu_plugin.classroom_map import render_classroom_map


def scoped(building, free, occupied=()):
    scope = dict(termCode='2026,1', week=9, dayOfWeek=4, period=2,
                 campusCode='01', buildingCode=building)
    return dict(success=True, _resolved_tool_name='empty_classroom_query',
                query={**scope, 'campusName': '明伦校区',
                       'buildingName': '十号楼' if building == '0013' else '综合教学楼'},
                rooms=[{'roomName': n, 'campusCode': '01', 'buildingCode': building} for n in free],
                total=len(free), freshness={'liveChecked': True, 'roomStates': {
                    'complete': True, 'scope': scope, 'free': list(free), 'occupied': list(occupied)}})


def test_building_ten_preserves_photo_room_200_and_delivers_status_colors():
    image = Image.open(io.BytesIO(render_classroom_map(scoped(
        '0013', ['十号楼200'], ['十号楼201']))))
    assert image.info['Title'] == '明伦校区 · 十号楼'
    assert image.info['PlanRooms'] == '68'
    assert image.info['UnnumberedSpaces'] == '13'
    # Second floor 200 lies south of the central corridor; left wing 201 is occupied.
    assert image.getpixel((600, 1650)) == (35, 155, 112)
    assert image.getpixel((100, 1530)) == (225, 230, 236)


def test_comprehensive_keeps_subrooms_separate_and_ambiguous_photo_cells_gray():
    row = scoped('0003', ['微格教室2（综合楼605-2)', '微格教室3（综合楼605-3）',
                          '综合教学楼509', '综合教学楼609'])
    image = Image.open(io.BytesIO(render_classroom_map(row)))
    assert image.info['PlanRooms'] == '103'
    assert '605-2' in image.info['UnlocatedRooms']
    assert '605-3' in image.info['UnlocatedRooms']
    assert '509' in image.info['UnlocatedRooms']
    # 6F large room printed 509 remains unknown, while small 609 can match exactly.
    assert image.getpixel((450, 450)) == (225, 230, 236)
    assert image.getpixel((310, 500)) == (35, 155, 112)
    # 605-2 and 605-3 never grant free state to the larger photo room 605.
    assert image.getpixel((310, 640)) == (225, 230, 236)


def test_day_matrix_rejects_wrong_period_and_cross_day_evidence():
    import copy
    import pytest
    base = scoped('0013', ['十号楼200'])
    periods = []
    for p in range(1, 6):
        slot = copy.deepcopy(base)
        slot['period'] = p
        slot['query']['period'] = p
        slot['freshness']['roomStates']['scope']['period'] = p
        periods.append(slot)
    row = dict(success=True, _resolved_tool_name='empty_classroom_query',
               data={**base['query'], 'periods': periods})
    row['data'].pop('period')
    wrong = copy.deepcopy(row)
    wrong['data']['periods'][1]['query']['period'] = 1
    wrong['data']['periods'][1]['freshness']['roomStates']['scope']['period'] = 1
    with pytest.raises(ValueError):
        render_classroom_map(wrong)
    row['data']['periods'][1]['query']['dayOfWeek'] = 5
    row['data']['periods'][1]['freshness']['roomStates']['scope']['dayOfWeek'] = 5
    with pytest.raises(ValueError):
        render_classroom_map(row)
