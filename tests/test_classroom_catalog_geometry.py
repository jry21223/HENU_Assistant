"""检查实际绘制边界，防止目录截断或全天房号被状态点覆盖。"""
import copy
import io
from collections import Counter

from PIL import Image, ImageChops, ImageDraw
import pytest

from henu_plugin import classroom_floorplans as plans_module
from henu_plugin.classroom_map import render_classroom_map


@pytest.mark.parametrize('key', ['03-22', '03-23', '03-24', '03-25', '03-26'])
@pytest.mark.parametrize('all_day', [False, True])
@pytest.mark.parametrize('filtered', [False, True])
def test_zhengzhou_directory_keeps_every_label_and_separates_daily_dots(
        monkeypatch, key, all_day, filtered):
    entry = plans_module.plans()[key]
    names = [room['name'] for room in entry['rooms']]
    scope = dict(termCode='2026,1', week=9, dayOfWeek=2, period=2,
                 campusCode='03', buildingCode=key.split('-')[1])
    free = names[::3]
    base = dict(success=True, _resolved_tool_name='empty_classroom_query',
                query={**scope, 'campusName': entry['campus'], 'buildingName': entry['name']},
                rooms=[] if filtered else [dict(roomName=name, campusCode='03',
                    buildingCode=scope['buildingCode']) for name in free],
                total=0 if filtered else len(free), freshness=dict(liveChecked=True,
                    roomStates=dict(complete=True, scope=scope, free=free,
                        occupied=[name for name in names if name not in free], filtered=filtered)))
    row = base
    if all_day:
        slots = []
        for period in range(1, 6):
            slot = copy.deepcopy(base)
            slot['period'] = period
            slot['query']['period'] = period
            slot['freshness']['roomStates']['scope']['period'] = period
            if period == 3:
                slot['freshness']['stale'] = True
            slots.append(slot)
        row = dict(success=True, _resolved_tool_name='empty_classroom_query',
                   data={**{k: v for k, v in base['query'].items() if k != 'period'},
                         'periods': slots})

    labels, cards, dots = [], [], []
    original_text = plans_module.draw_text
    original_rectangle = ImageDraw.ImageDraw.rounded_rectangle
    original_ellipse = ImageDraw.ImageDraw.ellipse

    def text(draw, xy, value, size, fonts, *, weight='regular', **kwargs):
        labels.append((str(value), draw.textbbox(xy, str(value), font=fonts(size, weight),
                                                anchor=kwargs.get('anchor'))))
        return original_text(draw, xy, value, size, fonts, weight=weight, **kwargs)

    def rectangle(draw, xy, *args, **kwargs):
        if xy[2] - xy[0] > 300 and xy[3] - xy[1] < 150:
            cards.append(xy)
        return original_rectangle(draw, xy, *args, **kwargs)

    def ellipse(draw, xy, *args, **kwargs):
        dots.append(xy)
        return original_ellipse(draw, xy, *args, **kwargs)

    monkeypatch.setattr(plans_module, 'draw_text', text)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'rounded_rectangle', rectangle)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'ellipse', ellipse)
    png = render_classroom_map(row)
    image = Image.open(io.BytesIO(png)).convert('RGB')
    assert len(png) <= 2 * 1024 * 1024
    bounds = ImageChops.difference(image, Image.new('RGB', image.size, '#f6f8fa')).getbbox()
    assert image.height - bounds[3] == 48
    for _, (left, top, right, bottom) in labels:
        assert 0 <= left < right <= image.width
        assert 0 <= top < bottom <= image.height
    assert all(0 <= box[1] < box[3] < image.height for box in cards)
    expected = Counter(plans_module._room_display_label(entry, room) for room in entry['rooms'])
    room_labels = [(label, box) for label, box in labels if label in expected]
    assert Counter(label for label, _ in room_labels) == expected
    assert len(cards) == len(names)
    assert sum('文字清单已筛选' in label for label, _ in labels) == int(filtered)
    if all_day:
        assert {plans_module.period_label(period, campus='03') for period in range(1, 6)} <= {
            label for label, _ in labels}
        assert len(dots) == len(names) * 5
        for _, label_box in room_labels:
            card = next(box for box in cards if box[0] <= label_box[0] < label_box[2] <= box[2]
                        and box[1] <= label_box[1] < label_box[3] <= box[3])
            card_dots = [box for box in dots if card[0] <= box[0] < box[2] <= card[2]
                         and card[1] <= box[1] < box[3] <= card[3]]
            assert len(card_dots) == 5
            assert min(box[1] for box in card_dots) - label_box[3] >= 8
            if card is cards[0]:
                assert image.getpixel((round((card_dots[0][0] + card_dots[0][2]) / 2),
                                       round((card_dots[0][1] + card_dots[0][3]) / 2))) == plans_module.FREE
                assert image.getpixel((round((card_dots[2][0] + card_dots[2][2]) / 2),
                                       round((card_dots[2][1] + card_dots[2][3]) / 2))) == plans_module.GRAY
