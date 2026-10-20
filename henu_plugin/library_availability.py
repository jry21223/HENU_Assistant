"""Read-only library availability, scoped to the current QQ conversation."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, date
import json
import re
import threading
import time
from zoneinfo import ZoneInfo

from henu_plugin.qq_cards import Card, card_chain, escape

ZONE = ZoneInfo('Asia/Shanghai')
MAX_REGIONS = 64
WORKERS = 4
QUERY_BUDGET = 12
# One process-wide pool and admission limit: timed-out queries cannot create
# additional pools or grow an unbounded queue behind unfinished requests.
_SEAT_EXECUTOR = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='library-seats')
_SEAT_SLOTS = threading.BoundedSemaphore(WORKERS)
_NETWORK_SLOTS = threading.BoundedSemaphore(WORKERS)


def campus_now():
    return datetime.now(ZONE)


def parse_window(text, now):
    match = re.fullmatch(r'(今天|明天|\d{4}-\d{2}-\d{2})\s*(\d{1,2}:\d{2})\s*[-–—至到]\s*(\d{1,2}:\d{2})', text.strip())
    if not match:
        return None
    day, start, end = match.groups()
    try:
        target = (now.date() + timedelta(days=int(day == '明天'))) if day in {'今天', '明天'} else date.fromisoformat(day)
        start = datetime.strptime(start, '%H:%M').strftime('%H:%M')
        end = datetime.strptime(end, '%H:%M').strftime('%H:%M')
        if start >= end:
            return None
        return target.isoformat(), start, end
    except ValueError:
        return None


def region_counts(rows):
    """Only documented raw free_num/total_num form a date-level count."""
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = str(row.get('id') or '')
        merged = str(row.get('nameMerge') or '')
        name = str(row.get('name') or merged).strip()
        if not name or (identity, name) in seen:
            continue
        seen.add((identity, name))
        free, total = row.get('free_num'), row.get('total_num')
        known = type(free) is int and type(total) is int and 0 <= free <= total
        from campus_core.locations import library_region_metadata
        result.append({'location': name, **library_region_metadata(row), 'area_id': identity,
                       'available_count': free if known else None})
    return result


def _fetch(cookies, target, start, end):
    # Each query owns its session. No default profile, authentication write or log.
    import requests
    from campus_core import HenuCampusBot
    from urllib3.util import Timeout

    deadline = time.monotonic() + QUERY_BUDGET

    class BoundedSession(requests.Session):
        def request(self, method, url, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not _NETWORK_SLOTS.acquire(timeout=remaining):
                raise TimeoutError('Library query deadline exceeded')
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Library query deadline exceeded')
                kwargs['timeout'] = Timeout(total=remaining, connect=min(3, remaining), read=min(3, remaining))
                kwargs['allow_redirects'] = False
                return super().request(method, url, **kwargs)
            finally:
                _NETWORK_SLOTS.release()

    def bot():
        value = HenuCampusBot('', '', cookies)
        headers = dict(value.session.headers)
        value.session.close()
        value.session = BoundedSession()
        value.session.headers.update(headers)
        # Restore existing headers/cookies without persisting or re-authenticating.
        value.session.cookies.update({k: v for k, v in cookies.items() if not k.startswith('_')})
        value._set_auth_header()
        return value

    client = bot()
    try:
        response = client._post_json('/v4/space/pick', {'date': target}, allow_reauth=False)
    finally:
        client.session.close()
    if response.get('code') != 0:
        return {'success': False, 'error_code': 'auth_required' if response.get('code') == 10001 else 'library_unavailable'}
    raw = (response.get('data') or {}).get('area')
    if not isinstance(raw, list) or len(raw) > MAX_REGIONS:
        return {'success': False, 'error_code': 'library_unavailable'}
    rows = region_counts(raw)
    if start and end:
        for row in rows:
            row['available_count'] = None

        def count(row):
            if not row['area_id'] or time.monotonic() >= deadline:
                return None
            client = bot()
            try:
                space = client._get_space_map(row['area_id'])
                if time.monotonic() >= deadline:
                    return None
                plan = client._build_reservation_plan(row['area_id'], space, target,
                    preferred_time=start, preferred_end_time=end)
                payload = plan['seat_query']
                # Backend slots may clip/replace requested times: never claim
                # availability for the full interval from a different query.
                if (payload.get('day') != target or payload.get('start_time') != start
                        or payload.get('end_time') != end):
                    return None
                response = client._post_json('/v4/Space/seat', payload, allow_reauth=False)
                seats = (response.get('data') or {}).get('list')
                if response.get('code') != 0 or not isinstance(seats, list):
                    return None
                # School response verified 2026-10-20 also has 2/已预约.
                # Other new enums/containers/bools are not zero seats.
                if any(not isinstance(seat, dict) or type(seat.get('status')) not in (str, int)
                       or not (str(seat['status']) in {'0', '1'} or
                               str(seat['status']) == '2' and seat.get('status_name') == '已预约')
                       or str(seat['status']) == '1' and seat.get('status_name', '') not in {'', '空闲', '可用'}
                       for seat in seats):
                    return None
                return sum(1 for seat in seats if isinstance(seat, dict) and client._seat_status(seat) == '1')
            except Exception:
                return None
            finally:
                client.session.close()

        remaining = iter(rows)
        active = {}

        def submit(row):
            budget = deadline - time.monotonic()
            if budget <= 0 or not _SEAT_SLOTS.acquire(timeout=budget):
                return False
            try:
                future = _SEAT_EXECUTOR.submit(count, row)
            except BaseException:
                _SEAT_SLOTS.release()
                raise
            future.add_done_callback(lambda _: _SEAT_SLOTS.release())
            active[future] = row
            return True

        try:
            for row in remaining:
                if not submit(row) or len(active) == WORKERS:
                    break
            while active and time.monotonic() < deadline:
                done, _ = wait(active, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
                if not done:
                    break
                for future in done:
                    row = active.pop(future)
                    row['available_count'] = future.result()
                    following = next(remaining, None)
                    if following is not None and time.monotonic() < deadline:
                        submit(following)
        finally:
            for future in active:
                future.cancel()
    return {'success': True, 'date': target, 'start': start, 'end': end, 'locations': rows}


async def _query_cached(plugin, sender, target, start, end):
    from henu_plugin.storage_adapter import PluginStorageAdapter
    adapter = PluginStorageAdapter(plugin, sender)
    worker = None
    try:
        paths = await adapter.load_all()
        cookies = json.loads(paths.library_cookie_file.read_text())
        profile = json.loads(paths.profile_file.read_text())
        saved = bool(profile.get('student_id') and profile.get('password'))
        subject = cookies.get('_subject_student_id')
        if subject and str(subject) != str(profile.get('student_id') or ''):
            return {'success': False, 'error_code': 'auth_required'}, saved
        if not cookies.get('_v4_token'):
            return {'success': False, 'error_code': 'auth_required'}, saved
        worker = asyncio.create_task(asyncio.to_thread(_fetch, cookies, target, start, end))
        try:
            return await asyncio.shield(worker), saved
        except asyncio.CancelledError:
            await worker
            raise
    finally:
        await adapter.abort()


async def query_library(plugin, sender, target, start='', end='', *, session=None, query_id=0):
    """Reuse the established read CLI for one login recovery, never rebind."""
    result, saved = await _query_cached(plugin, sender, target, start, end)
    if result.get('error_code') != 'auth_required':
        return result
    if not saved:
        return {'success': False, 'error_code': 'account_not_bound'}
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    session = session or Session(launcher_type=LauncherTypes.PERSON, launcher_id=sender, sender_id=sender)
    tool = HenuCliSafe()
    tool.plugin = plugin
    # _query_cached has released the user's transaction lock before this call.
    restored = await tool.call({'command': f'library locations --date {target}'}, session, query_id)
    if not restored.get('success'):
        return {'success': False, 'error_code': 'login_recovery_failed'}
    result, _ = await _query_cached(plugin, sender, target, start, end)
    if result.get('error_code') == 'auth_required':
        return {'success': False, 'error_code': 'login_recovery_failed'}
    return result


async def _reply(ctx, status, lines, next_step='', buttons=None):
    # Group several short rows as one paragraph to avoid eight-row pagination.
    blocks, block = [], ''
    for line in lines:
        if len((block + '\n' + line).encode()) > 1800:
            blocks.append(block)
            block = ''
        block += ('\n' if block else '') + line
    if block:
        blocks.append(block)
    from henu_plugin.qq_buttons import envelope
    pages = Card('图书馆余座', status, tuple(blocks), next_step).pages()
    for index, page in enumerate(pages):
        await ctx.reply(card_chain(envelope(page, buttons if index == len(pages)-1 else None)))


async def handle_library_availability(plugin, ctx, text):
    initial = text == '图书馆座位'
    window = parse_window(text, campus_now())
    state = getattr(plugin, '_library_availability_pending', {})
    if not initial and not state:
        return False
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    kind = getattr(getattr(ctx.event, 'launcher_type', ''), 'value', getattr(ctx.event, 'launcher_type', ''))
    sender = str(getattr(ctx.event, 'sender_id', '') or '').strip()
    launcher = str(getattr(ctx.event, 'launcher_id', '') or '').strip()
    if kind not in {'person', 'group'} or not sender or not launcher:
        return False
    bot = str(await ctx.get_bot_uuid() or '')
    if not bot:
        return False
    key = (bot, kind, launcher, sender)
    state = {k: v for k, v in state.items() if v > time.monotonic()}
    plugin._library_availability_pending = state
    if not initial and (key not in state or (not window and not re.fullmatch(r'(今天|明天|\d{4}-\d{2}-\d{2})\s*\d{1,2}:\d{2}\s*[-–—至到]\s*\d{1,2}:\d{2}', text.strip()))):
        return False
    ctx.prevent_default()
    ctx.prevent_postorder()
    if len(state) >= 256 and key not in state:
        state.pop(next(iter(state)))
    state[key] = time.monotonic() + 300
    if not initial and not window:
        await _reply(ctx, '补充查询时段', ['请提供使用日期及开始、结束时间，例如“今天14:00-16:00”。'])
        return True
    target, start, end = window or (campus_now().date().isoformat(), '', '')
    await _reply(ctx, '正在查座位', [target + (f' {start}–{end}' if start else '')])
    try:
        from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
        session = Session(launcher_type=LauncherTypes.GROUP if kind == 'group' else LauncherTypes.PERSON,
                          launcher_id=launcher, sender_id=sender)
        result = await query_library(plugin, sender, target, start, end, session=session, query_id=ctx.query_id)
    except Exception:
        result = {'success': False}
    finally:
        await ctx.set_query_var('_henu_card', None)
    if not result.get('success'):
        code = result.get('error_code')
        next_step = '请稍后再试。'
        if code == 'account_not_bound':
            next_step = '请私聊发送“绑定账号”，先绑定校园账号。'
        elif code in {'auth_required', 'login_recovery_failed'}:
            next_step = '校园登录没恢复成功。请私聊发送“我的状态”，不用重新绑定。'
        await _reply(ctx, '没查成功', ['图书馆暂时没返回座位信息。'],
            next_step)
        return True
    rows = result.get('locations') or []
    lines = [target + (f' {start}–{end}' if start else ' · 当天余座')]
    groups = {}
    for row in rows:
        groups.setdefault(row.get('campus') or '校区待确认', []).append(row)
    for campus in sorted(groups, key=lambda x: ({'金明校区': 0, '明伦校区': 1}.get(x, 2), x)):
        lines.append('**' + escape(campus, 60) + '**')
        for row in groups[campus]:
            value = row.get('available_count')
            label = f'剩余 {value} 座' if type(value) is int and value >= 0 else '余座未知'
            name = str(row.get('location') or '未命名区域').translate(str.maketrans('()', '（）'))
            lines.append(escape(name, 100) + ' · ' + label)
    if not rows:
        lines.append('图书馆没有返回区域列表，请稍后再试。')
    from henu_plugin.library_seat_map import region_actions
    buttons = await region_actions(plugin, ctx, result)
    await _reply(ctx, '', lines, '私聊可点击下方校区选座。要查具体时段，回复“今天14:00-16:00”。当天余座不代表整个时段都可预约。' if not start else '以上为所选时段的余座；显示“未知”的区域暂时没查到。', buttons=buttons)
    return True
