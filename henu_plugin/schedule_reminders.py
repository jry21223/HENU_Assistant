"""Opt-in, durable, at-most-once QQ class reminders (campus timezone)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from henu_plugin.qq_cards import Card, card_chain, escape, text_card

ZONE = ZoneInfo('Asia/Shanghai')
KEY = 'schedule_reminders:v1'
logger = logging.getLogger(__name__)


def valid_period_clock(clock):
    if not isinstance(clock, dict) or not clock:
        return False
    for key, entry in clock.items():
        if not str(key).isdigit() or not isinstance(entry, dict):
            return False
        start, end = entry.get('start', ''), entry.get('end', '')
        if not all(isinstance(t, str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in (start, end)) or start >= end:
            return False
    return True


def due_courses(result: dict, now: datetime) -> list[tuple[str, dict]]:
    """Only use week-filtered results and the saved period clock, never guessed times."""
    from henu_mcp.tools.server_impl import _course_with_clock
    if not result.get('success') or result.get('week_filter_applied') is not True:
        return []
    due = {}
    for row in result.get('day_schedule') or []:
        timed = _course_with_clock(row, result.get('period_times') or {})
        if not timed:
            continue
        start = now.replace(hour=timed['clock_start_minutes'] // 60,
                            minute=timed['clock_start_minutes'] % 60, second=0, microsecond=0)
        lag = (now - (start - timedelta(minutes=15))).total_seconds()
        if not 0 <= lag < 120 or now >= start:
            continue
        # A contiguous lesson is one schedule row; duplicate copies share a key.
        key = hashlib.sha256(json.dumps([now.date().isoformat(), row.get('course'),
                            timed['clock_start'], row.get('location')], ensure_ascii=False).encode()).hexdigest()
        due[key] = timed
    return list(due.items())


class ScheduleReminders:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = asyncio.Lock()
        self.task = None
        self.refresh_task = None

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        if self.refresh_task is None or self.refresh_task.done():
            self.refresh_task = asyncio.create_task(self.run_refresh())

    async def read(self):
        try:
            raw = await self.plugin.get_plugin_storage(KEY)
        except Exception as exc:
            # SDK signals missing keys with the same documented errors as adapter.
            from langbot_plugin.entities.io.errors import ActionCallError
            missing = isinstance(exc, (KeyError, FileNotFoundError)) or (
                isinstance(exc, ActionCallError) and re.fullmatch(
                    r'(?:ActionCallError: )*Storage with key ' + re.escape(KEY) + r' not found', str(exc)) is not None)
            if missing:
                return {}
            raise
        data = json.loads(raw or '{}')
        if not isinstance(data, dict):
            raise ValueError('invalid reminder registry')
        return data

    async def write(self, data):
        await self.plugin.set_plugin_storage(KEY, json.dumps(data, ensure_ascii=False).encode())

    async def query(self, sender):
        from henu_plugin.reminder_schedule import read_saved_schedule
        from henu_plugin.storage_adapter import PluginStorageAdapter
        adapter = PluginStorageAdapter(self.plugin, sender, read_only=True)
        try:
            paths = await adapter.load_all()
            saved_clock = json.loads((paths.shared_data_dir / "period_time_config.json").read_text())
            if not valid_period_clock(saved_clock):
                return {"success": False, "error_code": "period_clock_unavailable"}
            result = read_saved_schedule(paths, datetime.now(ZONE))
            actual = result.get('period_times') or {}
            if not actual or any(saved_clock.get(key) != value for key, value in actual.items()):
                return {'success': False, 'error_code': 'period_clock_mismatch'}
            return result
        finally:
            await adapter.abort()

    async def refresh_tick(self, now=None):
        from henu_plugin.reminder_schedule import refresh_if_due
        from henu_plugin.storage_adapter import PluginStorageAdapter
        from henu_plugin.service import set_current_user_paths
        for sender, item in (await self.read()).items():
            if not item.get('enabled'):
                continue
            adapter = PluginStorageAdapter(self.plugin, sender)
            try:
                paths = await adapter.load_all()
                set_current_user_paths(paths)
                await refresh_if_due(self.plugin, sender, adapter, paths, now or datetime.now(ZONE))
            except Exception:
                logger.warning('Class reminder refresh skipped; saved schedule retained')
            finally:
                set_current_user_paths(None)
                await adapter.abort()

    async def handle(self, ctx, text):
        if text not in {'课表推送', '课前提醒', '开启课前提醒', '关闭课前提醒', '测试课前提醒'}:
            return False
        kind = getattr(getattr(ctx.event, 'launcher_type', ''), 'value', getattr(ctx.event, 'launcher_type', ''))
        ctx.prevent_default()
        ctx.prevent_postorder()
        sender = str(getattr(ctx.event, 'sender_id', '') or '')
        if kind != 'person' or not sender:
            await ctx.reply(card_chain(text_card('课前提醒', '请在机器人私聊中设置个人课前提醒。')[0]))
            return True
        try:
            async with self.lock:
                data = await self.read()
                item = data.get(sender, {})
                if text == '开启课前提醒':
                    result = await self.query(sender)
                    if not result.get('success') or not result.get('week_filter_applied') or not result.get('period_times'):
                        raise ValueError('schedule or clock unavailable')
                    bot = str(await ctx.get_bot_uuid() or '')
                    if not bot:
                        raise ValueError('missing bot route')
                    item.update(enabled=True, bot=bot, journal=item.get('journal', {}))
                    data[sender] = item
                    await self.write(data)
                elif text == '关闭课前提醒':
                    item.update(enabled=False)
                    item.pop('test_due', None)
                    data[sender] = item
                    await self.write(data)
                elif text == '测试课前提醒':
                    if not item.get('enabled'):
                        await ctx.reply(card_chain(text_card('课前提醒', '请先发送“开启课前提醒”。')[0]))
                        return True
                    item['test_due'] = (datetime.now(ZONE) + timedelta(seconds=45)).isoformat()
                    data[sender] = item
                    await self.write(data)
                state = '已开启 · 课前15分钟' if item.get('enabled') else '未开启'
                message = ('测试已安排，约45–75秒后发送一条明确标注的测试提醒。' if text == '测试课前提醒' else
                           '每次上课前15分钟提醒，连堂课只提醒一次。\n课表每天最多自动更新一次；更新失败后暂停自动更新，请手动同步课表恢复。\n发“关闭课前提醒”可关闭，发“测试课前提醒”试一下能否收到。')
                last = item.get('last_delivery')
                if last:
                    message += '\n最近发送：' + {'sent': '接口已受理', 'unknown': '结果未确认，不自动重发'}.get(last, last)
                message += '\n没收到时，请检查QQ通知设置。'
            await ctx.reply(card_chain(Card('课前提醒', state, (escape(message),)).pages()[0]))
        except Exception:
            await ctx.reply(card_chain(text_card('课前提醒', '设置未完成，请先同步课表并核对节次时间，稍后重试。')[0]))
        return True

    async def tick(self, now=None):
        now = now or datetime.now(ZONE)
        async with self.lock:
            data = await self.read()
            for sender, item in data.items():
                if not item.get('enabled'):
                    continue
                try:
                    journal = item.setdefault('journal', {})
                    # Keep seven days of dedupe receipts, including uncertain sends.
                    journal = {k: v for k, v in journal.items() if v['date'] >= (now.date() - timedelta(days=7)).isoformat()}
                    item['journal'] = journal
                    events = []
                    test_due = item.get('test_due')
                    if test_due and datetime.fromisoformat(test_due) <= now:
                        item.pop('test_due')
                        events.append(('test:' + test_due, Card('课前提醒测试', '后台定时测试 · 非真实上课提醒', ('这条测试由后台调度发送，用于确认QQ提醒通道。实际课程按教学周，在上课前15分钟提醒。',))))
                    try:
                        result = await self.query(sender)
                    except Exception:
                        result = {}
                    for key, course in due_courses(result, now):
                        card = Card('还有15分钟上课', now.strftime('%m月%d日') + ' · 课前提醒', (
                            '**' + escape(course.get('course') or '课程') + '**',
                            escape(course.get('clock_start', '') + '–' + course.get('clock_end', '') + ' · ' + str(course.get('period') or '')),
                            '地点：' + escape(course.get('location') or '未标注'),
                        ))
                        events.append((key, card))
                    for key, card in events:
                        if key in journal:
                            continue
                        journal[key] = {'date': now.date().isoformat(), 'status': 'unknown'}
                        item['last_delivery'] = 'unknown'
                        # Persist claim BEFORE sending. Timeout/crash is never auto-retried.
                        await self.write(data)
                        try:
                            await asyncio.wait_for(self.plugin.send_message(item['bot'], 'person', sender, card_chain(card.pages()[0])), timeout=15)
                        except Exception:
                            logger.warning('Class reminder delivery unconfirmed; automatic resend disabled')
                            continue
                        journal[key]['status'] = 'sent'
                        item['last_delivery'] = 'sent'
                        await self.write(data)
                except Exception:
                    logger.warning('Class reminder scan failed; user data unchanged')

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning('Class reminder registry unavailable; scan skipped')
            await asyncio.sleep(30)

    async def run_refresh(self):
        while True:
            try:
                await self.refresh_tick()
            except Exception:
                logger.warning('Class reminder refresh registry unavailable; sync skipped')
            await asyncio.sleep(60 * 60)
