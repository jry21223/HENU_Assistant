"""Reviewed photo geometry and catalog views; no timetable data in the assets."""
from __future__ import annotations

import io
import json
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
from PIL.PngImagePlugin import PngInfo
from henu_plugin.font_assets import draw_text, new_font_cache

FREE = (35, 155, 112)
GRAY = (225, 230, 236)
INK = (30, 45, 61)
BORDER = '#bac7d2'
PERIOD_TIMES = {
    1: ('08:00', '09:40'), 2: ('10:00', '12:30'),
    3: ('14:05', '16:40'), 4: ('17:00', '18:40'), 5: ('19:10', '21:40'),
}


@lru_cache(maxsize=1)
def plans():
    return json.loads((Path(__file__).with_name('assets') / 'classroom-plans.json').read_text())


def catalog_name(campus, building, name):
    entry = plans().get(f'{campus}-{building}', {})
    return name if any(r['name'] == name for r in entry.get('rooms', [])) else ''


def period_label(period, name=None, clock_start=None, clock_end=None):
    label = name or f'第{period}大节'
    times = PERIOD_TIMES.get(int(period)) if str(period).isdigit() else None
    start, end = (clock_start, clock_end) if clock_start and clock_end else times or (None, None)
    return f'{label}（{start}–{end}）' if start and end else label


def _draw_floor(draw, floor, kind, left, top, available, all_day, fonts):
    """Scale the reviewed normalized photo coordinates into a floor panel."""
    ten = kind == 'ten'
    scale, shift = (0.7, 40) if ten else (0.55, 0)

    def point(x, y):
        return (left + (x + shift) * scale, top + y * scale)

    def text(x, y, value, size=24, fill=INK, weight='regular'):
        draw_text(draw, point(x, y), value, max(9, round(size * scale)), fonts,
                  weight=weight, anchor='mm', fill=fill)

    def line(points, width=38, fill='#edf1f5'):
        draw.line([point(x, y) for x, y in points], width=max(1, round(width * scale)), fill=fill, joint='curve')

    def box(x, y, w, h, label='', fill='#f8fafb'):
        draw.rounded_rectangle((*point(x, y), *point(x + w, y + h)), radius=2,
                               fill=fill, outline=BORDER, width=1)
        if label:
            text(x + w / 2, y + h / 2, label, 17, '#7a8b98')

    if ten:
        for points in [[(180, 270), (180, 650)], [(1340, 270), (1340, 650)],
                       [(140, 470), (180, 470)], [(1340, 470), (1380, 470)],
                       [(180, 580), (1340, 580)], [(820, 125), (820, 580)],
                       [(680, 125), (940, 125)]]:
            line(points)
        for x, y, w, h, label in [(240, 480, 25, 75, ''), (240, 610, 25, 75, ''),
                                  (1245, 480, 35, 75, ''), (1245, 610, 35, 75, ''),
                                  (140, 300, 70, 58, '厕所'), (1310, 300, 70, 58, '厕所'),
                                  (615, 505, 60, 50, '楼梯'), (880, 505, 60, 50, '楼梯'),
                                  (690, 505, 60, 50, '辅助'), (800, 505, 60, 50, '辅助')]:
            box(x, y, w, h, label)
        for c in floor['blank']:
            box(c['x'], c['y'], c['w'], c['h'], '未标号')
        text(440, 582, '走廊', 17, '#8b9dad')
        text(1080, 582, '走廊', 17, '#8b9dad')
    else:
        number = floor['floor']
        line([(240, 160), (600, 160)])
        line([(240, 160), (240, 665)])
        line([(240, 580), (840, 580)])
        if number == 1:
            line([(240, 665), (240, 795), (570, 795), (570, 960)], 40)
        elif number in (2, 3):
            line([(240, 665), (225, 665), (225, 795), (560, 795), (560, 960)], 35)
            line([(225, 795), (225, 960)], 35)
        else:
            line([(240, 665), (225, 665), (225, 975 if number == 4 else 775)], 25)
        for x, y, w, h, label in [(530, 50, 65, 90, '厕所'), (530, 180, 65, 90, '厕所'),
                                  (770, 470, 65, 90, '厕所'), (770, 600, 65, 90, '厕所'),
                                  (50, 575, 140, 64, '厕所')]:
            box(x, y, w, h, label)
        for x, y, w, h in [(210, 50, 60, 90), (845, 50, 50, 220),
                            (840, 470, 55, 220), (85, 650, 80, 70)]:
            box(x, y, w, h)
            for yy in range(y + 10, y + h - 5, 13):
                line([(x + 10, yy), (x + w - 10, yy)], 2, '#a9b6c1')
        if number < 4 or number == 5:
            box(100, 285, 100, 130, '辅助区')
        elif number == 4:
            box(100, 355, 100, 75, '辅助区')
        text(240, 410, '走廊', 18, '#8d9ca8')
        text(440, 162, '走廊', 15, '#8d9ca8')
        text(550, 582, '走廊', 15, '#8d9ca8')

    for c in floor['cells']:
        x, y, w, h = (c[k] for k in ('x', 'y', 'w', 'h'))
        key = c['stateKey']  # None for ambiguous photo labels, even when a number looks familiar.
        free = not all_day and available[1].get(key) == 'free'
        fill = 'white' if all_day else FREE if free else GRAY
        shape = c.get('shape')
        if shape in {'round-west', 'round-east'}:
            # Sample the same cubic arc used in the reviewed SVG.
            west = shape == 'round-west'
            edge = x + w if west else x
            mid = x + w / 2
            outer = x - 50 if west else x + w + 50
            points = [(edge, y), (mid, y)]
            for i in range(1, 41):
                t = i / 40
                points.append(((1-t)**3*mid + 3*(1-t)**2*t*outer + 3*(1-t)*t*t*outer + t**3*mid,
                               y + (3*(1-t)*t*t + t**3)*h))
            points.append((edge, y + h))
            draw.polygon([point(px, py) for px, py in points], fill=fill)
            line(points + [points[0]], 2.5, BORDER)
        elif shape:
            points = [(x, y+h), (x+w, y+h), (x+w, y+24)]
            for i in range(1, 31):
                t = i / 30
                points.append(((1-t)**2*(x+w) + 2*(1-t)*t*(x+w/2) + t*t*x,
                               (1-t)**2*(y+24) + 2*(1-t)*t*(y-35) + t*t*(y+24)))
            draw.polygon([point(px, py) for px, py in points], fill=fill)
            line(points + [points[0]], 2.5, BORDER)
        else:
            box(x, y, w, h, fill=fill)
        label_y = y + h / 2 - (15 if c.get('uncertain') else 7)
        label_weight = 'medium' if any(char.isdigit() for char in c['label']) else 'regular'
        text(x + w / 2, label_y, c['label'], 27 if w < 105 or c.get('uncertain') else 32,
             'white' if free else INK, label_weight)
        if c.get('uncertain'):
            text(x + w / 2, label_y + 24, '编号待核', 14, '#657e90')
        if all_day:
            for i in range(5):
                cx, cy = point(x + w / 2 - 30 + i * 15, y + h - 14)
                radius = 6 * scale
                draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
                             fill=FREE if available[i+1].get(key) == 'free' else GRAY)
    north_x = 1515 if ten else 940
    line([(north_x, 120), (north_x, 55)], 2, '#748b9c')
    line([(north_x-11, 73), (north_x, 55), (north_x+11, 73)], 2, '#748b9c')
    text(north_x, 35, 'N', 21, '#657e90')


def render_catalog_plan(entry, query, available, all_day, periods, *, font_cache=None):
    """Render only evidence supplied by the caller's verified query scope."""
    fonts = font_cache or new_font_cache()
    kind = entry.get('kind')
    ten = kind == 'ten'
    floors = sorted(entry.get('floors', []), key=lambda f: f['floor'], reverse=not ten)
    placed = {c['roomKey'] for f in floors for c in f['cells']}
    unplaced = [r for r in entry['rooms'] if r['key'] not in placed]
    directory_top = 318 + (len(floors) * 782 if ten else math.ceil(len(floors) / 2) * 660)
    directory_rows = math.ceil(len(unplaced) / 3)
    notes_top = directory_top + (50 + directory_rows * 100 if unplaced else 0)
    footer_height = 430 if all_day else 160
    image = Image.new('RGB', (1280, notes_top + footer_height), '#f6f8fa')
    draw = ImageDraw.Draw(image)
    title = entry['campus'] + ' · ' + entry['name']
    draw_text(draw, (48, 48 - fonts(46, 'bold').getmask(title).getbbox()[1]),
              title, 46, fonts, weight='bold', fill=INK, anchor='lt')
    view = '全天 · 状态点对应各大节' if all_day else period_label(
        query.get('period', '?'), query.get('periodName'),
        query.get('clockStart') if query.get('clockVerified') else None,
        query.get('clockEnd') if query.get('clockVerified') else None)
    scope = f"第{query.get('week', '?')}周 · 星期{query.get('dayOfWeek', '?')} · "
    if all_day and query.get('date'):
        scope = str(query['date']) + ' · '
    draw_text(draw, (48, 118), scope + view, 30, fonts, fill=INK)
    for x, color, label in [(48, FREE, '课程未占用'), (600, GRAY, '未确认空闲')]:
        draw.rounded_rectangle((x, 192, x+40, 232), radius=8, fill=color)
        draw_text(draw, (x+58, 190), label, 30, fonts, weight='medium', fill=INK)
    if not floors:
        draw_text(draw, (48, 248), '教室目录 · 暂无位置平面图', 24, fonts, fill='#59697b')
    for i, f in enumerate(floors):
        left = 48 if ten else 48 + i % 2 * 604
        top = 300 + (i * 782 if ten else i // 2 * 660)
        draw.rounded_rectangle((left, top, left+(1183 if ten else 579), top+(770 if ten else 638)),
                               radius=18, fill='white')
        draw_text(draw, (left+24, top+16), f"{f['floor']}F", 32, fonts,
                  weight='medium', fill=INK)
        _draw_floor(draw, f, kind, left+12 if ten else left+24, top+70,
                    available, all_day, fonts)
    if unplaced:
        label = '目录中有、原图未定位的房间' if floors else '按教室名称排列，位置以现场为准'
        draw_text(draw, (48, directory_top), label, 24, fonts, fill=INK)
    for i, room in enumerate(unplaced):
        x, y = 64 + i % 3 * 386, directory_top + 50 + i // 3 * 100
        free = not all_day and available[1].get(room['name']) == 'free'
        draw.rounded_rectangle((x, y, x+350, y+80), radius=10,
                               fill='white' if all_day else FREE if free else GRAY)
        name = room['name']
        size = max(14, min(28, int(325 / max(len(name), 1))))
        draw_text(draw, (x+175, y+27 if all_day else y+40), name, size, fonts,
                  weight='medium', anchor='mm', fill='white' if free else INK)
        if all_day:
            for p in range(1, 6):
                cx, cy = x+133+(p-1)*21, y+60
                draw.ellipse((cx-7, cy-7, cx+7, cy+7),
                             fill=FREE if available[p].get(room['name']) == 'free' else GRAY)
    offset = notes_top + 65
    if all_day:
        for i, slot in enumerate(sorted(periods, key=lambda s: s['period'])):
            y = offset + i*50
            label = period_label(slot['period'], clock_start=slot.get('clockStart') if query.get('clockVerified') else None,
                                 clock_end=slot.get('clockEnd') if query.get('clockVerified') else None)
            draw_text(draw, (48, y), label, 28, fonts, fill=INK)
        offset += 265
    filtered = any((s.get('freshness') or {}).get('roomStates', {}).get('filtered') for s in periods)
    if filtered:
        draw_text(draw, (48, offset), '图示整栋课程状态；文字清单已筛选。',
                  23, fonts, fill=INK)
        offset += 38
    metadata = PngInfo()
    metadata.add_text('Title', title)
    metadata.add_text('PlanRooms', str(sum(len(f['cells']) for f in floors)))
    metadata.add_text('UnnumberedSpaces', str(sum(len(f['blank']) for f in floors)))
    metadata.add_text('UnlocatedRooms', '、'.join(r['name'] for r in unplaced) if floors else '')
    content_bottom = ImageChops.difference(image, Image.new('RGB', image.size, '#f6f8fa')).getbbox()[3]
    image = image.crop((0, 0, 1280, content_bottom + 48))
    output = io.BytesIO()
    image.save(output, 'PNG', optimize=True, pnginfo=metadata)
    return output.getvalue()
