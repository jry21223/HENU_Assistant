"""Resolve a real user's plain confirmation; tokens remain server-side."""
from __future__ import annotations

import asyncio
import json
import re

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes

from henu_mcp.core.secure_storage import decrypt_value
from henu_plugin.confirmation import conversation_context, validate_pending_operation


CONFIRM_WORDS = {'确认', '确认操作', '确认绑定', '确认雨课堂绑定'}


class ConfirmationShortcut:
    def __init__(self, listener):
        self.listener = listener
        self.seen = set()
        self.tasks = {}

    async def handle(self, ctx) -> bool:
        text = str(getattr(ctx.event, 'text_message', '') or getattr(ctx.event, 'message_chain', '')).strip().rstrip('。！!')
        if text not in CONFIRM_WORDS:
            return False
        ctx.prevent_default()
        ctx.prevent_postorder()
        if ctx.query_id in self.seen:
            return True
        self.seen.add(ctx.query_id)
        if len(self.seen) > 1024:
            self.seen = set(sorted(self.seen)[-512:])
        event = ctx.event
        sender = str(getattr(event, 'sender_id', '') or '').strip()
        conversation = conversation_context(event)
        if sender in {'', '0', 'None', 'none'} or conversation['launcher_type'] not in {'person', 'group'} or not conversation['launcher_id']:
            await self.reply(ctx, '无法确认当前身份，本次未执行。')
            return True
        is_group = conversation['launcher_type'] == 'group'
        if is_group and text in {'确认绑定', '确认雨课堂绑定'}:
            await self.reply(ctx, '账号凭据只能回到原私聊确认。')
            return True
        storage_key = re.sub(r'[^0-9A-Za-z._-]+', '_', sender).strip('._-')
        plugin = self.listener.plugin
        choices = []
        records = [('operation', f'user:{storage_key}:pending_operation', storage_key)]
        if not is_group:
            records.append(('binding', f'user:{sender}:yuketang_pending_credentials', sender))
        try:
            getter = getattr(plugin, 'get_plugin_storage_keys', None)
            known = set(await getter()) if callable(getter) else None
            for kind, key, identity in records:
                if known is not None and key not in known:
                    continue
                try:
                    raw = await plugin.get_plugin_storage(key)
                except KeyError:
                    continue
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                pending = json.loads(decrypt_value(raw) if kind == 'binding' else raw)
                if not isinstance(pending, dict) or pending.get('conversation') != conversation:
                    continue
                check = validate_pending_operation(pending, token=str(pending.get('token') or ''),
                    storage_key=identity, canonical_command=str(pending.get('command') or ''), query_id=ctx.query_id)
                if check.ok:
                    choices.append((kind, str(pending['token'])))
        except Exception:
            await self.reply(ctx, '确认记录暂时无法读取，本次未执行；请稍后重试。')
            return True
        if text == '确认操作':
            choices = [item for item in choices if item[0] == 'operation']
        elif text in {'确认绑定', '确认雨课堂绑定'}:
            choices = [item for item in choices if item[0] == 'binding']
        if not choices:
            await self.reply(ctx, '当前聊天没有有效的待确认操作，可能已过期或已执行，请重新发起。旧版令牌确认仍可使用。')
            return True
        if len(choices) > 1:
            await self.reply(ctx, '同时有雨课堂凭据绑定和其他操作待确认。请回复“确认绑定”或“确认操作”，本次尚未执行。')
            return True
        kind, token = choices[0]
        if kind == 'binding':
            await self.listener._yuketang_login.handle(ctx, is_group=False, command='yuketang confirm ' + token)
            return True
        current = self.tasks.get(sender)
        if current is not None and not current.done():
            await self.reply(ctx, '上一项确认正在执行，请等待结果，不要重复确认。')
            return True
        start = asyncio.Event()
        task = asyncio.create_task(self.execute(ctx, sender, conversation, token, start))
        self.tasks[sender] = task
        try:
            await self.reply(ctx, '已收到确认，正在执行，请等待结果。')
        except Exception:
            task.cancel()
            return True
        start.set()
        return True

    async def execute(self, ctx, sender, conversation, token, start):
        try:
            await start.wait()
            # Reuse token, fingerprint, expiry and durable consumption checks.
            from components.cli_tools.henu_cli_safe import HenuCliSafe
            tool = HenuCliSafe()
            tool.plugin = self.listener.plugin
            session = Session(sender_id=sender, launcher_id=conversation['launcher_id'],
                              launcher_type=LauncherTypes(conversation['launcher_type']))
            result = await tool.call({'command':'confirm ' + token}, session, ctx.query_id)
            await self.reply(ctx, str(result.get('reply_text') or result.get('msg') or '操作未返回明确结果，请先查询状态。'))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f'[henu.confirmation] {type(exc).__name__}', flush=True)
            await self.reply(ctx, '本次操作结果未确认，请先查询状态，不要重复提交。')
        finally:
            if self.tasks.get(sender) is asyncio.current_task():
                self.tasks.pop(sender, None)

    @staticmethod
    async def reply(ctx, text):
        await ctx.reply(MessageChain([Plain(text=text)]))
