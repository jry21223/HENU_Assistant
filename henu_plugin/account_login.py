"""Pre-model private account intake with query-independent result delivery."""
from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict

from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes, Session

from henu_plugin.cli import inspect_cli_command
from henu_plugin.login_guard import guarded_login
from henu_plugin.qq_cards import card_chain, text_card
from henu_plugin.background import create_background_task


def account_login_intent(text: str) -> bool:
    compact = ' '.join(text.lower().split())
    if compact.startswith(('yuketang ', '雨课堂 ')):
        return False
    if re.match(r'^(?:account|count)\s+(?:set|bind)(?:\b|--)', compact):
        return True
    return any(word in compact for word in ('学号', '账号', '用户名', 'student-id', 'student_id', 'student id', 'username')) and any(
        word in compact for word in ('密码', 'password', 'passwd')
    )


def valid_account_arguments(argv) -> bool:
    values = {'student-id', 'password', 'library-location', 'library-seat-no'}
    flags = {'no-verify-login', 'no-calibrate-period-time'}
    needs_value = False
    for token in argv[2:]:
        if needs_value:
            needs_value = False
            continue
        option, separator, _value = token.partition('=')
        if not option.startswith('--'):
            return False
        name = option[2:].replace('_', '-')
        if name not in values | flags:
            return False
        needs_value = name in values and not separator
    return not needs_value


class AccountLoginCoordinator:
    def __init__(self, listener):
        self.listener = listener
        self.tasks = OrderedDict()
        self.users = {}
        self.last_started = {}
        self.handled = OrderedDict()

    async def handle(self, ctx) -> bool:
        event = ctx.event
        text = str(getattr(event, 'text_message', '') or getattr(event, 'message_chain', '') or '').strip()
        if not account_login_intent(text):
            return False
        ctx.prevent_default()
        ctx.prevent_postorder()
        sender = str(getattr(event, 'sender_id', '') or '').strip()
        key = (sender, ctx.query_id)
        if key in self.handled:
            return True
        self.handled[key] = None
        while len(self.handled) > 1024:
            self.handled.popitem(last=False)
        kind = str(getattr(getattr(event, 'launcher_type', ''), 'value', getattr(event, 'launcher_type', '')))
        if kind != 'person':
            await self.reply(ctx, '账号密码只允许本人私聊操作。本条消息未交给模型，也未执行登录；请撤回群内凭据。')
            return True
        if sender in {'', '0', 'unknown', 'None'}:
            await self.reply(ctx, '无法确认当前私聊发送者身份，本次未执行登录。')
            return True
        spec = inspect_cli_command(text)
        if (spec.error or spec.resolved_tool != 'setup_account' or spec.is_help
                or not valid_account_arguments(spec.argv)
                or any(mark in str(spec.params.get('student_id', '')) for mark in '<>')):
            await self.reply(ctx, "账号命令格式无效。请私聊发送 account set --student-id 学号 --password '密码'；不要填写尖括号。")
            return True
        try:
            bot = str(await ctx.get_bot_uuid() or '')
        except Exception:
            bot = ''
        if not bot or not callable(getattr(self.listener.plugin, 'send_message', None)):
            await self.reply(ctx, '无法确认回复通道，本次未执行登录，请稍后重试。')
            return True
        active = self.users.get(sender)
        if active is not None and not active.done():
            await self.reply(ctx, '已有账号验证正在进行，请等待结果，勿重复发送密码。')
            return True
        if sum(not task.done() for task in self.tasks.values()) >= 32:
            await self.reply(ctx, '账号验证通道繁忙，本次未执行登录，请稍后再试。')
            return True
        now = time.monotonic()
        if now - self.last_started.get(sender, -float('inf')) < 30:
            await self.reply(ctx, '账号验证请求过于频繁，请等待 30 秒后再试。')
            return True
        # Capture routing while the query is alive; background delivery must not
        # use ctx.reply after LangBot has closed the originating invocation.
        start = asyncio.Event()
        task = create_background_task(
            self.listener.plugin, self.run(bot, sender, ctx.query_id, spec.params, start)
        )
        self.tasks[key] = task
        self.users[sender] = task
        self.last_started[sender] = now
        for old_key, old_task in list(self.tasks.items()):
            if len(self.tasks) > 512 and old_task.done():
                self.tasks.pop(old_key)
        self.last_started = {user: when for user, when in self.last_started.items() if now - when < 30}
        try:
            acknowledgement = ('已安全接收账号绑定命令，正在验证；请勿重复发送密码。'
                               if spec.params.get('verify_login', True)
                               else '已安全接收账号绑定命令，正在仅保存配置，不验证学校登录。')
            await self.reply(ctx, acknowledgement)
        except (Exception, asyncio.CancelledError) as error:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if self.users.get(sender) is task:
                self.users.pop(sender, None)
            if isinstance(error, asyncio.CancelledError):
                raise
            return True
        start.set()
        return True

    async def run(self, bot, sender, query_id, params, start):
        try:
            await start.wait()
            session = Session(launcher_type=LauncherTypes.PERSON, launcher_id=sender, sender_id=sender)
            hint = {'sender_id': sender, 'launcher_id': sender, 'launcher_type': 'person'}
            async def execute():
                return await self.listener._run_with_user_storage(
                    session, hint, self.listener.plugin.service.run_tool,
                    'setup_account', params, session, query_id, hint,
                )

            result = await guarded_login(self.listener.plugin, sender, params, execute)
            if result.get('success') and result.get('verified'):
                message = '账号登录验证成功，已完成绑定。'
            elif result.get('success'):
                message = '账号已保存，尚未验证登录；不代表学校已确认账号可用。'
            elif (result.get('login_attempt') or {}).get('status') == 'unknown':
                message = '本次认证结果未知，请先查询账号状态，勿重复提交凭据。'
            elif result.get('error_code') == 'login_rate_limited':
                message = f"登录限流保护已生效，请等待 {result['retry_after_seconds']} 秒后再试。"
            elif result.get('error_code', '').startswith('login_guard_'):
                message = '登录保护或校验记录不可用，本次结果未确认，请查询账号状态；勿重复发送密码。'
            else:
                message = '账号绑定未完成，请检查凭据或稍后重试。' 
        except asyncio.CancelledError:
            raise
        except Exception:
            message = '本次操作结果未确认，请查询账号状态；勿重复发送密码。'
        finally:
            if self.users.get(sender) is asyncio.current_task():
                self.users.pop(sender, None)
        try:
            await self.listener.plugin.send_message(
                bot, 'person', sender, card_chain(text_card('校园账号绑定', message)[0]),
            )
        except Exception:
            # A lost response may follow a delivered message. Never resend it
            # automatically; the durable attempt remains readable in status.
            pass

    @staticmethod
    async def reply(ctx, message):
        await ctx.reply(card_chain(text_card('校园账号绑定', message)[0]))
