"""Private, scoped Rain navigation. Reads metadata; writes only via existing confirmation."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from contextvars import ContextVar
import re
import secrets
import shlex
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from henu_plugin.qq_cards import Card, card_chain, scope, escape, result_card
from henu_plugin.qq_buttons import actions, envelope, confirmation_actions

active_selection = ContextVar('rain_entry_selection', default=None)


@dataclass
class Selection:
    version: str
    expires: float
    account: str = ''
    rows: list = field(default_factory=list)
    page: int = 1
    view: str = 'available'
    query: str = ''
    detail: dict | None = None
    preview_command: str = ''
    generation: int = 0
    page_size: int = 5


class RainEntry:
    def __init__(self, plugin):
        self.plugin = plugin
        self.selections = OrderedDict()
        self.tasks = {}

    async def tell(self, ctx, card, specs=()):
        wire = await actions(self.plugin, ctx, specs) if specs else None
        for index, page in enumerate(card.pages()):
            await ctx.reply(card_chain(envelope(page, wire) if index == 0 else page))

    async def call(self, ctx, command):
        from components.cli_tools.henu_cli_safe import HenuCliSafe
        from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
        tool = HenuCliSafe()
        tool.plugin = self.plugin
        session = Session(sender_id=ctx.event.sender_id, launcher_id=ctx.event.launcher_id,
                          launcher_type=LauncherTypes.PERSON)
        result = await tool.call({'command': command}, session, ctx.query_id)
        return result

    def valid(self, owner):
        current = self.selections.get(owner)
        return current if current and current.expires > time.monotonic() else None

    async def handle(self, ctx, text):
        text = text.strip().rstrip('。！？!?')
        text = re.sub(r'^(?:请|帮我|我想)\s*(?=(?:开始考试|开始测验|查看雨课堂|看看雨课堂))', '', text)
        if text in {'查看雨课堂', '看看雨课堂'}:
            text = '雨课堂'
        known = {'雨课堂', '雨课堂首页', '开始考试', '开始测验', '参加考试', '雨课堂考试', '测评列表',
            '雨课堂状态', '雨课堂配置', '雨课堂帮助', '雨课堂课程', '测评下一页', '测评上一页', '全部测评',
            '开始这份测评', '开始这份考试', '开始这份测验', '继续这份测评', '查看当前测评'}
        choice = re.fullmatch(r'(?:选择|选|查看)(?:测评)?第?\s*([1-9]\d{0,2})\s*(?:项|个|份)?', text)
        search = re.fullmatch(r'(?:选择|查看|查找测评)\s+(.{1,128})', text)
        visible = any(text in {r.get('title'), r.get('course_name'), r.get('classroom_label')}
            for state in self.selections.values() for r in state.rows)
        if text not in known and not choice and not search and not visible:
            return False
        getter = getattr(ctx, 'get_bot_uuid', None)
        owner = scope(str(await getter() or ''), ctx.event) if callable(getter) else None
        current = self.valid(owner)
        operation, value = '', ''
        if text in {'雨课堂', '雨课堂首页'}:
            operation = 'home'
        elif text in {'开始考试', '开始测验', '参加考试', '雨课堂考试', '测评列表'}:
            operation = 'browse'
        elif text in {'雨课堂状态', '雨课堂配置', '雨课堂帮助', '雨课堂课程'}:
            operation, value = 'command', {'雨课堂状态': 'yuketang status',
                '雨课堂配置': 'yuketang config show', '雨课堂帮助': 'help yuketang',
                '雨课堂课程': 'yuketang courses'}[text]
        elif text in {'测评下一页', '测评上一页', '全部测评'}:
            operation, value = 'page', text
        elif text in {'开始这份测评', '开始这份考试', '开始这份测验', '继续这份测评', '查看当前测评'}:
            operation = 'preview' if text.startswith('开始') else 'refresh'
        else:
            match = re.fullmatch(r'(?:选择|选|查看)(?:测评)?第?\s*([1-9]\d{0,2})\s*(?:项|个|份)?', text)
            if match:
                operation, value = 'select', int(match[1])
            elif current:
                match = re.fullmatch(r'(?:选择|查看|查找测评)\s+(.{1,128})', text)
                candidates = [(current.page - 1) * current.page_size + i + 1 for i, row in enumerate(current.rows)
                    if text in {row.get('title'), row.get('course_name'), row.get('classroom_label')}]
                if match:
                    operation, value = 'search', match[1].strip()
                elif candidates:
                    operation, value = 'select', candidates[0] if len(candidates) == 1 else text
        if not operation:
            return False
        if getattr(ctx, 'is_prevent_postorder', False):
            return True
        ctx.prevent_default()
        ctx.prevent_postorder()
        if not owner or owner[1] != 'person':
            await self.tell(ctx, Card('雨课堂', '请在私聊继续', ('个人课程和测评仅在本人私聊中展示。',)))
            return True
        return await self.launch(ctx, owner, operation, value)

    async def launch(self, ctx, owner, operation, value=''):
        key = (*owner, ctx.query_id)
        if key in self.tasks:
            return True
        # Serialize state changes with confirmed writes, but never hold this lock
        # across a CLI call: QuizFlow uses the same per-account lock.
        from components.cli_tools.base import _resolve_storage_key
        storage_owner = _resolve_storage_key(SimpleNamespace(sender_id=owner[3], launcher_id=owner[2]), {})
        locks = getattr(self.plugin, '_quiz_operation_locks', None)
        if locks is None:
            locks = self.plugin._quiz_operation_locks = {}
        previous = None
        async with locks.setdefault(storage_owner, asyncio.Lock()):
            state = self.valid(owner)
            if operation in {'home', 'browse', 'search', 'page'}:
                old = state
                state = Selection(secrets.token_urlsafe(12), time.monotonic() + 300)
                if operation == 'page' and old:
                    state.page = max(1, old.page + (1 if value == '测评下一页' else -1))
                    state.view, state.query = old.view, old.query
                    if value == '全部测评':
                        state.page, state.view = 1, 'all'
                self.selections[owner] = state
                self.selections.move_to_end(owner)
                while len(self.selections) > 128:
                    self.selections.popitem(last=False)
            elif state and operation in {'select', 'refresh', 'preview'}:
                previous = deepcopy(state.detail)
                state.generation += 1
                state.preview_command = ''
                if operation == 'select':
                    state.detail = None
            generation = state.generation if state else 0
        if operation != 'command' and state is None:
            await self.tell(ctx, Card('雨课堂', '选择已过期', ('请发送“开始考试”重新读取列表。',)))
            return True
        # ACK before starting the read; no operation can outlive an undelivered ACK.
        await self.tell(ctx, Card('雨课堂', '正在核对', ('正在读取当前账号的测评信息，请稍候。',)))
        # Query RPCs and replies require the originating Host query route. The
        # Host removes ordinary natural-language queries when this handler
        # returns, so this work must finish within the listener invocation.
        task = asyncio.create_task(self.run(ctx, owner, state, operation, value, generation, previous))
        self.tasks[key] = task
        if len(self.tasks) > 512:
            self.tasks = {k: v for k, v in self.tasks.items() if not v.done()}
        try:
            await asyncio.wait_for(task, timeout=120)
        except asyncio.TimeoutError:
            if self.selections.get(owner) is state:
                self.selections.pop(owner, None)
            await self.tell(ctx, Card('雨课堂', '查询超时',
                ('本次查询未完成，请重新查询测评列表；没有自动开始、保存或交卷。',)))
        return True

    async def run(self, ctx, owner, state, operation, value, generation, previous):
        try:
            if operation == 'home':
                result = await self.call(ctx, 'yuketang status')
                if self.selections.get(owner) is not state:
                    return
                await self.tell(ctx, Card('雨课堂', '选择你要办理的事', (
                    '考试与测验：先选目标、核对限制，再开始。',
                    '默认仅保存答案；正式交卷需要独立本人授权。',
                    '配置或监听成功不代表后台已答题，保存和交卷以官方核验为准。',
                    escape(result.get('reply_text') or result.get('msg') or '登录状态未核验', 650),
                ), '发送“开始考试”或点击下方按钮。'), [
                    ('开始考试', {'kind': 'rain_nav', 'action': 'browse'}),
                    ('课程班级', {'kind': 'rain_nav', 'action': 'courses'}),
                    ('登录雨课堂', {'kind': 'rain_nav', 'action': 'login'}),
                    ('查看状态', {'kind': 'rain_nav', 'action': 'status'}),
                    ('查看配置', {'kind': 'rain_nav', 'action': 'config'}),
                    ('使用帮助', {'kind': 'rain_nav', 'action': 'help'}),
                ])
            elif operation in {'browse', 'search', 'page'}:
                page, view, query = 1, 'available', ''
                if operation == 'page':
                    page, view, query = state.page, state.view, state.query
                elif operation == 'search':
                    query, view = value, 'all'
                command = f'yuketang browse --page {page} --view {view}'
                if query:
                    command += ' --query ' + shlex.quote(query)
                result = await self.call(ctx, command)
                if self.selections.get(owner) is not state:
                    return
                if not result.get('success'):
                    await self.tell(ctx, result_card(result, 'yuketang_browse'))
                    return
                state.account = result['account_revision']
                state.rows, state.page, state.view, state.query = result['activities'], page, view, query
                state.page_size = result.get('page_size', 5)
                rows = state.rows
                offset = (page - 1) * state.page_size
                specs = [(f'查看第{i}项', {'kind': 'rain_select', 'version': state.version, 'index': i})
                         for i in range(offset + 1, offset + len(rows) + 1)]
                if page > 1:
                    specs.append(('上一页测评', {'kind': 'rain_page', 'version': state.version, 'direction': -1}))
                if result.get('has_more'):
                    specs.append(('下一页测评', {'kind': 'rain_page', 'version': state.version, 'direction': 1}))
                specs.append(('查看历史', {'kind': 'rain_page', 'version': state.version, 'direction': 0}))
                await self.tell(ctx, result_card(result, 'yuketang_browse'), specs)
            elif operation in {'select', 'refresh', 'preview'}:
                if operation == 'select':
                    if not isinstance(value, int):
                        await self.tell(ctx, Card('选择测评', '有多个候选',
                            ('同一课程或标题对应多个目标，请按本页序号选择准确班级。',)))
                        return
                    offset = (state.page - 1) * state.page_size
                    if not offset < value <= offset + len(state.rows):
                        await self.tell(ctx, Card('选择测评', '序号不在本页', ('请使用本页列出的序号，或发送“测评下一页”。',)))
                        return
                    selected = state.rows[value - offset - 1]
                else:
                    selected = (previous or {}).get('activity')
                    if not selected:
                        await self.tell(ctx, Card('选择测评', '尚未选定目标', ('请先按列表序号或标题选择一份测评。',)))
                        return
                detail = await self.call(ctx, selected['detail_command'])
                if not self.current(owner, state, generation):
                    return
                if not detail.get('success'):
                    await self.tell(ctx, result_card(detail, 'yuketang_activity_show'))
                    return
                if detail.get('account_revision') != state.account:
                    self.selections.pop(owner, None)
                    await self.tell(ctx, Card('选择测评', '登录账号已变化', ('旧列表已失效，请发送“开始考试”重新查询。',)))
                    return
                row = detail['activity']
                state.detail = detail
                if operation == 'preview' and previous:
                    fields = ('classroom_id', 'assessment_type', 'assessment_id', 'title',
                              'deadline_china', 'question_count', 'limit_seconds', 'status')
                    if any(previous['activity'].get(k) != row.get(k) for k in fields):
                        await self.tell(ctx, Card('测评信息已更新', '请重新核对',
                            ('测评状态或限制已变化，未创建开始请求；请先查看最新详情。',)))
                        await self.detail_card(ctx, state)
                        return
                    command = detail.get('start_command')
                    if command and row.get('assessment_type') == 'quiz' and row.get('can_start'):
                        token = active_selection.set({'owner': list(owner), 'version': state.version,
                            'generation': generation, 'target': self.target(row)})
                        try:
                            result = await self.call(ctx, command)
                        finally:
                            active_selection.reset(token)
                        if not self.current(owner, state, generation):
                            return
                        result['activity'] = row
                        await self.confirmation_card(ctx, result)
                        if result.get('confirmation_required') and self.current(owner, state, generation):
                            state.preview_command = result.get('canonical_command', '')
                        return
                await self.detail_card(ctx, state)
            elif operation == 'command':
                result = await self.call(ctx, value)
                await self.tell(ctx, result_card(result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('[henu.rain_entry] ' + type(exc).__name__, flush=True)
            await self.tell(ctx, Card('雨课堂', '本次未完成',
                ('查询或确认结果未能核实，请重新查询；没有自动重试任何答题或交卷操作。',)))

    def current(self, owner, state, generation):
        return (self.valid(owner) is state and state.generation == generation)

    @staticmethod
    def target(row):
        return [str(row.get(key, '')) for key in ('classroom_id', 'assessment_type', 'assessment_id')]

    async def detail_card(self, ctx, state):
        result = state.detail
        row = result['activity']
        specs = []
        if row.get('can_start') and row.get('assessment_type') == 'quiz':
            specs.append(('查看开始预览', {'kind': 'rain_preview', 'version': state.version, 'generation': state.generation}))
        specs.append(('刷新状态', {'kind': 'rain_refresh', 'version': state.version, 'generation': state.generation}))
        card = result_card(result, 'yuketang_activity_show')
        url = result.get('official_url') or row.get('official_url')
        if self.valid_url(url, row):
            # Validated constants and numeric IDs only; external titles cannot form a URL.
            card = Card(card.title, card.status, (*card.lines, '', f'[进入雨课堂官方页面]({url})'),
                'quiz可发送“开始这份测评”查看预览；exam请本人从官方页面开始。'
                if row.get('status') in (0, 1) else '此活动只读，不会重新开始或重复交卷。')
        await self.tell(ctx, card, specs)

    @staticmethod
    def valid_url(url, row):
        if not isinstance(url, str):
            return False
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.netloc in {'www.yuketang.cn', 'pro.yuketang.cn'}
            and parsed.path == f'/v2/web/{"studentQuiz_web" if row.get("assessment_type") == "quiz" else "exam"}/{row.get("classroom_id")}/{row.get("assessment_id")}'
            and not parsed.query and not parsed.fragment)

    async def confirmation_card(self, ctx, result):
        # The existing CLI creates/validates the durable pending token. This entry never confirms it.
        if not result.get('confirmation_required'):
            await self.tell(ctx, result_card(result))
            return
        command = result.get('canonical_command')
        if not command:
            raise ValueError('confirmation target not bound')
        from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
        session = Session(sender_id=ctx.event.sender_id, launcher_id=ctx.event.launcher_id, launcher_type=LauncherTypes.PERSON)
        wire = await confirmation_actions(self.plugin, str(await ctx.get_bot_uuid() or ''),
            session, ctx.query_id, expected_command=command)
        if not wire:
            raise ValueError('confirmation not available')
        for page in result_card(result).pages():
            await ctx.reply(card_chain(envelope(page, wire)))


def entry(plugin):
    if not isinstance(getattr(plugin, '_rain_entry', None), RainEntry):
        plugin._rain_entry = RainEntry(plugin)
    return plugin._rain_entry


async def handle_rain_text(plugin, ctx, text):
    return await entry(plugin).handle(ctx, text)


async def dispatch_rain(plugin, ctx, payload, *, receipt=None):
    bot = str(await ctx.get_bot_uuid() or '')
    if receipt is None or not receipt.matches(ctx, bot, payload):
        return
    coordinator = entry(plugin)
    owner = scope(bot, ctx.event)
    if payload['kind'] == 'rain_nav':
        action = payload.get('action')
        if action == 'login':
            from henu_plugin.yuketang_login import YuketangLoginCoordinator
            await YuketangLoginCoordinator(SimpleNamespace(plugin=plugin)).handle(ctx, is_group=False, command='yuketang login')
            return
        commands = {'status': 'yuketang status', 'config': 'yuketang config show',
                    'courses': 'yuketang courses', 'help': 'help yuketang'}
        await coordinator.launch(ctx, owner, 'browse' if action == 'browse' else 'command', commands.get(action, 'help yuketang'))
        return
    state = coordinator.valid(owner)
    if not state or state.version != payload.get('version'):
        await coordinator.tell(ctx, Card('雨课堂', '选择已失效', ('列表已更新或过期，请发送“开始考试”重新查询。',)))
        return
    kind = payload['kind']
    if kind == 'rain_select':
        await coordinator.launch(ctx, owner, 'select', payload.get('index'))
    elif kind == 'rain_page':
        value = '全部测评' if payload.get('direction') == 0 else '测评下一页' if payload.get('direction') == 1 else '测评上一页'
        await coordinator.launch(ctx, owner, 'page', value)
    elif kind in {'rain_preview', 'rain_refresh'}:
        if payload.get('generation') != state.generation:
            await coordinator.tell(ctx, Card('雨课堂', '详情已更新', ('请使用当前测评详情的按钮。',)))
            return
        await coordinator.launch(ctx, owner, 'preview' if kind == 'rain_preview' else 'refresh')
