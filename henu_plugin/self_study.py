"""Read-only study-room queries; never infer a large-period/small-period mapping."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from henu_plugin.qq_cards import Card, card_chain, escape


def campus_now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))


async def _outside_saved_clock(plugin, now):
    from henu_plugin.schedule_reminders import valid_period_clock
    try:
        clock = json.loads(await plugin.get_plugin_storage('shared:period_time') or b'{}')
    except Exception:
        return False
    if not valid_period_clock(clock):
        return False
    if not set(str(n) for n in range(1, 14)).issubset(clock):
        return False
    # Only establish outside the entire saved teaching day, not gaps or big periods.
    current = now.strftime('%H:%M')
    return current < min(v['start'] for v in clock.values()) or current >= max(v['end'] for v in clock.values())


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
    if not initial:
        for field, value in zip(('campus', 'building', 'period'), location.groups()):
            if value:
                state[field] = value
    state['expires'] = time.monotonic() + 300
    if len(pending) >= 256 and key not in pending:
        pending.pop(next(iter(pending)))
    pending[key] = state
    now = campus_now()
    if initial and await _outside_saved_clock(plugin, now):
        await _reply(ctx, '当前不在已保存的教学时段', [now.strftime('%m月%d日 %H:%M'),
            '现在不在已保存的教学钟点范围内，不能据此认定所有教室空闲或开放。'],
            '如需查询课程占用，请回复校区、教学楼和大节。')
        return True
    if not state.get('period'):
        await _reply(ctx, '需确认当前大节', [
            '现在是第几大节（1–5）？尚未核验五大节与小节钟点的对应关系，不会把其他时段教室当作当前可用。',
            '可直接回复“第2大节”；如需限定地点，可一并回复“金明校区十号楼第2大节”。'])
        return True
    if state.get('building') and not state.get('campus'):
        await _reply(ctx, '补充校区', ['请回复校区，例如“金明校区”。'])
        return True
    campus, building = state.get('campus', ''), state.get('building', '')
    command = f"empty classroom query --view free --period {state['period']}"
    if campus:
        command += f' --campus-text {campus}'
    if building:
        command += f' --building-text {building}'
    else:
        command += ' --freshness cache_only'
    session = Session(launcher_type=LauncherTypes.GROUP if kind == 'group' else LauncherTypes.PERSON,
                      launcher_id=launcher, sender_id=sender)
    await _reply(ctx, '正在查询', [
        '正在读取所选楼栋的该大节课程占用。' if building else '正在读取该大节已有缓存，不启动全校逐栋刷新。'])
    tool = HenuCliSafe()
    tool.plugin = plugin
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
    lines = [now.strftime('%m月%d日') + f" · 本人指定第{state['period']}大节",
             '仅反映课程占用，不保证现场无人或楼门开放。']
    cached = not building or not freshness.get('liveChecked')
    stale = freshness.get('stale') is True
    if stale:
        lines.append('缓存已过期，需重新核验，不能作为当前可用教室。')
        rooms = []
    if not rooms:
        lines.append('没有可核验的该时段教室结果；不能据此判断无空教室。')
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
