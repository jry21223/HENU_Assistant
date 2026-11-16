"""Read-only classroom directories and reviewed Minglun photo plans."""
from __future__ import annotations

import base64
import io
import math
import re
import time
from collections import OrderedDict

FREE = (35, 155, 112)
UNKNOWN = (225, 230, 236)
INK = (30, 45, 61)

# Room identity and floor membership only; list order does not describe a corridor.
JINMING_BUILDINGS = {
    '17': {'number': '2', 'floors': {
        f: [f'2{f}{n:02}' for n in range(1, 4 if f == 1 else 7 if f in (3, 4) else 6)]
        for f in range(1, 6)}},
    '18': {'number': '3', 'floors': {
        f: [f'3{f}{n:02}' for n in range(1, 10)] for f in range(1, 6)}},
    '20': {'number': '5', 'floors': {
        f: [f'5{f}{n:02}' for n in range(1, 9 if f >= 4 else 8)] for f in range(1, 6)}},
    '21': {'number': '6', 'floors': {
        f: [f'6{f}{n:02}' for n in range(1, 6 if f in (3, 4) else 5)] for f in range(1, 6)}},
}


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
    if campus != '02' or building not in JINMING_BUILDINGS:
        from henu_plugin.classroom_floorplans import catalog_name
        return catalog_name(campus, building, name)
    number = JINMING_BUILDINGS[building]['number']
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
    if campus != '02' or building not in JINMING_BUILDINGS:
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
    from PIL import Image, ImageChops, ImageDraw
    from henu_plugin.font_assets import draw_text, new_font_cache

    if not is_classroom(result):
        raise ValueError('unsupported classroom result')
    font = new_font_cache()
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    all_day = isinstance(data.get('periods'), list)
    query = data if all_day else result.get('query') or {}
    campus, building = query.get('campusCode'), query.get('buildingCode')
    from henu_plugin.classroom_floorplans import draw_period_legend, plans, render_catalog_plan
    entry = plans().get(f'{campus}-{building}')
    if entry:
        if (query.get('campusName', entry['campus']) != entry['campus']
                or query.get('buildingName', entry['name']) not in
                {entry['name'], entry['campus'] + ' ' + entry['name']}):
            raise ValueError('scope name mismatch')
    elif campus != '02' or building not in JINMING_BUILDINGS:
        raise ValueError('no reviewed layout for this scope')
    directory = JINMING_BUILDINGS.get(building) if campus == '02' else None
    if not entry and (query.get('campusName', '金明校区') != '金明校区'
            or query.get('buildingName', '金明综合楼' + directory['number']) not in
            {'金明综合楼' + directory['number'], '金明校区 金明综合楼' + directory['number']}):
        raise ValueError('scope name mismatch')
    if all_day:
        slots = data['periods']
        if len(slots) != 5 or {slot.get('period') for slot in slots} != {1, 2, 3, 4, 5}:
            raise ValueError('incomplete day matrix')
        available = {}
        day_scope = {k: query.get(k, (slots[0].get('query') or {}).get(k))
                     for k in ('termCode', 'week', 'dayOfWeek')}
        for slot in slots:
            detail = slot.get('query') or {}
            if (detail.get('campusCode') != campus or detail.get('buildingCode') != building
                    or detail.get('period') != slot['period']
                    or any(detail.get(k) != value for k, value in day_scope.items())):
                raise ValueError('mixed day scope')
            available[slot['period']] = _room_states(slot, campus, building) if not result.get('partial') else {}
    else:
        available = {1: _room_states(result, campus, building)}
    if entry:
        return render_catalog_plan(entry, query, available, all_day,
                                   data['periods'] if all_day else [result], font_cache=font)
    state_rows = data['periods'] if all_day else [result]
    uncovered = sorted({number for row in state_rows for number, _ in catalog_uncovered(row)})
    filtered = any((row.get('freshness') or {}).get('roomStates', {}).get('filtered') for row in state_rows)
    floors = sorted(directory['floors'].items(), reverse=True)
    floor_rows = {floor: math.ceil(len(rooms) / 5) for floor, rooms in floors}
    panel_heights = {floor: 48 + rows * 64 + max(0, rows - 1) * 16
                     for floor, rows in floor_rows.items()}
    footer_top = 300 + sum(height + 12 for height in panel_heights.values()) + 24
    footer_height = ((276 if all_day else 0) + (40 if filtered else 0)
                     + (35 + math.ceil(len(uncovered) / 12) * 32 if uncovered else 0))
    image = Image.new('RGB', (1280, footer_top + footer_height + 48), '#f6f8fa')
    draw = ImageDraw.Draw(image)
    title = '金明校区 · 综合楼' + directory['number']
    draw_text(draw, (48, 48 - font(46, 'bold').getmask(title).getbbox()[1]),
              title, 46, font, weight='bold', fill=INK, anchor='lt')
    from henu_plugin.classroom_floorplans import period_label
    period = '全天 · 状态点对应各大节' if all_day else period_label(
        query.get('period', '?'), query.get('periodName'),
        query.get('clockStart') if query.get('clockVerified') else None,
        query.get('clockEnd') if query.get('clockVerified') else None,
        campus=query.get('campusName') or query.get('campusCode'))
    window = (str(data.get('date', '')) + ' · ' if all_day else f"第{query.get('week', '?')}周 · 星期{query.get('dayOfWeek', '?')} · ") + period
    draw_text(draw, (48, 118), window, 30, font, fill=INK)
    for x, color, label in ((48, FREE, '课程未占用'), (600, UNKNOWN, '未确认空闲')):
        draw.rounded_rectangle((x, 192, x + 40, 232), radius=8, fill=color)
        draw_text(draw, (x + 58, 190), label, 30, font, weight='medium', fill=INK)
    draw_text(draw, (48, 252), '楼层目录 · 按房号排序', 23, font, fill=INK)
    top = 300
    for floor, rooms in floors:
        height = panel_heights[floor]
        draw.rounded_rectangle((48, top, 1231, top + height), radius=18, fill='white')
        floor_label = f'{floor}F'
        bounds = draw.textbbox((0, 0), floor_label, font=font(40, 'medium'), anchor='mm')
        floor_y = top + height / 2 - (bounds[1] + bounds[3]) / 2
        draw_text(draw, (96, floor_y), floor_label, 40, font,
                  weight='medium', fill=INK, anchor='mm')
        for index, number in enumerate(sorted(rooms)):
            x, y = 180 + index % 5 * 210, top + 24 + index // 5 * 80
            free = not all_day and available[1].get(number) == 'free'
            draw.rounded_rectangle((x, y, x + 190, y + 64), radius=10,
                                   fill='white' if all_day else FREE if free else UNKNOWN,
                                   outline=UNKNOWN if all_day else None, width=2)
            draw_text(draw, (x + 95, y + (20 if all_day else 32)), number, 32, font,
                      weight='medium', anchor='mm', fill='white' if free else INK)
            if all_day:
                for p in range(1, 6):
                    cx = x + 95 - 42 + (p - 1) * 21
                    draw.ellipse((cx - 7, y + 43, cx + 7, y + 57),
                                 fill=FREE if available[p].get(number) == 'free' else UNKNOWN)
        top += height + 12
    if all_day:
        footer_top = draw_period_legend(draw, footer_top, data['periods'], query, font)
    if filtered:
        draw_text(draw, (48, footer_top),
                  '图示整栋课程状态；文字清单已筛选。', 23, font, fill=INK)
        footer_top += 40
    if uncovered:
        label = '教室已确认，部分课表未覆盖：' if all_day else '教室已确认，课表未覆盖：'
        draw_text(draw, (48, footer_top), label, 23, font, fill=INK)
        for index in range(0, len(uncovered), 12):
            draw_text(draw, (48, footer_top + 35 + index // 12 * 32),
                      '、'.join(uncovered[index:index + 12]), 23, font, fill=INK)
    content_bottom = ImageChops.difference(image, Image.new('RGB', image.size, '#f6f8fa')).getbbox()[3]
    image = image.crop((0, 0, 1280, content_bottom + 48))
    output = io.BytesIO()
    from PIL.PngImagePlugin import PngInfo
    metadata = PngInfo()
    metadata.add_text('Title', title)
    metadata.add_text('View', 'floor-directory')
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
    from henu_plugin.classroom_floorplans import period_label
    lines.append(f"第{query.get('week', '?')}周 · 星期{('一二三四五六日'[query['dayOfWeek'] - 1] if type(query.get('dayOfWeek')) is int and 1 <= query['dayOfWeek'] <= 7 else '?')} · "
                 + period_label(query.get('period', '?'), query.get('periodName'),
                                query.get('clockStart') if query.get('clockVerified') else None,
                                query.get('clockEnd') if query.get('clockVerified') else None,
                                campus=query.get('campusName') or query.get('campusCode')))
    rooms = [row for row in result.get('rooms', []) if isinstance(row, dict)]
    current = verified(result)
    if rooms and current:
        lines.append('教室示例：' + '、'.join(escape(row.get('roomName') or row.get('room_name') or '未标名教室') + (f" · {row['capacity']}座" if type(row.get('capacity')) is int and row['capacity'] > 0 else '') for row in rooms[:3]))
    elif not current:
        age, ttl = proof.get('ageSeconds'), proof.get('ttlSeconds')
        expired = result.get('stale') or result.get('is_stale') or proof.get('stale') or (type(age) in (int, float) and type(ttl) in (int, float) and age > ttl)
        lines.append('缓存已过期，未将其中教室列为可用；请重新查询。' if expired else '本次结果时效待核验，未列为当前可用。')
    if image_ok:
        from henu_plugin.classroom_floorplans import plans
        entry = plans().get(f"{query.get('campusCode')}-{query.get('buildingCode')}")
        if entry and entry.get('floors'):
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
