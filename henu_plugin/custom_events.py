"""Private custom events, independent of campus credentials and school schedules."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shlex
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from henu_plugin.background import create_background_task
from henu_plugin.qq_cards import card_chain, text_card
from henu_plugin.reminder_schedule import _read_guard

ZONE = ZoneInfo('Asia/Shanghai')
KEY = 'custom_events:v1'
logger = logging.getLogger(__name__)
HELP = '''自定义日程（北京时间）
可以直接告诉我：“帮我添加明天19点在综合楼2305开班会，提前30分钟提醒”。
支持添加、查看、修改、取消；未给具体日期或时间时会先补问。
默认提前15分钟提醒，也可以指定不提醒；只在私聊中管理、提醒本人。
命令：
event add --title 班会 --start "2026-11-06 19:00" --location 综合楼2305 --remind-minutes 30
event list [--date YYYY-MM-DD] [--offset 0]
event update --id <日程编号> --start "YYYY-MM-DD HH:MM"
event cancel --id <日程编号>
可选 --end "YYYY-MM-DD HH:MM"、--note "备注"；--remind-minutes off 关闭提醒。
提醒受QQ通知通道影响，接口受理不等于实际送达。'''


def is_event_command(command):
    return bool(re.match(r'^event(?:\s|$)', str(command or '').strip(), re.I))


def clock(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}', str(value)):
        raise ValueError('时间请使用 YYYY-MM-DD HH:MM（北京时间），不要省略日期或时间。')
    return datetime.strptime(str(value).replace('T', ' '), '%Y-%m-%d %H:%M').replace(tzinfo=ZONE)


def parse(command):
    args = shlex.split(command)
    if len(args) <= 1 or args[1] in {'help', '--help', '-h'}:
        if len(args) > 2:
            raise ValueError('帮助命令不接受额外参数。')
        return 'help', {}
    action = args[1].lower()
    allowed = {
        'add': {'title', 'start', 'end', 'location', 'note', 'remind-minutes'},
        'update': {'id', 'title', 'start', 'end', 'location', 'note', 'remind-minutes'},
        'cancel': {'id'}, 'list': {'date', 'offset'},
    }
    if action not in allowed:
        raise ValueError('未知日程命令，请用 event help 查看用法。')
    options = {}
    tail = args[2:]
    if len(tail) % 2:
        raise ValueError('每个日程参数都需要一个值，含空格的内容请加引号。')
    for flag, value in zip(tail[::2], tail[1::2]):
        name = flag.removeprefix('--')
        if not flag.startswith('--') or name not in allowed[action] or name in options:
            raise ValueError('包含未知或重复的日程参数。')
        options[name] = value
    return action, options


def describe(row):
    start = datetime.fromisoformat(row['start']).strftime('%Y-%m-%d %H:%M')
    end = (' 至 ' + datetime.fromisoformat(row['end']).strftime('%m-%d %H:%M')) if row.get('end') else ''
    reminder = row['remind_minutes']
    delivery = {'sent': '最近提醒接口已受理', 'unknown': '最近提醒结果未知，不自动重发',
                'missed': '提醒时间已过，未补发'}.get(row.get('delivery'), '')
    return '\n'.join(filter(None, [f"{row['id']} · {row['title']}", start + end + '（北京时间）',
        '地点：' + (row.get('location') or '未设置'),
        '不提醒' if reminder is None else f'提前{reminder}分钟提醒',
        ('备注：' + row['note']) if row.get('note') else '', delivery]))


class CustomEvents:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = asyncio.Lock()
        self.task = None

    def start(self):
        if self.task is None or self.task.done():
            self.task = create_background_task(self.plugin, self.run())

    async def read(self):
        # Strict missing-key detection: storage failures/corruption must not reset data.
        data = await _read_guard(self.plugin, KEY)
        if any(not isinstance(v, dict) or not isinstance(v.get('events'), dict)
               or not isinstance(v.get('requests'), dict) for v in data.values()):
            raise ValueError('invalid event registry')
        return data

    async def write(self, data):
        await self.plugin.set_plugin_storage(KEY, json.dumps(data, ensure_ascii=False).encode())

    async def execute(self, command, *, sender, bot, kind='person', request_id='', now=None):
        if kind != 'person' or sender in {'', '0', 'None', 'none', 'unknown'} or not bot:
            return self.result(False, '请在机器人私聊中管理个人日程。', 'private_reply_required')
        try:
            action, options = parse(command)
            if action == 'help':
                return self.result(True, HELP)
            now = (now or datetime.now(ZONE)).astimezone(ZONE)
            owner = hashlib.sha256(json.dumps([bot, sender]).encode()).hexdigest()
            async with self.lock:
                data = await self.read()
                user = data.setdefault(owner, {'sender': sender, 'bot': bot, 'events': {}, 'requests': {}})
                rows = user['events']
                if action == 'list':
                    date = options.get('date')
                    if date:
                        datetime.strptime(date, '%Y-%m-%d')
                    offset = int(options.get('offset', '0'))
                    if offset < 0:
                        raise ValueError('offset 必须为非负整数。')
                    selected = sorted((r for r in rows.values() if not r.get('cancelled') and
                        (datetime.fromisoformat(r['start']).date().isoformat() == date if date else
                         datetime.fromisoformat(r.get('end') or r['start']) >= now)), key=lambda r: r['start'])
                    page = selected[offset:offset + 8]
                    message = '\n\n'.join(describe(r) for r in page) or '没有符合条件的自定义日程。'
                    if len(selected) > offset + 8:
                        message += f'\n还有{len(selected)-offset-8}条，继续查询 --offset {offset + 8}。'
                    return {**self.result(True, message), 'events': page, 'total': len(selected)}
                # Retry receipt is scoped to this exact request AND command.
                receipt = hashlib.sha256((str(request_id) + '\0' + shlex.join(shlex.split(command))).encode()).hexdigest()
                if request_id and receipt in user['requests']:
                    return self.result(True, user['requests'][receipt]['message'])
                event_id = options.get('id', '')
                if action in {'update', 'cancel'} and (event_id not in rows or rows[event_id].get('cancelled')):
                    raise ValueError('未找到该日程，请先查看“我的日程”并使用本人日程编号。')
                if action == 'cancel':
                    rows[event_id]['cancelled'] = True
                    message = '已取消日程及后续提醒：\n' + describe(rows[event_id])
                else:
                    if action == 'add':
                        if sum(not r.get('cancelled') and datetime.fromisoformat(r.get('end') or r['start']) >= now for r in rows.values()) >= 100:
                            raise ValueError('最多保留100条未来日程，请先取消不需要的日程。')
                        row = {'id': uuid.uuid4().hex[:12], 'title': '', 'start': '', 'end': '',
                               'location': '', 'note': '', 'remind_minutes': 15}
                    else:
                        if set(options) == {'id'}:
                            raise ValueError('请提供要修改的日程字段。')
                        row = dict(rows[event_id])
                    for field in ('title', 'location', 'note'):
                        if field in options:
                            value = options[field].strip()
                            if len(value) > (500 if field == 'note' else 100) or (field == 'title' and not value):
                                raise ValueError('标题不能为空；标题/地点最多100字，备注最多500字。')
                            row[field] = value
                    for field in ('start', 'end'):
                        if field in options:
                            row[field] = clock(options[field]).isoformat() if options[field] else ''
                    if not row['title'] or not row['start']:
                        raise ValueError('请提供日程名称和具体开始日期、时间。')
                    start = datetime.fromisoformat(row['start'])
                    timing_changed = (action == 'add' or
                        start != datetime.fromisoformat(rows[event_id]['start']))
                    if timing_changed and start <= now:
                        raise ValueError('开始时间必须晚于当前北京时间。')
                    if row['end'] and datetime.fromisoformat(row['end']) <= start:
                        raise ValueError('结束时间必须晚于开始时间。')
                    if 'remind-minutes' in options:
                        value = options['remind-minutes']
                        if value.lower() == 'off':
                            row['remind_minutes'] = None
                        elif not value.isdigit() or not 0 <= int(value) <= 10080:
                            raise ValueError('提前提醒分钟数须为0至10080的整数，或 off 不提醒。')
                        else:
                            row['remind_minutes'] = int(value)
                    timing_changed = (timing_changed or
                        row['remind_minutes'] != rows[event_id]['remind_minutes'])
                    if timing_changed:
                        row.pop('delivery', None)
                        row.pop('claimed_at', None)
                        row.pop('claim_id', None)
                    row['updated_at'] = now.isoformat()
                    rows[row['id']] = row
                    message = ('已添加自定义日程：\n' if action == 'add' else '已修改自定义日程：\n') + describe(row)
                    if row['remind_minutes'] is not None and start - timedelta(minutes=row['remind_minutes']) < now:
                        message += '\n提前提醒时刻已过，将在下一次扫描提醒（约30秒内）。'
                # Keep event and cancellation history; only retry receipts expire.
                user['requests'] = {k: r for k, r in user['requests'].items() if datetime.fromisoformat(r['at']) >= now - timedelta(days=1)}
                if request_id:
                    user['requests'][receipt] = {'message': message, 'at': now.isoformat()}
                await self.write(data)
                return self.result(True, message)
        except ValueError as exc:
            return self.result(False, str(exc), 'invalid_event')
        except Exception:
            logger.warning('Custom event storage unavailable')
            return self.result(False, '日程保存或读取未能确认，请先查看日程核对，不要重复添加。', 'event_storage_unavailable')

    @staticmethod
    def result(success, text, error=''):
        return {'success': success, 'msg': text, 'reply_text': text, 'error_code': error, '_resolved_tool_name': 'custom_event'}

    async def handle(self, ctx, *, is_group=False):
        text = str(getattr(ctx.event, 'text_message', '') or '').strip()
        if text not in {'我的日程', '自定义日程', '日程帮助', '今天日程', '明天日程'} and not is_event_command(text):
            return False
        if getattr(ctx, 'is_prevent_postorder', False):
            return True
        ctx.prevent_default()
        ctx.prevent_postorder()
        if text in {'今天日程', '明天日程'}:
            day = datetime.now(ZONE).date() + timedelta(days=int(text == '明天日程'))
            command = 'event list --date ' + day.isoformat()
        else:
            command = {'我的日程': 'event list', '自定义日程': 'event help', '日程帮助': 'event help'}.get(text, text)
        result = await self.execute(command, sender=str(getattr(ctx.event, 'sender_id', '') or ''),
            bot='' if is_group else str(await ctx.get_bot_uuid() or ''), kind='group' if is_group else 'person',
            request_id=str(getattr(ctx.event, 'message_id', '') or getattr(ctx, 'query_id', '') or ''))
        for page in text_card('自定义日程', result['reply_text']):
            await ctx.reply(card_chain(page))
        return True

    async def tick(self, now=None):
        async with self.lock:
            data = await self.read()
            snapshot_time = (now or datetime.now(ZONE)).astimezone(ZONE)
            candidates = []
            for owner, user in data.items():
                for event_id, row in user['events'].items():
                    if row.get('cancelled') or row.get('delivery') or row['remind_minutes'] is None:
                        continue
                    due = datetime.fromisoformat(row['start']) - timedelta(minutes=row['remind_minutes'])
                    if snapshot_time >= due:
                        candidates.append((owner, event_id))
        for owner, event_id in candidates:
            # Re-read before each claim: pending cancellations and changes must
            # win over the scan's old snapshot while earlier sends are blocked.
            async with self.lock:
                data = await self.read()
                user = data.get(owner)
                row = user['events'].get(event_id) if user else None
                if not row or row.get('cancelled') or row.get('delivery') or row['remind_minutes'] is None:
                    continue
                current = (now or datetime.now(ZONE)).astimezone(ZONE)
                start = datetime.fromisoformat(row['start'])
                due = start - timedelta(minutes=row['remind_minutes'])
                if current < due:
                    continue
                if current > start + timedelta(seconds=60):
                    row['delivery'] = 'missed'
                    await self.write(data)
                    continue
                reminder_text = describe(row)
                claim_id = uuid.uuid4().hex
                row.update(delivery='unknown', claimed_at=current.isoformat(), claim_id=claim_id)
                await self.write(data)  # Durable claim BEFORE external send.
                bot, sender = user['bot'], user['sender']
            try:
                pages = text_card('日程提醒', reminder_text)
                await asyncio.wait_for(self.plugin.send_message(bot, 'person', sender, card_chain(pages[0])), 15)
            except Exception:
                logger.warning('Custom event delivery unconfirmed; automatic resend disabled')
                continue
            async with self.lock:
                data = await self.read()
                user = data.get(owner)
                row = user['events'].get(event_id) if user else None
                # A new timing configuration owns its own delivery lifecycle.
                # A late receipt must not overwrite edits or another claim.
                if row and row.get('claim_id') == claim_id and row.get('delivery') == 'unknown':
                    row['delivery'] = 'sent'
                    await self.write(data)

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning('Custom event scan unavailable; skipped')
            await asyncio.sleep(30)
