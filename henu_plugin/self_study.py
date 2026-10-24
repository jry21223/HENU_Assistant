"""Read-only classroom queries with a campus- and clock-verified current period."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from henu_plugin.qq_cards import Card, card_chain, escape

# Official Kaifeng timetable: https://kfxq.henu.edu.cn/info/1931/2401.htm
# Applies only to Minglun/Jinming and the matching saved thirteen-period clock.
KAIFENG_CLOCK = dict(zip((str(n) for n in range(1, 14)), (
    {'start': start, 'end': end} for start, end in (
        ('08:00', '08:45'), ('08:55', '09:40'), ('10:00', '10:45'),
        ('10:55', '11:40'), ('11:45', '12:30'), ('14:05', '14:50'),
        ('15:00', '15:45'), ('15:55', '16:40'), ('17:00', '17:45'),
        ('17:55', '18:40'), ('19:10', '19:55'), ('20:05', '20:50'), ('20:55', '21:40')))))
KAIFENG_UNITS = (('08:00', '09:40'), ('10:00', '12:30'), ('14:05', '16:40'),
                  ('17:00', '18:40'), ('19:10', '21:40'))


def campus_now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))


async def _saved_clock_matches(plugin):
    try:
        clock = json.loads(await plugin.get_plugin_storage('shared:period_time') or b'{}')
    except Exception:
        return False
    return clock == KAIFENG_CLOCK


def _may_have_current_cache(campus, period, now):
    """Reject empty/stale/other-slot caches before the CLI's login preamble."""
    from itertools import islice
    from campus_core.storage_paths import get_empty_classroom_cache_dir
    from henu_mcp.core.teaching_calendar import teaching_week
    calendar = teaching_week(now)
    if not calendar.get('available'):
        return False
    try:
        folder = get_empty_classroom_cache_dir() / 'query_cache'
        for file in islice(folder.glob('*.json'), 128):
            try:
                stat = file.stat()
                age = time.time() - stat.st_mtime
                if not 0 <= age <= 300 or stat.st_size > 1_000_000:
                    continue
                data = json.loads(file.read_text())
                query = data.get('query', {})
                if (isinstance(query, dict) and query.get('week') == calendar['week']
                        and query.get('dayOfWeek') == now.isoweekday()
                        and query.get('period') == int(period)
                        and query.get('campusName') == campus + '校区'
                        and query.get('buildingCode') == '' and query.get('minCapacity') == 0):
                    return True
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    except OSError:
        pass
    return False


async def _reply(ctx, status, lines, next_step=''):
    for page in Card('找自习室', status, tuple(lines), next_step).pages():
        await ctx.reply(card_chain(page))


async def with_all_day_clock(plugin, result):
    """Use the saved, verified Kaifeng clock; other campuses keep unconverted slots."""
    data = result.get('data')
    if (result.get('success') and isinstance(data, dict) and isinstance(data.get('periods'), list)
            and data.get('campusCode') in {'01', '02'} and await _saved_clock_matches(plugin)):
        slots = []
        for slot in data['periods']:
            slot = dict(slot)
            period = slot.get('period')
            if type(period) is int and 1 <= period <= 5:
                slot['clockStart'], slot['clockEnd'] = KAIFENG_UNITS[period - 1]
            slots.append(slot)
        result = {**result, 'data': {**data, 'periods': slots, 'clockVerified': True}}
    return result


async def all_day_pages(plugin, result):
    from henu_plugin.qq_cards import result_card
    return result_card(await with_all_day_clock(plugin, result), tool='empty_classroom_query').pages()


def _campus_building_choices(campus, word='综合'):
    """Use actual registry names; never reuse a similarly named other campus."""
    from campus_core.resource_registry.registry import search_resources
    from campus_core.resource_registry import preload_seed_if_needed
    preload_seed_if_needed()
    campuses = [r for r in search_resources(query=campus, resource_type='campus', limit=20)
                if campus in r.display_name and r.location.get('campusCode')]
    codes = {r.location['campusCode'] for r in campuses}
    if len(codes) != 1:
        return []
    code = codes.pop()
    return [{'name': r.location.get('buildingName') or r.display_name,
             'campus_code': code, 'building_code': r.location['buildingCode']}
            for r in search_resources(query=word, resource_type='building', campus_code=code, limit=30)
            if r.resource_type == 'building' and r.location.get('campusCode') == code
            and r.location.get('buildingCode')]


async def handle_self_study(plugin, ctx, text):
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    from components.cli_tools.henu_cli_safe import HenuCliSafe

    full_default = re.fullmatch(r'查((?:金明|明伦|郑州龙子湖|郑州|龙子湖)校区[\u4e00-\u9fffA-Za-z0-9]{1,30}(?:楼(?:[1-9]\d?)?|[A-Da-d]座))的空教室', text)
    initial = text in {'找自习室', '空教室查询', '现在呢'} or bool(full_default)
    all_day_choice = text in {'全天', '一整天'}
    number_choice = re.fullmatch(r'[1-9][0-9]?', text)
    location = re.fullmatch(r'(?:(金明|明伦|郑州龙子湖|郑州|龙子湖)(?:校区)?)?([\u4e00-\u9fffA-Za-z0-9]{1,30}(?:楼(?:[1-9]\d?)?|[A-Da-d]座))?(?:第([1-5])大节)?', full_default[1] if full_default else text)
    if not initial and not all_day_choice and not number_choice and (not location or not any(location.groups())):
        return False
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    kind = getattr(ctx.event.launcher_type, 'value', ctx.event.launcher_type)
    sender = str(getattr(ctx.event, 'sender_id', '') or '').strip()
    launcher = str(getattr(ctx.event, 'launcher_id', '') or '').strip()
    if kind not in {'person', 'group'} or not sender or not launcher:
        return False
    bot = str(await ctx.get_bot_uuid() or '')
    if not bot:
        return False
    key = (bot, kind, launcher, sender)
    pending = getattr(plugin, '_self_study_pending', {})
    pending = {k: v for k, v in pending.items() if v['expires'] > time.monotonic()}
    plugin._self_study_pending = pending
    previous = pending.get(key)
    from henu_plugin.study_place import known_place
    remembered = await known_place(plugin, key) if initial else {}
    if text == '现在呢' and not previous and not remembered:
        return False
    if not initial and not previous:
        return False
    if not initial and number_choice and not 1 <= int(text) <= len(previous.get('building_choices', [])):
        return False
    ctx.prevent_default()
    ctx.prevent_postorder()
    state = remembered if initial else dict(previous)
    if initial and previous:
        if previous.get('awaiting_place'):
            state = {field: previous[field] for field in ('campus', 'building', 'campus_code', 'building_code') if field in previous}
            state['awaiting_place'] = True
        elif not state:
            state['campus'] = previous.get('campus', '')
    if all_day_choice:
        state['view'] = 'day_matrix'
        state.pop('period', None)
    elif not initial and number_choice:
        choices = state.get('building_choices', [])
        selected = choices[int(text) - 1]
        state.update(building=selected['name'], campus_code=selected['campus_code'],
                     building_code=selected['building_code'])
        state.pop('building_choices', None)
    elif not initial or full_default:
        for field, value in zip(('campus', 'building', 'period'), location.groups()):
            if value:
                if field == 'campus':
                    from campus_core.resource_registry.alias import normalize_campus_name
                    value = normalize_campus_name(value).removesuffix('校区')
                if field == 'campus' and state.get('campus') != value:
                    state.pop('building', None)
                    state.pop('building_choices', None)
                    state.pop('campus_code', None)
                    state.pop('building_code', None)
                if field == 'building':
                    state.pop('campus_code', None)
                    state.pop('building_code', None)
                    state.pop('building_choices', None)
                state[field] = value
                if field in {'campus', 'building'}:
                    state['awaiting_place'] = True
                if field == 'period':
                    state['view'] = 'free'
    state['expires'] = time.monotonic() + 300
    if len(pending) >= 256 and key not in pending:
        pending.pop(next(iter(pending)))
    pending[key] = state
    now = campus_now()
    session = Session(launcher_type=LauncherTypes.GROUP if kind == 'group' else LauncherTypes.PERSON,
                      launcher_id=launcher, sender_id=sender)
    tool = HenuCliSafe()
    tool.plugin = plugin
    if not state.get('campus'):
        await _reply(ctx, '确认校区', ['在哪个校区？'], '请回复“金明”“明伦”或“郑州”；郑州校区也可回复“龙子湖”。')
        return True
    period = state.get('period')
    all_day = state.get('view') == 'day_matrix'
    automatic = not period and not all_day
    target_date = now.date()
    recommendation = ''
    if automatic and state['campus'] in {'金明', '明伦'} and await _saved_clock_matches(plugin):
        current = now.strftime('%H:%M')
        period = next((str(n) for n, (start, end) in enumerate(KAIFENG_UNITS, 1) if current < end), None)
        if not period:
            target_date += timedelta(days=1)
            period = '1'
        start, end = KAIFENG_UNITS[int(period) - 1]
        label = '当前时段' if target_date == now.date() and start <= current < end else '下一教学时段'
        recommendation = f'{label}推荐：{target_date:%m月%d日} {start}–{end}（第{period}大节）。仅反映课程占用，现场开放以实际为准。'
    if not period and not all_day:
        await _reply(ctx, '需确认当前大节', [
            '可回复“全天”查看全部5个大节，也可选择第几大节（1–5）。',
            '可直接回复“第2大节”；如需限定地点，可一并回复“明伦校区十号楼第2大节”。'])
        return True
    if state.get('building') and not state.get('campus'):
        await _reply(ctx, '补充校区', ['请回复校区，例如“金明校区”。'])
        return True
    campus, building = state.get('campus', ''), state.get('building', '')
    if building in {'综合楼', '综合教学楼', '金明综合楼', '明伦综合楼'}:
        choices = _campus_building_choices(campus)
        if len(choices) != 1:
            state['building_choices'] = choices
            if not automatic:
                state['period'] = period
            lines = [f'{campus}有这几栋综合楼，你想查哪一栋？'] if choices else [f'没找到{campus}的“{building}”。请换一个具体楼名。']
            lines.extend(f'{i}. {escape(row["name"])}' for i, row in enumerate(choices, 1))
            await _reply(ctx, '', lines, '回复序号或楼名即可。' if choices else '')
            return True
        selected = choices[0]
        state.update(building=selected['name'], campus_code=selected['campus_code'], building_code=selected['building_code'])
        building = state['building']
    if not building and (all_day or not _may_have_current_cache(campus, period, now)):
        await _reply(ctx, '需要查询地点', [
            '查哪栋教学楼？'], '比如“十号楼”或“综合楼”。')
        return True
    state['awaiting_place'] = False
    command = 'empty classroom query --view day_matrix' if all_day else f"empty classroom query --view free --period {period}"
    if all_day or automatic:
        command += f' --saved-session-only true --target-date {target_date.isoformat()}'
    if campus:
        command += f' --campus-text {campus}'
    if state.get('campus_code') and state.get('building_code'):
        command += f" --campus-code {state['campus_code']} --building-code {state['building_code']}"
    elif building:
        command += f' --building-text {building}'
    else:
        command += ' --freshness cache_only'
    await _reply(ctx, '正在查询', [
        f'正在查{building}全天各大节的课程占用。' if all_day else f'正在查{building}第{period}大节的空教室。' if building else f'正在查第{period}大节的空教室。'])
    staged_page = None
    try:
        result = await tool.call({'command': command}, session, ctx.query_id)
        try:
            variables = await ctx.get_query_vars()
            staged = variables.get('_henu_card') if isinstance(variables, dict) else None
            from henu_plugin.qq_cards import PREFIX
            if (isinstance(staged, dict) and staged.get('owner') == list(key)
                    and isinstance(staged.get('page'), str) and staged['page'].startswith(PREFIX)):
                staged_page = staged['page']
        except Exception:
            pass  # The structured result remains usable when the card API is unavailable.
    except Exception:
        await _reply(ctx, '查询未完成', ['校园服务暂时无法读取，请稍后重试；没有提交预约。'])
        return True
    finally:
        await ctx.set_query_var('_henu_card', None)
    if staged_page:
        if recommendation:
            staged_page += '\n\n' + recommendation
        await ctx.reply(card_chain(staged_page))
        return True
    if not result.get('success'):
        message = str(result.get('msg') or result.get('reply_text') or '校园服务未能完成查询。')
        auth = result.get('error_code') in {'auth_required', 'account_not_bound', 'login_required'} or any(
            word in message for word in ('登录', '账号', '密码', 'setup_account'))
        await _reply(ctx, '查询未完成', [escape(message, 600)],
            ('请在私聊发送“绑定账号”，按提示绑定校园账号。' if result.get('error_code') == 'account_not_bound' else '请在私聊检查“我的状态”，恢复校园登录后重试。') if auth else '请核对查询条件；校历或学期无法核验时请联系维护者，稍后重试。')
        return True
    if all_day:
        for page in await all_day_pages(plugin, result):
            await ctx.reply(card_chain(page))
        return True
    if 'rooms' not in result and type(result.get('total')) is int and result['total'] > 0:
        await _reply(ctx, '清单暂未读取完整', [
            f"学校返回了{result['total']}间教室，但本次清单暂时无法读取；这不代表没有空教室。"],
            '请重新查询该教学楼。')
        return True
    rooms = result.get('rooms') or []
    freshness = result.get('freshness') or {}
    lines = [recommendation if automatic else now.strftime('%m月%d日') + f' · 本人指定第{period}大节',
             '仅反映课程占用，不保证现场无人或楼门开放。']
    cached = not building or not freshness.get('liveChecked')
    stale = freshness.get('stale') is True
    if stale:
        lines.append('缓存已过期，需重新核验，不能作为当前可用教室。')
        rooms = []
    if not rooms:
        lines.append('没有可核验的该时段教室结果；不能据此判断无空教室。')
        if not building:
            lines.append('请回复教学楼，例如“十号楼”，继续查询该楼栋。')
    else:
        for room in rooms[:10]:
            name = room.get('roomName') or room.get('room_name') or '教室未标名'
            place = ' · '.join(str(room.get(k) or '') for k in ('campusName', 'buildingName') if room.get(k))
            lines.append(escape(name, 100) + (' · ' + escape(place, 100) if place else ''))
        if len(rooms) > 10:
            lines.append('仅展示前10间，请补充校区和教学楼缩小范围。')
    await _reply(ctx, '过期缓存 · 待核验' if stale else '缓存参考' if cached else '该大节课程占用查询', lines,
        '可回复校区和教学楼继续查询，例如“明伦校区十号楼”。')
    return True
