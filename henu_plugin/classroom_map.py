"""Read-only classroom diagrams transcribed from reviewed floor-plan photos."""
from __future__ import annotations

import base64
import io
import math
import re
import time
from collections import OrderedDict
from pathlib import Path

FREE = (35, 155, 112)
OCCUPIED = (243, 216, 211)
OCCUPIED_INK = (129, 57, 47)
OCCUPIED_BORDER = (175, 75, 61)
UNKNOWN = (225, 230, 236)
INK = (30, 45, 61)

# Three-numbered building: odd rooms north of the corridor, remaining rooms south.
# Source: user-provided 2026-10-20 floor-plan photo, matched to Jinming building 18.
LAYOUTS = {'18': {'number': '3', 'floors': {
    floor: ([f'3{floor}{n:02}' for n in (1, 3, 5)],
            [f'3{floor}{n:02}' for n in (2, 4, 6, 7, 8, 9)])
    for floor in range(1, 6)}}}
LAYOUTS.update({
    '17': {'number': '2', 'floors': {
        f: ([None, None, None, None, f'2{f}06'] if f in (3, 4) else [],
            [f'2{f}{n:02}' for n in range(1, 4 if f == 1 else 6)]) for f in range(1, 6)}},
    '20': {'number': '5', 'floors': {
        f: ([f'5{f}{n:02}' for n in (1, 3, 5)],
            [f'5{f}{n:02}' for n in ((2, 4, 6, 7, 8) if f >= 4 else (2, 4, 6, 7))]) for f in range(1, 6)}},
    '21': {'number': '6', 'floors': {
        f: ([None, None, None, None, f'6{f}05'] if f in (3, 4) else [],
            [f'6{f}{n:02}' for n in range(1, 5)]) for f in range(1, 6)}}})


def is_classroom(result):
    tool = (result.get('cli') or {}).get('resolved_tool') or result.get('_resolved_tool_name')
    return (tool == 'empty_classroom_query' and result.get('success') is True
            and (isinstance(result.get('rooms'), list)
                 or isinstance(result.get('data'), dict) and isinstance(result['data'].get('periods'), list)))


def verified(row):
    proof = row.get('freshness') if isinstance(row.get('freshness'), dict) else {}
    if (row.get('success') is False or row.get('partial') or row.get('stale') or row.get('is_stale')
            or proof.get('partial') or proof.get('stale') or proof.get('refreshFailed')
            or proof.get('refreshErrors') or proof.get('cacheHit') is False):
        return False
    age, ttl = proof.get('ageSeconds'), proof.get('ttlSeconds')
    if age is None and ttl is None:
        return proof.get('liveChecked') is True
    return (bool(proof.get('fetchedAt')) and type(age) in (int, float)
            and type(ttl) in (int, float) and math.isfinite(age) and math.isfinite(ttl)
            and 0 <= age <= ttl)


def _room_number(row, campus, building):
    if not isinstance(row, dict):
        return ''
    if row.get('campusCode', campus) != campus or row.get('buildingCode', building) != building:
        raise ValueError('classroom scope mismatch')
    name = str(row.get('roomName') or row.get('room_name') or '')
    number = LAYOUTS[building]['number']
    match = re.fullmatch(r'(?:金明综合楼)?(' + number + r'[1-5][0-9]{2})', name)
    return match[1] if match else ''


def _room_states(row, campus, building):
    """Use explicit same-query coverage; never infer occupancy from absence."""
    if not verified(row):
        return {}
    proof = row.get('freshness', {}).get('roomStates')
    query = row.get('query') or {}
    if not isinstance(proof, dict) or proof.get('complete') is not True:
        return {}
    scope = proof.get('scope')
    fields = ('termCode', 'week', 'dayOfWeek', 'period', 'campusCode', 'buildingCode')
    if (not isinstance(scope, dict) or any(scope.get(k) != query.get(k) or k not in scope for k in fields)
            or scope.get('campusCode') != campus or scope.get('buildingCode') != building):
        return {}
    free, occupied = proof.get('free'), proof.get('occupied')
    rooms = row.get('rooms')
    if (not isinstance(free, list) or not isinstance(occupied, list)
            or not all(isinstance(name, str) for name in free + occupied)
            or not isinstance(rooms, list) or type(row.get('total')) is not int
            or row['total'] != len(rooms) or set(free) & set(occupied)):
        return {}
    actual = {_room_number(item, campus, building) for item in rooms}
    free_numbers = {_room_number({'roomName': name}, campus, building) for name in free}
    occupied_numbers = {_room_number({'roomName': name}, campus, building) for name in occupied}
    if (free_numbers - {''}) & (occupied_numbers - {''}):
        return {}
    if not (actual - {''}) <= free_numbers:
        return {}
    if not proof.get('filtered') and actual != free_numbers:
        return {}
    states = {}
    for names, state in ((free, 'free'), (occupied, 'occupied')):
        for name in names:
            number = _room_number({'roomName': name}, campus, building)
            if number:
                states[number] = state
    return states


def catalog_uncovered(result):
    """Exact school identities with no course coverage; never status evidence."""
    query = result.get('query') or {}
    campus, building = query.get('campusCode'), query.get('buildingCode')
    if campus != '02' or building not in LAYOUTS:
        return []
    states = _room_states(result, campus, building)
    if not states:
        return []
    entries = result.get('freshness', {}).get('roomStates', {}).get('catalogUncovered', [])
    if not isinstance(entries, list) or not all(isinstance(row, dict) for row in entries):
        return []
    ids = [row.get('roomId') for row in entries]
    if not all(isinstance(value, str) and value for value in ids) or len(set(ids)) != len(ids):
        return []
    matched = []
    for row in entries:
        if row.get('campusCode') != campus or row.get('buildingCode') != building:
            continue
        number = _room_number(row, campus, building)
        if number and number not in states:
            matched.append((number, row['roomId']))
    return sorted(set(matched))


def render_classroom_map(result):
    """Return a PNG; absent rooms remain unverified, never inferred occupied."""
    from PIL import Image, ImageDraw, ImageFont
    if not is_classroom(result):
        raise ValueError('unsupported classroom result')
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    all_day = isinstance(data.get('periods'), list)
    query = data if all_day else result.get('query') or {}
    campus, building = query.get('campusCode'), query.get('buildingCode')
    if campus != '02' or building not in LAYOUTS:
        raise ValueError('no reviewed layout for this scope')
    layout = LAYOUTS[building]
    if (query.get('campusName', '金明校区') != '金明校区'
            or query.get('buildingName', '金明综合楼' + layout['number']) not in
            {'金明综合楼' + layout['number'], '金明校区 金明综合楼' + layout['number']}):
        raise ValueError('scope name mismatch')
    if all_day:
        slots = data['periods']
        if len(slots) != 5 or {slot.get('period') for slot in slots} != {1, 2, 3, 4, 5}:
            raise ValueError('incomplete day matrix')
        available = {}
        for slot in slots:
            detail = slot.get('query') or {}
            if detail.get('campusCode') != campus or detail.get('buildingCode') != building:
                raise ValueError('mixed day scope')
            available[slot['period']] = _room_states(slot, campus, building) if not result.get('partial') else {}
    else:
        available = {1: _room_states(result, campus, building)}
    font_file = Path(__file__).with_name('assets') / 'NotoSansSC-Classroom.otf'
    font = lambda size: ImageFont.truetype(str(font_file), size)
    status_font = lambda size: ImageFont.truetype(str(font_file.with_name('NotoSansSC-Classroom-Status.ttf')), size)
    image = Image.new('RGB', (1280, 1720 if all_day else 1550), '#f6f8fa')
    draw = ImageDraw.Draw(image)
    title = '金明校区 · 综合楼' + layout['number']
    draw.text((64, 36), title, font=font(46), fill=INK)
    period = '全天 · 状态点从左到右为第1至5大节' if all_day else query.get('periodName') or f"第{query.get('period', '?')}大节"
    window = (str(data.get('date', '')) + ' · ' if all_day else f"第{query.get('week', '?')}周 · 星期{query.get('dayOfWeek', '?')} · ") + period
    if not all_day and query.get('clockVerified'):
        window += ' ' + query['clockStart'] + '–' + query['clockEnd']
    draw.text((64, 106), window, font=font(30), fill=INK)
    for x, color, label, mark in ((64, FREE, '空闲', '✓'), (400, OCCUPIED, '课程占用', '×'), (850, UNKNOWN, '未核验', '?')):
        draw.rounded_rectangle((x, 180, x + 40, 220), radius=8, fill=color,
                               outline=OCCUPIED_BORDER if color == OCCUPIED else None, width=2)
        draw.text((x + 20, 200), mark, anchor='mm', font=status_font(23), fill='white' if color == FREE else INK)
        draw.text((x + 58, 178), label, font=status_font(30), fill=INK)
    for index, floor in enumerate((5, 4, 3, 2, 1)):
        top = 300 + index * 210
        draw.rounded_rectangle((48, top, 1232, top + 198), radius=18, fill='white')
        draw.text((72, top + 70), f'{floor}F', font=font(40), fill=INK)
        draw.line((170, top + 110, 1160, top + 110), fill='#c4ced8', width=3)
        draw.text((1070, top + 85), '走廊', font=font(21), fill='#657589')
        for row_index, row in enumerate(layout['floors'][floor]):
            for column, number in enumerate(row):
                if not number:
                    continue
                x, y = 180 + column * 145, top + 28 + row_index * 94
                state = available[1].get(number, 'unknown') if not all_day else 'unknown'
                free = state == 'free'
                occupied = state == 'occupied'
                draw.rounded_rectangle((x, y, x + 130, y + 64), radius=10,
                                       fill='white' if all_day else FREE if free else OCCUPIED if occupied else UNKNOWN,
                                       outline=UNKNOWN if all_day else OCCUPIED_BORDER if occupied else None, width=2)
                draw.text((x + 65, y + (20 if all_day else 32)), number, anchor='mm', font=font(32),
                          fill='white' if free else OCCUPIED_INK if occupied else INK)
                if not all_day:
                    draw.text((x + 115, y + 11), '✓' if free else '×' if occupied else '?', anchor='mm',
                              font=status_font(14), fill='white' if free else OCCUPIED_INK if occupied else INK)
                if all_day:
                    for p in range(1, 6):
                        cx = x + 17 + (p - 1) * 21
                        state = available[p].get(number, 'unknown')
                        draw.ellipse((cx - 7, y + 43, cx + 7, y + 57),
                                     fill=FREE if state == 'free' else OCCUPIED if state == 'occupied' else UNKNOWN,
                                     outline=OCCUPIED_BORDER if state == 'occupied' else None)
                        draw.text((cx, y + 49), '✓' if state == 'free' else '×' if state == 'occupied' else '?',
                                  anchor='mm', font=status_font(10), fill='white' if state == 'free' else INK)
    if all_day:
        for index, slot in enumerate(sorted(data['periods'], key=lambda row: row['period'])):
            y = 1370 + index * 52
            draw.rounded_rectangle((64, y, 104, y + 40), radius=12, fill=INK)
            draw.text((84, y + 20), str(slot['period']), anchor='mm', font=font(27), fill='white')
            label = (slot.get('clockStart', '') + '–' + slot.get('clockEnd', '')
                     if data.get('clockVerified') else '第' + str(slot['period']) + '大节 · 时刻待核验')
            draw.text((132, y + 20), label, anchor='lm', font=font(32), fill=INK)
    state_rows = data['periods'] if all_day else [result]
    uncovered = sorted({number for row in state_rows for number, _ in catalog_uncovered(row)})
    if any((row.get('freshness') or {}).get('roomStates', {}).get('filtered') for row in state_rows):
        draw.text((64, 1630 if all_day else 1365), '图示整栋课程状态；文字清单已筛选。', font=status_font(23), fill=INK)
    if uncovered:
        label = '教室已确认，部分课表未覆盖：' if all_day else '教室已确认，课表未覆盖：'
        if all_day:
            draw.text((650, 1370), label, font=status_font(23), fill=INK)
            for index in range(0, len(uncovered), 6):
                draw.text((650, 1405 + index // 6 * 32), '、'.join(uncovered[index:index + 6]), font=status_font(23), fill=INK)
        else:
            draw.text((64, 1405), label + '、'.join(uncovered), font=status_font(23), fill=INK)
    draw.text((64, 1660 if all_day else 1450), '课程未占用不等于现场开放。', font=font(28), fill='#59697b')
    output = io.BytesIO()
    from PIL.PngImagePlugin import PngInfo
    metadata = PngInfo()
    metadata.add_text('Title', title)
    metadata.add_text('CatalogUncovered', '、'.join(uncovered))
    image.save(output, 'PNG', optimize=True, pnginfo=metadata)
    return output.getvalue()


def build_classroom_image(result):
    try:
        data = render_classroom_map(result)
        return base64.b64encode(data).decode() if len(data) <= 2 * 1024 * 1024 else None
    except (ValueError, KeyError, TypeError, OSError):
        return None


def classroom_summary(result, *, image_ok=False, image_failed=False):
    from henu_plugin.qq_cards import Card, escape
    query = result.get('query') if isinstance(result.get('query'), dict) else {}
    proof = result.get('freshness') if isinstance(result.get('freshness'), dict) else {}
    lines = [' · '.join(escape(query.get(k, '')) for k in ('campusName', 'buildingName') if query.get(k))]
    lines.append(f"第{query.get('week', '?')}周 · 星期{('一二三四五六日'[query['dayOfWeek'] - 1] if type(query.get('dayOfWeek')) is int and 1 <= query['dayOfWeek'] <= 7 else '?')} · "
                 + str(query.get('periodName') or f"第{query.get('period', '?')}大节"))
    if query.get('clockVerified'):
        lines.append(query['clockStart'] + '–' + query['clockEnd'])
    rooms = [row for row in result.get('rooms', []) if isinstance(row, dict)]
    current = verified(result)
    if rooms and current:
        lines.append('教室示例：' + '、'.join(escape(row.get('roomName') or row.get('room_name') or '未标名教室') + (f" · {row['capacity']}座" if type(row.get('capacity')) is int and row['capacity'] > 0 else '') for row in rooms[:3]))
    elif not current:
        age, ttl = proof.get('ageSeconds'), proof.get('ttlSeconds')
        expired = result.get('stale') or result.get('is_stale') or proof.get('stale') or (type(age) in (int, float) and type(ttl) in (int, float) and age > ttl)
        lines.append('缓存已过期，未将其中教室列为可用；请重新查询。' if expired else '本次结果时效待核验，未列为当前可用。')
    if image_failed:
        lines.append('示意图未能显示，仍可查看简短结果。')
    elif not image_ok:
        lines.append('该楼栋暂未提供可核对的位置示意图。')
    else:
        lines.append('图中仅标记已核对位置，其余房间可查看清单。')
    if image_ok and proof.get('roomStates', {}).get('filtered'):
        lines.append('图片为整栋课程状态；数量及教室清单已按条件筛选。')
    uncovered = catalog_uncovered(result)
    if uncovered:
        lines.append('教室已确认，课表未覆盖：' + '、'.join(number for number, _ in uncovered) + '；状态仍未核验。')
    if proof.get('fetchedAt'):
        lines.append('来源获取时间：' + escape(proof['fetchedAt'], 60))
    if proof.get('liveChecked') is False:
        lines.append('本次读取缓存，未请求上游重新核验。')
    lines.append('仅反映课程占用，临时占用及楼门是否开放以现场为准。')
    return Card('空教室', f'课程未占用 {len(rooms)} 间' if current else '时效待核验', tuple(lines),
                '回复“教室清单”查看完整结果；仍可指定大节或查全天。')


def source_duration(result):
    """The remaining school-cache lifetime; never renew it during display."""
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    rows = data.get('periods') or [result]
    durations = []
    for row in rows:
        if not isinstance(row, dict) or not verified(row):
            continue
        proof = row.get('freshness') or {}
        age, ttl = proof.get('ageSeconds'), proof.get('ttlSeconds')
        durations.append(max(0, ttl - age) if type(age) in (int, float) and type(ttl) in (int, float) else 300)
    return min([300, *durations])


def remember_details(plugin, owner, result, *, expires=None):
    from henu_plugin.qq_cards import result_card
    details = getattr(plugin, '_classroom_details', None)
    if details is None:
        details = plugin._classroom_details = OrderedDict()
    details[owner] = (expires if expires is not None else time.monotonic() + source_duration(result), result_card({**result, '_qq_full_classroom_list': True}).pages())
    details.move_to_end(owner)
    while len(details) > 128:
        details.popitem(last=False)


async def handle_classroom_details(plugin, ctx, text):
    if text not in {'教室清单', '查看教室清单', '完整教室清单'}:
        return False
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    from henu_plugin.qq_cards import scope, card_chain, cache, text_card
    owner = scope(str(await ctx.get_bot_uuid() or ''), ctx.event)
    row = getattr(plugin, '_classroom_details', {}).get(owner)
    ctx.prevent_default()
    ctx.prevent_postorder()
    if not row or row[0] <= time.monotonic():
        await ctx.reply(card_chain(text_card('查询结果已过期', '请先重新查询空教室，再查看清单。')[0]))
        return True
    cache(plugin).put(owner, row[1], expires=row[0])
    await ctx.reply(card_chain(row[1][0]))
    return True


async def reply_classroom_map(plugin, ctx, staged, owner, footer=''):
    if not isinstance(staged, dict) or staged.get('owner') != list(owner) or not staged.get('classroom_ref'):
        return False
    from henu_plugin.presentation import presentations
    from henu_plugin.qq_cards import card_chain
    from langbot_plugin.api.entities.builtin.platform.message import Image, MessageChain
    try:
        asset = presentations(plugin).asset(owner, ctx.query_id, staged['classroom_ref'])
    except ValueError:
        from henu_plugin.qq_cards import text_card
        await ctx.reply(card_chain(text_card('查询结果已过期', '请重新查询空教室。')[0]))
        return True
    failed = False
    if asset.image:
        try:
            await ctx.reply(MessageChain([Image(base64=asset.image)]))
        except Exception:
            failed = True  # Delivery may have succeeded; do not resend the image.
    page = (asset.fallback if failed else asset.card).pages()[0]
    await ctx.reply(card_chain(page + ('\n\n' + footer if footer else '')))
    return True
