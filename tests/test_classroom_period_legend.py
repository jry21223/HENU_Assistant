"""不同校区复用同一页脚版式，同时保留各自时间与已核验时间覆盖。"""
import copy
import io

from PIL import Image, ImageDraw
import pytest

from henu_plugin import classroom_floorplans, font_assets
from henu_plugin.classroom_map import render_classroom_map
from test_classroom_states import result, zhengzhou_day_matrix


@pytest.mark.parametrize('verified_clock', [False, True])
def test_daily_legends_share_badges_alignment_and_keep_campus_times(monkeypatch, verified_clock):
    base = result()
    slots = []
    for period in range(1, 6):
        slot = copy.deepcopy(base)
        slot['period'] = period
        slot['query']['period'] = slot['freshness']['roomStates']['scope']['period'] = period
        slots.append(slot)
    jinming = dict(success=True, _resolved_tool_name='empty_classroom_query',
                   data={**{k: v for k, v in base['query'].items() if k != 'period'}, 'periods': slots})
    rows = [jinming, zhengzhou_day_matrix()]
    original_text = font_assets.draw_text
    original_rectangle = ImageDraw.ImageDraw.rounded_rectangle
    badges, labels = [], []

    def text(draw, xy, value, size, fonts, *, weight='regular', **kwargs):
        if str(value).startswith(tuple(f'第{p}大节（' for p in range(1, 6))):
            labels.append((str(value), xy, size, kwargs.get('anchor'),
                draw.textbbox(xy, str(value), font=fonts(size, weight), anchor=kwargs.get('anchor'))))
        return original_text(draw, xy, value, size, fonts, weight=weight, **kwargs)

    def rectangle(draw, xy, *args, **kwargs):
        if xy[0] == 64 and xy[2] - xy[0] == xy[3] - xy[1] == 40:
            badges.append(tuple(xy))
        return original_rectangle(draw, xy, *args, **kwargs)

    monkeypatch.setattr(font_assets, 'draw_text', text)
    monkeypatch.setattr(classroom_floorplans, 'draw_text', text)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'rounded_rectangle', rectangle)
    crops, styles = [], []
    for index, row in enumerate(rows):
        badges.clear()
        labels.clear()
        row['data']['clockVerified'] = verified_clock
        for slot in row['data']['periods']:
            slot.update(clockStart='09:30', clockEnd='11:00')
        image = Image.open(io.BytesIO(render_classroom_map(row)))
        assert len(badges) == len(labels) == 5
        first_range = '09:30–11:00' if verified_clock else ('08:00–09:40', '08:30–10:10')[index]
        assert labels[0][0] == f'第1大节（{first_range}）'
        assert [box[1] - badges[0][1] for box in badges] == [0, 52, 104, 156, 208]
        styles.append([(xy[0] - box[0], xy[1] - box[1], size, anchor)
                       for box, (_, xy, size, anchor, _) in zip(badges, labels)])
        crops.append([image.crop(box).tobytes() for box in badges])
        for _, _, _, _, box in labels:
            assert 0 <= box[0] < box[2] <= image.width
            assert 0 <= box[1] < box[3] <= image.height
    assert styles[0] == styles[1] == [(68, 20, 32, 'lm')] * 5
    assert crops[0] == crops[1]
