"""Read-only classroom queries with a campus- and clock-verified current period."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
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


async def _reply(ctx, status, lines, next_step=''):
    for page in Card('找自习室', status, tuple(lines), next_step).pages():
        await ctx.reply(card_chain(page))


async def handle_self_study(plugin, ctx, text):
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    from components.cli_tools.henu_cli_safe import HenuCliSafe

    initial = text in {'找自习室', '空教室查询'}
    location = re.fullmatch(r'(?:(金明|明伦|龙子湖)(?:校区)?)?([\u4e00-\u9fffA-Za-z0-9]{1,30}楼)?(?:第([1-5])大节)?', text)
    if not initial and (not location or not any(location.groups())):
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
    if not initial and not previous:
        return False
    ctx.prevent_default()
    ctx.prevent_postorder()
    state = {} if initial else dict(previous)
    if initial and previous:
        state['campus'] = previous.get('campus', '')
    if not initial:
        for field, value in zip(('campus', 'building', 'period'), location.groups()):
            if value:
                if field == 'campus' and state.get('campus') != value:
                    state.pop('building', None)
                state[field] = value
    state['expires'] = time.monotonic() + 300
    if len(pending) >= 256 and key not in pending:
        pending.pop(next(iter(pending)))
    pending[key] = state
    now = campus_now()
    session = Session(launcher_type=LauncherTypes.GROUP if kind == 'group' else LauncherTypes.PERSON,
                      launcher_id=launcher, sender_id=sender)
    tool = HenuCliSafe()
    tool.plugin = plugin
    if initial and not state.get('campus') and kind == 'person':
        try:
            preferences = await tool.call({'command': 'preferences get'}, session, ctx.query_id)
            values = preferences.get('preferences') or {} if isinstance(preferences, dict) and preferences.get('success') else {}
            value = values.get('campus') if isinstance(values, dict) else None
            if value in {'金明', '明伦', '龙子湖', '金明校区', '明伦校区', '龙子湖校区'}:
                state['campus'] = value.removesuffix('校区')
        except Exception:
            pass  # Missing preferences do not establish a campus.
        finally:
            await ctx.set_query_var('_henu_card', None)
    if not state.get('campus'):
        await _reply(ctx, '确认校区', ['在哪个校区？'], '请回复“金明”“明伦”或“龙子湖”。')
        return True
    period = state.get('period')
    automatic = not period
    if automatic and state['campus'] in {'金明', '明伦'} and await _saved_clock_matches(plugin):
        current = now.strftime('%H:%M')
        period = next((str(n) for n, (start, end) in enumerate(KAIFENG_UNITS, 1) if start <= current < end), None)
        if not period:
            await _reply(ctx, '当前不在教学时段', [now.strftime('%m月%d日 %H:%M'),
                '当前不在该校区教学时段内，不能据此认定所有教室空闲或开放。'],
                '如需查询课程占用，可回复指定大节，例如“第2大节”。')
            return True
    if not period:
        await _reply(ctx, '需确认当前大节', [
            '暂时无法确定当前查询时段，请选择第几大节（1–5）。',
            '可直接回复“第2大节”；如需限定地点，可一并回复“金明校区十号楼第2大节”。'])
        return True
    if state.get('building') and not state.get('campus'):
        await _reply(ctx, '补充校区', ['请回复校区，例如“金明校区”。'])
        return True
    campus, building = state.get('campus', ''), state.get('building', '')
    command = f"empty classroom query --view free --period {period}"
    if campus:
        command += f' --campus-text {campus}'
    if building:
        command += f' --building-text {building}'
    else:
        command += ' --freshness cache_only'
    await _reply(ctx, '正在查询', [
        '正在读取所选楼栋的该大节课程占用。' if building else '正在读取该大节已有缓存，不启动全校逐栋刷新。'])
    try:
        result = await tool.call({'command': command}, session, ctx.query_id)
    except Exception:
        await _reply(ctx, '查询未完成', ['校园服务暂时无法读取，请稍后重试；没有提交预约。'])
        return True
    finally:
        await ctx.set_query_var('_henu_card', None)
    if not result.get('success'):
        message = str(result.get('msg') or result.get('reply_text') or '校园服务未能完成查询。')
        auth = result.get('error_code') in {'auth_required', 'account_not_bound', 'login_required'} or any(
            word in message for word in ('登录', '账号', '密码', 'setup_account'))
        await _reply(ctx, '查询未完成', [escape(message, 600)],
            '请在私聊检查“我的状态”，按“绑定账号”提示恢复校园登录后重试。' if auth else '请核对查询条件；校历或学期无法核验时请联系维护者，稍后重试。')
        return True
    rooms = result.get('rooms') or []
    freshness = result.get('freshness') or {}
    lines = [now.strftime('%m月%d日') + (f' · 当前第{period}大节' if automatic else f' · 本人指定第{period}大节'),
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
        '可回复校区和教学楼继续查询，例如“金明校区十号楼”。')
    return True
