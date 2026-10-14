"""Host bridge for explicit HENU keyboards and verified WebSocket clicks.

Copy as langbot/pkg/platform/sources/henu_qq_buttons.py. No token/config logging.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from types import SimpleNamespace

CONTROL = re.compile(r'henu-btn:v1:(operation|binding):(confirm|cancel):[A-Za-z0-9_-]{24}')


def _identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', value))


async def handle_interaction(adapter, event, ws_event_id):
    if not isinstance(event, dict):
        return False
    data = event.get('data') or {}
    resolved = data.get('resolved') or {}
    control = resolved.get('button_data') or ''
    if not isinstance(control, str) or not control.startswith('henu-btn:'):
        return False  # Preserve the existing Dify form handler.
    interaction_id = event.get('id')
    if not _identifier(interaction_id):
        return True
    seen = getattr(adapter, '_henu_button_seen', None)
    if seen is None:
        seen = adapter._henu_button_seen = OrderedDict()
    now = time.monotonic()
    for identifier, expires in list(seen.items()):
        if expires <= now:
            del seen[identifier]
    if interaction_id in seen:
        return True
    if len(seen) >= 512:
        return True  # Fail closed; never evict a live ACK and replay it later.
    seen[interaction_id] = now + 600  # Mark before ACK/enqueue, never replay unknown outcomes.
    try:
        stamp = datetime.fromisoformat(event.get('timestamp', '').replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        actor = event.get('user_openid')
        valid = (adapter.config.get('enable-henu-buttons') is True and adapter.enable_webhook is False
                 and event.get('type') == 11 and data.get('type') == 11
                 and event.get('chat_type') == 2 and event.get('scene', 'c2c') == 'c2c'
                 and not any(event.get(field) for field in ('group_openid', 'group_member_openid', 'channel_id', 'guild_id'))
                 and _identifier(actor) and _identifier(ws_event_id) and -30 <= age < 300
                 and CONTROL.fullmatch(control) is not None and adapter.ap is not None)
    except (TypeError, ValueError):
        valid = False
    bot = None
    if valid:
        bot = next((candidate for candidate in adapter.ap.platform_mgr.bots if candidate.adapter is adapter), None)
        valid = bot is not None
    await adapter.bot.ack_interaction(interaction_id, code=0 if valid else 4)
    if not valid:
        return True
    from langbot_plugin.api.entities.builtin.platform import message as pm, events as pe, entities as entities
    from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes
    chain = pm.MessageChain([pm.Source(id=ws_event_id, time=stamp), pm.Plain(text=control)])
    # This source is intentionally not C2C_MESSAGE_CREATE, and never gains KIT's
    # trusted quote marker. Keep the exact anchor on this query, not a shared cache.
    source = SimpleNamespace(t='INTERACTION_CREATE', event_id=ws_event_id, user_openid=actor, timestamp=stamp)
    synthetic = pe.FriendMessage(sender=entities.Friend(id=actor, nickname='', remark=''),
                                 message_chain=chain, time=int(stamp.timestamp()), source_platform_object=source)
    await adapter.ap.query_pool.add_query(bot_uuid=bot.bot_entity.uuid,
        launcher_type=LauncherTypes.PERSON, launcher_id=actor, sender_id=actor,
        message_event=synthetic, message_chain=chain, adapter=adapter,
        pipeline_uuid=bot.bot_entity.use_pipeline_uuid,
        variables={'_henu_button_interaction': {'data': control, 'event_id': ws_event_id,
                   'interaction_id': interaction_id}, '_routed_by_rule': True})
    return True


async def try_reply_buttons(adapter, message_source, chain):
    from langbot_plugin.api.entities.builtin.platform import message as pm
    from langbot.pkg.platform.sources.henu_qq_cards import is_card, send_card, validate_keyboard
    component = getattr(pm, 'HenuMarkdown', None)
    source = message_source.source_platform_object
    callback = getattr(source, 't', '') == 'INTERACTION_CREATE'
    native = component is not None and len(chain) == 1 and isinstance(chain[0], component) and chain[0].keyboard
    if not native and not callback:
        return False, None
    if len(chain) != 1 or not isinstance(chain[0], pm.Plain):
        raise ValueError('invalid HENU keyboard reply')
    content = chain[0].markdown if native else chain[0].text
    if not is_card(content):
        raise ValueError('invalid HENU keyboard card')
    event = getattr(source, 't', '')
    if callback:
        scene, target, event_id, msg_id = 'c2c', source.user_openid, source.event_id, None
        stamp = source.timestamp
    elif event in {'C2C_MESSAGE_CREATE', 'GROUP_AT_MESSAGE_CREATE', 'GROUP_MESSAGE_CREATE'}:
        scene = 'c2c' if event == 'C2C_MESSAGE_CREATE' else 'group'
        target = getattr(source, 'user_openid' if scene == 'c2c' else 'group_openid', '')
        msg_id, event_id = source.d_id, None
        stamp = datetime.fromtimestamp(message_source.time, timezone.utc)
    else:
        # Channels keep existing readable transport; never expose inline controls.
        receipt = await adapter.reply_message(message_source, pm.MessageChain([pm.Plain(text=chain[0].text)]))
        return True, receipt
    if not _identifier(target) or not (msg_id or event_id):
        raise ValueError('missing passive HENU keyboard source')
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    if not -30 <= age < 300:
        raise ValueError('expired passive HENU keyboard source')
    keyboard = chain[0].keyboard if native and adapter.config.get('enable-henu-buttons') is True else None
    if keyboard:
        actor = getattr(message_source.sender, 'id', '')
        validate_keyboard(keyboard, scene, actor)
    receipt = await send_card(adapter.bot, scene, target, content, msg_id=msg_id, event_id=event_id, keyboard=keyboard)
    if scene == 'c2c':
        from langbot.libs.qq_official_api.api import normalize_qq_c2c_send_receipt
        normalized = normalize_qq_c2c_send_receipt(receipt)
        return True, {'qq_c2c_receipt': normalized} if normalized is not None else None
    return True, None
