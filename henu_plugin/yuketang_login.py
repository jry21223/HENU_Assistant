"""Private credential intake using the account flow's ACK/background/reply pattern."""
from __future__ import annotations

import asyncio
import json
import shlex
import time

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes

from henu_mcp.core.secure_storage import encrypt_value, decrypt_value
from henu_plugin import bridge_client
from henu_plugin.cli import inspect_cli_command
from henu_plugin.confirmation import create_pending_operation, validate_pending_operation


SENSITIVE_TOOLS = {'yuketang_account_set', 'yuketang_set_token', 'yuketang_login'}


class YuketangLoginCoordinator:
    def __init__(self, listener):
        self.listener = listener
        self.tasks = {}
        self.users = {}
        self.last_login = {}

    async def handle(self, ctx, *, is_group: bool) -> bool:
        text = str(getattr(ctx.event, 'text_message', '') or getattr(ctx.event, 'message_chain', '')).strip()
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        if not parts or parts[0].lower() not in {'yuketang', '雨课堂'}:
            return False
        spec = inspect_cli_command(text)
        confirming = len(parts) >= 2 and parts[1].lower() == 'confirm'
        sensitive = spec.resolved_tool in SENSITIVE_TOOLS or any(
            flag in text.lower() for flag in ('--password', '--x-access-token', '--x_access_token')
        )
        if not sensitive and not confirming:
            return False
        ctx.prevent_default()
        ctx.prevent_postorder()
        query_id = ctx.query_id
        if query_id in self.tasks:
            return True
        sender = str(getattr(ctx.event, 'sender_id', '') or '').strip()
        if is_group or not sender:
            await self.reply(ctx, '雨课堂凭据及登录仅允许本人私聊操作，本条消息未交给模型。')
            return True
        existing = self.users.get(sender)
        if existing is not None and not existing.done():
            await self.reply(ctx, '雨课堂操作正在处理中，请等待结果，不要重复发送密码。')
            return True
        if (spec.error and not confirming) or (confirming and len(parts) != 3):
            await self.reply(ctx, '敏感命令格式无效。请使用 yuketang account set --account 手机号 --password \'密码\'；不要填写尖括号。')
            return True
        start = asyncio.Event()
        task = asyncio.create_task(self.run(ctx, text, sender, start))
        self.tasks[query_id] = task
        self.users[sender] = task
        try:
            await self.reply(ctx, '已安全接收雨课堂操作，正在处理；凭据不会发送给模型，请等待最终结果。')
        except Exception:
            task.cancel()
            return True
        start.set()
        return True

    async def run(self, ctx, text, sender, start):
        try:
            await start.wait()
            message = await self.process(ctx, text, sender)
            await self.reply(ctx, message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await self.reply(ctx, '雨课堂操作未完成，请查询 yuketang status；不要重复提交密码。')
            except Exception:
                pass
            print(f'[henu.yuketang] operation failed: {type(exc).__name__}', flush=True)
        finally:
            if self.users.get(sender) is asyncio.current_task():
                self.users.pop(sender, None)
            # Keep bounded query deduplication without retaining credentials.
            if len(self.tasks) > 512:
                self.tasks = {key: value for key, value in self.tasks.items() if not value.done()}

    async def process(self, ctx, text, sender):
        plugin = self.listener.plugin
        key = f'user:{sender}:yuketang_pending_credentials'
        parts = shlex.split(text)
        confirming = len(parts) == 3 and parts[1].lower() == 'confirm'
        if confirming:
            try:
                raw = await plugin.get_plugin_storage(key)
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                pending = json.loads(decrypt_value(str(raw)))
                command = pending['command']
                check = validate_pending_operation(pending, token=parts[2], storage_key=sender,
                    canonical_command=command, query_id=ctx.query_id)
            except Exception:
                return '没有有效的雨课堂待确认操作，或确认已过期，请重新发起。'
            if not check.ok:
                return check.message
            # Consume before mutation: a retry cannot repeat credential changes.
            await plugin.set_plugin_storage(key, b'')
            spec = inspect_cli_command(command)
        else:
            spec = inspect_cli_command(text)
            if spec.resolved_tool in {'yuketang_account_set', 'yuketang_set_token'}:
                pending = create_pending_operation(storage_key=sender, canonical_command=text,
                                                   query_id=ctx.query_id)
                await plugin.set_plugin_storage(key, encrypt_value(json.dumps(pending)).encode())
                return ('将保存并向雨课堂桥服务传递你的凭据；账号密码绑定确认后将验证登录。'
                        f'请在下一条私聊回复：yuketang confirm {pending["token"]}')
        if spec.resolved_tool not in SENSITIVE_TOOLS:
            return '待确认内容不属于允许的雨课堂敏感操作。'
        if spec.resolved_tool != 'yuketang_login':
            result = await self.call_service(ctx, spec.resolved_tool, spec.params)
            if not result.get('success') or spec.resolved_tool == 'yuketang_set_token':
                return str(result.get('reply_text') or result.get('msg') or '操作未完成')
        elapsed = time.monotonic() - self.last_login.get(sender, -1000)
        if elapsed < 30:
            return '凭据已保存；登录请求过于频繁，请稍后执行 yuketang login。'
        self.last_login[sender] = time.monotonic()
        started = await self.call_service(ctx, 'yuketang_login', {})
        session_id = started.get('login_session_id')
        if not started.get('success') or not session_id:
            return str(started.get('reply_text') or started.get('msg') or '无法启动雨课堂登录')
        # No Storage transaction is held during the remote login wait.
        deadline = time.monotonic() + 160
        while time.monotonic() < deadline:
            await asyncio.sleep(4)
            try:
                result = await asyncio.to_thread(bridge_client.login_result, session_id)
            except bridge_client.BridgeError:
                return '雨课堂登录结果查询中断，当前结果未知；请稍后查询 yuketang status。'
            if result.get('status') == 'success':
                return '雨课堂登录成功。学校 IDS 绑定不受影响；可用 yuketang status 查询当前状态。'
            if result.get('status') in {'failed', 'expired'}:
                return '雨课堂登录失败或会话过期，请核对雨课堂账号密码；不会自动重复尝试。'
        return '雨课堂仍未返回最终登录结果，请稍后查询 yuketang status；不要重复提交。'

    async def call_service(self, ctx, name, params):
        event = ctx.event
        sender = str(event.sender_id)
        launcher = str(event.launcher_id)
        session = Session(launcher_type=LauncherTypes('person'), launcher_id=launcher, sender_id=sender)
        hint = {'sender_id': sender, 'launcher_id': launcher, 'launcher_type': 'person'}
        return await self.listener._run_with_user_storage(session, hint,
            self.listener.plugin.service.run_tool, name, params, session, ctx.query_id, hint)

    @staticmethod
    async def reply(ctx, text):
        await ctx.reply(MessageChain([Plain(text=text)]))
