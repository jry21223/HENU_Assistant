"""Explicit private renewal of the caller's saved school account."""
from __future__ import annotations

import asyncio
import json
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path

from henu_mcp.core.secure_storage import load_encrypted_profile, save_encrypted_profile
from henu_mcp.tools import server_impl as api

_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()


def verify_saved_login() -> dict:
    path = Path(api.PROFILE_FILE)
    profile = load_encrypted_profile(path)
    sid, password = str(profile.get('student_id') or ''), str(profile.get('password') or '')
    if not sid or not password:
        return {'success': False, 'error_code': 'auth_required', 'msg': '尚未保存校园账号，请在私聊发送“绑定账号”。'}
    attempt = profile.get('last_login_attempt') or api.load_json(path.with_name('login_attempt.json'))
    guard = profile.get('saved_login_guard') or {}
    uncertain_codes = {'auth_state_unknown', 'binding_verification_unavailable', 'binding_persist_failed'}
    verification = profile.get('login_verification') or {}
    if (any(record.get('status') in {'unknown', 'pending'} or record.get('error_code') in uncertain_codes
            for record in (attempt, verification)) or guard.get('status') in {'pending', 'unknown'}):
        return {'success': False, 'error_code': 'auth_state_unknown',
                'msg': '上次认证结果尚未确认，本次未提交密码，请先人工核对学校登录状态。'}
    now = time.time()
    failures = int(guard.get('failures', 0)) if float(guard.get('expires_at', 0)) > now else 0
    if failures >= 3 or float(guard.get('next_at', 0)) > now:
        return {'success': False, 'error_code': 'login_rate_limited',
                'msg': '账号验证请求过于频繁，本次未执行登录，请稍后再试。'}
    guard = {'status': 'pending', 'failures': failures + 1,
             'expires_at': guard.get('expires_at') if failures else now + 86400,
             'next_at': now + 60}
    profile['saved_login_guard'] = guard
    # The message handler also reserves native Storage before entering this transaction.
    save_encrypted_profile(path, profile)
    try:
        result = api.save_account(sid, password, verify_login=True)
        auth = result.get('auth') or {}
        code = str(auth.get('error_code') or result.get('error_code') or '')
        verified = bool(result.get('success') and result.get('verified'))
        status = 'verified' if verified else 'unknown' if code in uncertain_codes else 'failed'
    except Exception:
        verified, status, code = False, 'unknown', 'auth_state_unknown'
    profile = load_encrypted_profile(path)
    guard['status'] = status
    if verified:
        guard['failures'] = failures
    profile['saved_login_guard'] = guard
    profile['last_login_attempt'] = {
        'status': status, 'verified': verified, 'error_code': code,
        'attempted_at': datetime.now(timezone.utc).isoformat(), 'subject_student_id': sid,
    }
    save_encrypted_profile(path, profile)
    if verified:
        return {'success': True, 'verified': True, 'msg': '校园账号验证成功，可以继续查询。'}
    message = {
        'captcha_required': '学校要求验证码，本次未完成验证，请先在学校认证页面完成验证。',
        'account_locked': '学校提示账号已锁定，请先通过学校渠道解锁。',
        'invalid_credentials': '学校提示账号或密码有误，请在私聊重新绑定。',
    }.get(code, '本次登录未能确认，请先核对学校登录状态，勿重复提交密码。')
    return {'success': False, 'error_code': code, 'msg': message}


async def handle_saved_login(plugin, ctx, listener) -> bool:
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    from henu_plugin.qq_cards import card_chain, text_card
    if getattr(ctx, 'is_prevent_postorder', False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    event = ctx.event
    kind = str(getattr(getattr(event, 'launcher_type', ''), 'value', getattr(event, 'launcher_type', '')))
    sender = str(getattr(event, 'sender_id', '') or '').strip()
    if kind != 'person' or sender in {'', '0', 'unknown', 'None'}:
        await ctx.reply(card_chain(text_card('校园账号验证', '请本人私聊发送“验证登录”；群内未执行登录。')[0]))
        return True
    if not callable(getattr(listener, '_run_with_user_storage', None)):
        await ctx.reply(card_chain(text_card('校园账号验证', '验证服务暂不可用，本次未执行登录。')[0]))
        return True
    await ctx.reply(card_chain(text_card('校园账号验证', '正在验证已保存的校园账号，请等待结果，勿重复发送。')[0]))
    session = Session(launcher_type=LauncherTypes.PERSON, launcher_id=sender, sender_id=sender)
    try:
        lock = _locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            key = f'user:{sender}:saved_login_inflight'
            previous = json.loads(await plugin.get_plugin_storage(key) or b'{}')
            if previous.get('status') in {'pending', 'unknown'}:
                message = '上次认证结果尚未确认，本次未提交密码，请先人工核对学校登录状态。'
            else:
                await plugin.set_plugin_storage(key, b'{"status":"pending"}')
                result = await listener._run_with_user_storage(
                    session, {}, plugin.service.run_tool, 'verify_saved_login', {}, session, ctx.query_id, {},
                )
                known_failure = result.get('error_code') in {
                    'auth_required', 'invalid_credentials', 'captcha_required', 'account_locked',
                    'login_rate_limited', 'local_login_cooldown', 'cas_schema_incompatible',
                    'rate_limited', 'access_denied', 'additional_verification_required',
                }
                status = 'finished' if result.get('success') and result.get('verified') or known_failure else 'unknown'
                await plugin.set_plugin_storage(key, json.dumps({'status': status}).encode())
                message = result.get('msg') or '本次验证未能确认，请先核对学校登录状态。'
    except Exception:
        message = '本次验证结果未能确认，请先核对学校登录状态，勿重复发送。'
    await ctx.reply(card_chain(text_card('校园账号验证', message)[0]))
    return True
