"""QQ choices reference owned pending records, never commands from the model."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from types import SimpleNamespace

from langbot_plugin.api.entities.builtin.platform import message as pm

CONTROL = re.compile(r'henu-btn:v1:(operation|binding|kit|food):(confirm|cancel):([A-Za-z0-9_-]{24})')


def enabled(plugin) -> bool:
    getter = getattr(plugin, 'get_config', None)
    return callable(getter) and getter().get('qq_cards_buttons') is True and hasattr(pm, 'HenuMarkdown')


def _key(owner, kind):
    return 'qq-button:' + hashlib.sha256(json.dumps([list(owner), kind]).encode()).hexdigest()


async def _load(plugin, key, *, encrypted=False):
    try:
        raw = await plugin.get_plugin_storage(key)
    except KeyError:
        return {}
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    if encrypted:
        from henu_mcp.core.secure_storage import decrypt_value
        raw = decrypt_value(raw)
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


async def _save(plugin, key, value):
    await plugin.set_plugin_storage(key, json.dumps(value, ensure_ascii=False).encode())


def _lock(plugin):
    if not hasattr(plugin, '_henu_button_lock'):
        plugin._henu_button_lock = asyncio.Lock()
    return plugin._henu_button_lock


async def register_pending(plugin, owner, kind, storage_key, pending, *, mode='callback'):
    if not enabled(plugin) or not owner or owner[1] != 'person':
        return None
    expires = pending.get('expires_at', pending.get('expires', 0))
    if not isinstance(expires, (float, int)) or not math.isfinite(expires) or expires <= time.time():
        return None
    if pending.get('consumed') or pending.get('terminal'):
        return None
    if kind != 'food' and pending.get('conversation') != {'launcher_type': owner[1], 'launcher_id': owner[2]}:
        return None
    if kind != 'food' and pending.get('bot') != owner[0]:
        return None
    token = str(pending.get('token') or pending.get('nonce') or '')
    if not token:
        return None
    key = _key(owner, kind)
    async with _lock(plugin):
        old = await _load(plugin, key)
        revision = str(pending.get('preview_source') or '')
        if old.get('pending_token') == token and old.get('revision') == revision and old.get('expires', 0) > time.time():
            return old
        action = {'schema': 'henu.qq-button.v1', 'id': secrets.token_urlsafe(18),
                  'owner': list(owner), 'kind': kind, 'mode': mode,
                  'storage_key': storage_key, 'pending_token': token,
                  'revision': revision, 'created_query': pending.get('created_query_id', -1),
                  'expires': min(expires, time.time() + 600), 'decision': None}
        await _save(plugin, key, action)
        return action


def pending_keyboard(action, sender):
    if not action or action.get('decision'):
        return None
    buttons = []
    for choice, label, style in [('confirm', '确认', 1), ('cancel', '取消', 0)]:
        control = f"henu-btn:v1:{action['kind']}:{choice}:{action['id']}"
        command = action['mode'] == 'command'
        behavior = {'type': 2 if command else 1, 'data': control,
                    'permission': {'type': 0, 'specify_user_ids': [sender]},
                    'unsupport_tips': '请使用卡片内的文字提示继续。'}
        if command:
            behavior.update(enter=True, reply=True)
        button = {'id': choice + '-' + action['id'],
                  'render_data': {'label': label, 'visited_label': '已选择' + label, 'style': style},
                  'action': behavior}
        if not command:
            button['group_id'] = action['id']
        buttons.append(button)
    return {'content': {'rows': [{'buttons': buttons}]}}


def command_keyboard(owner, commands):
    buttons = []
    for index, (label, command) in enumerate(commands[:5]):
        buttons.append({'id': 'next-' + str(index), 'render_data': {'label': label[:10], 'style': 0},
                        'action': {'type': 2, 'data': command,
                                   'permission': {'type': 0, 'specify_user_ids': [owner[3]]},
                                   'enter': owner[1] == 'person', 'reply': False,
                                   'unsupport_tips': '请直接发送：' + command}})
    return {'content': {'rows': [{'buttons': buttons}]}} if buttons else None


def coordinators(plugin, *, listener=None):
    if not hasattr(plugin, '_henu_button_coordinators'):
        from henu_plugin.kit_binding import KitBindingCoordinator
        from henu_plugin.yuketang_login import YuketangLoginCoordinator
        if listener is None:
            from components.event_listener.identity_capture import IdentityCaptureListener
            listener = IdentityCaptureListener()
            listener.plugin = plugin
        plugin._henu_button_coordinators = SimpleNamespace(kit=KitBindingCoordinator(listener), yuketang=YuketangLoginCoordinator(listener))
    elif listener is not None:
        plugin._henu_button_coordinators.kit.listener = listener
        plugin._henu_button_coordinators.yuketang.listener = listener
    return plugin._henu_button_coordinators


async def direct_keyboard(plugin, ctx, title, text, *, pending=None):
    if not enabled(plugin):
        return None
    from henu_plugin.qq_cards import scope
    owner = scope(str(await ctx.get_bot_uuid() or ''), ctx.event)
    if not owner or owner[1] != 'person':
        return None
    if title == '雨课堂登录' and '5 分钟内有效' in text:
        key = f'user:{owner[3]}:yuketang_pending_credentials'
        pending = await _load(plugin, key, encrypted=True)
        action = await register_pending(plugin, owner, 'binding', key, pending)
        return pending_keyboard(action, owner[3])
    if title == 'HENU KIT 绑定':
        kit = coordinators(plugin).kit
        key = kit._scope_key(owner[0], owner[3])
        pending = pending or await kit._read(key)
        if pending and (pending.get('previewed') or pending.get('command') == 'unlink' or '即将绑定 HENU KIT：' in text):
            action = await register_pending(plugin, owner, 'kit', key, pending, mode='command')
            return pending_keyboard(action, owner[3])
    return None


async def handle_button(plugin, ctx):
    chain = getattr(getattr(ctx.event, 'message_chain', None), 'root', [])
    text = ''.join(item.text for item in chain if isinstance(item, pm.Plain)).strip()
    match = CONTROL.fullmatch(text)
    if not match:
        return False
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    from henu_plugin.qq_cards import reply_card, scope
    reply_started = False
    async def reply(message):
        nonlocal reply_started
        reply_started = True
        return await reply_card(ctx, '按钮操作', message, plugin=plugin)
    try:
        if not enabled(plugin) or any(not isinstance(item, (pm.Source, pm.Plain, pm.At, pm.Quote)) for item in chain):
            await reply('按钮当前不可用，请按原卡片文字提示继续；本次未执行。')
            return True
        owner = scope(str(await ctx.get_bot_uuid() or ''), ctx.event)
        if not owner or owner[1] != 'person':
            await reply('此操作只能由本人回到原私聊处理，本次未执行。')
            return True
        kind, choice, identifier = match.groups()
        async with _lock(plugin):
            key = _key(owner, kind)
            action = await _load(plugin, key)
            if action.get('id') != identifier or action.get('owner') != list(owner) or action.get('schema') != 'henu.qq-button.v1':
                await reply('按钮已失效或不属于当前会话，本次未执行；请重新发起。')
                return True
            if action.get('decision'):
                await reply('该选择已处理，请查询当前状态，不会重复提交。')
                return True
            if action.get('expires', 0) <= time.time() or action.get('created_query') == ctx.query_id:
                await reply('按钮已过期或当前轮次不能确认，本次未执行；请重新发起。')
                return True
            if action['mode'] == 'callback':
                variables = await ctx.get_query_vars()
                if (variables.get('_henu_button_interaction') or {}).get('data') != text:
                    await reply('请点击原卡片按钮；本次未收到有效的 QQ 按钮事件，未执行。')
                    return True
            if kind == 'food':
                from henu_plugin.food_drafts import FoodDrafts
                pending = await asyncio.to_thread(FoodDrafts().button_snapshot, action['storage_key'])
                if (pending.get('nonce') != action['pending_token'] or pending.get('preview_source') != action['revision']):
                    await reply('原美食草稿已变化或已处理，本次未执行；请核对最新预览。')
                    return True
                if choice == 'confirm' and (not pending.get('preview_delivered') or pending.get('pending_source') is not None or pending.get('image_error')):
                    await reply('请先阅读完整的最新草稿预览，并处理图片错误；本次尚未发布。')
                    return True
            else:
                pending = await _load(plugin, action['storage_key'], encrypted=kind in {'kit', 'binding'})
                if (pending.get('token') != action['pending_token'] or pending.get('consumed') or pending.get('terminal')
                        or pending.get('schema') != 'henu.pending-operation.v1'
                        or pending.get('bot') != owner[0]
                        or pending.get('expires_at', 0) <= time.time()
                        or pending.get('conversation') != {'launcher_type': owner[1], 'launcher_id': owner[2]}):
                    await reply('原待确认操作已变化或已处理，本次未执行；请重新核对预览。')
                    return True
            action['decision'] = choice
            await _save(plugin, key, action)  # Write ahead: no retry after uncertain outcomes.
        if choice == 'cancel' and kind == 'operation':
            from components.cli_tools.henu_cli_safe import HenuCliSafe
            tool = HenuCliSafe(); tool.plugin = plugin
            if not await tool.cancel_pending(action['storage_key'], action['pending_token']):
                await reply('原操作已变化或已处理，本次未取消新请求，请查询当前状态。')
                return True
        elif choice == 'cancel' and kind == 'binding':
            if not await coordinators(plugin).yuketang.cancel_pending(owner[3], action['pending_token']):
                await reply('原绑定请求已变化或正在处理，本次未取消新请求；请查询当前状态。')
                return True
        elif choice == 'cancel' and kind == 'kit':
            if not await coordinators(plugin).kit.cancel_pending(action['storage_key'], action['pending_token']):
                await reply('原 KIT 请求已变化或已处理，本次未取消新请求。')
                return True
        if choice == 'cancel' and kind != 'food':
            await reply('待确认操作已取消，尚未提交。已完成的操作不受影响。')
        elif kind == 'operation':
            from components.cli_tools.henu_cli_safe import HenuCliSafe
            from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
            tool = HenuCliSafe(); tool.plugin = plugin
            result = await tool.call({'command': 'confirm ' + action['pending_token']},
                Session(sender_id=owner[3], launcher_id=owner[2], launcher_type=LauncherTypes(owner[1])), ctx.query_id)
            await reply(str(result.get('reply_text') or result.get('msg') or '操作结果未明确，请先查询状态，不要重复提交。'))
        elif kind == 'binding':
            reply_started = True
            await coordinators(plugin).yuketang.handle(ctx, is_group=False, command='yuketang confirm ' + action['pending_token'])
        elif kind == 'kit':
            # Keep the actual Source and QQ quote marker, not a synthetic callback.
            original = ctx.event.message_chain
            original_text = getattr(ctx.event, 'text_message', None)
            confirmation = '确认'
            if original_text is not None:
                ctx.event.text_message = confirmation
            ctx.event.message_chain = pm.MessageChain([item for item in original if not isinstance(item, pm.Plain)] + [pm.Plain(text=confirmation)])
            try:
                reply_started = True
                await coordinators(plugin).kit.handle(ctx, is_group=False)
            finally:
                ctx.event.message_chain = original
                if original_text is not None:
                    ctx.event.text_message = original_text
        else:
            from components.cli_tools.henu_food import HenuFood
            food = HenuFood(); food.plugin = plugin
            reply_started = True
            await food.receive_message(ctx, button_action=action)
    except Exception:
        if not reply_started:
            await reply('按钮操作结果未确认，请先查询当前状态；不会自动重复提交。')
    return True
