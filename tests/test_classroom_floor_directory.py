"""金明目录按楼层、房号连续排列，不再保留走廊两侧及空格。"""
import copy
import io

from PIL import Image, ImageDraw
import pytest

from henu_plugin import font_assets
from henu_plugin.classroom_map import render_classroom_map


@pytest.mark.parametrize('building,number,counts', [
    ('17', '2', [3, 5, 6, 6, 5]), ('18', '3', [9, 9, 9, 9, 9]),
    ('20', '5', [7, 7, 7, 8, 8]), ('21', '6', [4, 4, 5, 5, 4]),
])
@pytest.mark.parametrize('all_day', [False, True])
def test_floor_directory_orders_rooms_without_empty_rows(monkeypatch, building, number, counts, all_day):
    labels, rectangles = [], []
    text = font_assets.draw_text
    rectangle = ImageDraw.ImageDraw.rounded_rectangle

    def traced(draw, xy, value, size, fonts, *, weight='regular', **kwargs):
        labels.append((str(value), draw.textbbox(xy, str(value), font=fonts(size, weight),
                                                anchor=kwargs.get('anchor'))))
        return text(draw, xy, value, size, fonts, weight=weight, **kwargs)

    def boxed(draw, xy, *args, **kwargs):
        rectangles.append(xy)
        return rectangle(draw, xy, *args, **kwargs)

    monkeypatch.setattr(font_assets, 'draw_text', traced)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'rounded_rectangle', boxed)
    expected = [f'{number}{floor}{room:02}' for floor in range(5, 0, -1)
                for room in range(1, counts[floor - 1] + 1)]
    scope = dict(termCode='2026,1', week=9, dayOfWeek=2, period=2,
                 campusCode='02', buildingCode=building)
    base = dict(success=True, _resolved_tool_name='empty_classroom_query',
                query={**scope, 'campusName': '金明校区', 'buildingName': '金明综合楼' + number},
                rooms=[dict(roomName='金明综合楼' + expected[0])], total=1,
                freshness=dict(liveChecked=True, roomStates=dict(complete=True, scope=scope,
                    free=['金明综合楼' + expected[0]], occupied=['金明综合楼' + expected[1]])))
    row = base
    if all_day:
        slots = []
        for period in range(1, 6):
            slot = copy.deepcopy(base)
            slot['period'] = period
            slot['query']['period'] = slot['freshness']['roomStates']['scope']['period'] = period
            if period == 2:
                slot['freshness']['stale'] = True
            slots.append(slot)
        row = dict(success=True, _resolved_tool_name='empty_classroom_query',
                   data={**{k: v for k, v in base['query'].items() if k != 'period'}, 'periods': slots})
    png = render_classroom_map(row)
    image = Image.open(io.BytesIO(png))
    actual = [(label, box) for label, box in labels if label.isdigit() and len(label) == 4]
    assert [label for label, _ in actual] == expected
    panels = [box for box in rectangles if box[2] - box[0] > 1000]
    cards = [box for box in rectangles if box[3] - box[1] == 64]
    assert len(panels) == 5 and len(cards) == len(expected)
    for floor, panel in zip(range(5, 0, -1), panels):
        room_boxes = [next(card for card in cards if card[0] <= box[0] < box[2] <= card[2]
                           and card[1] <= box[1] < box[3] <= card[3])
                      for label, box in actual if label[1] == str(floor)]
        assert room_boxes[0][1] - panel[1] == panel[3] - room_boxes[-1][3]
        for index, card in enumerate(room_boxes):
            assert card[0] == room_boxes[index % 5][0]
            assert (card[1] == room_boxes[0][1]) == (index < 5)
        floor_box = next(box for label, box in labels if label == f'{floor}F')
        assert abs((floor_box[1] + floor_box[3] - panel[1] - panel[3]) / 2) <= 2
    assert image.info['View'] == 'floor-directory'
    assert any('楼层目录' in label for label, _ in labels)
    assert all(0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height
               for _, box in labels)
    assert len(png) < 2 * 1024 * 1024
