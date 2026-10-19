"""Persistent login-attempt budgets at the trusted private-message boundary."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import time
import weakref
from typing import Any, Awaitable, Callable

GUARD_KEY = 'shared:account_login_guard'
ATTEMPT_KEY = 'user:{}:login_attempt'
MAX_FAILURES = 3
WINDOW_SECONDS = 24 * 3600
MAX_ENTRIES = 4096
_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _failure(code: str, message: str, **extra) -> dict[str, Any]:
    return {'success': False, 'verified': False, 'error_code': code, 'msg': message, **extra}


async def guarded_login(plugin, sender: str, params: dict, execute: Callable[[], Awaitable[dict]]) -> dict:
    loop = asyncio.get_running_loop()
    lock = _locks.setdefault(loop, asyncio.Lock())
    async with lock:
        try:
            return await _guarded_login(plugin, sender, params, execute)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Any uncertain Storage operation blocks automatic retries. Never
            # surface SDK exception strings, which can include request payloads.
            return _failure('login_guard_unavailable', '登录保护或校验记录不可用，本次结果未确认，请查询账号状态后再操作。')


async def _guarded_login(plugin, sender, params, execute):
    sender = re.sub(r'[^0-9A-Za-z._-]+', '_', sender).strip('._-')
    if not sender or sender == 'unknown':
        return _failure('identity_missing', '无法确认绑定用户，本次未执行登录。')
    keys = []
    guards = {}
    now = time.time()
    verify = params.get('verify_login', True)
    if verify:
        master = os.environ.get('HENU_MASTER_KEY', '')
        if not master:
            return _failure('login_guard_unavailable', '登录保护尚未配置，本次未执行登录。')
        for scope, value in [('user', sender), ('student', str(params.get('student_id', '')).strip())]:
            digest = hmac.new(master.encode(), f'{scope}:{value}'.encode(), hashlib.sha256).hexdigest()
            keys.append(digest)
        raw = await plugin.get_plugin_storage(GUARD_KEY)
        stored = json.loads(raw or b'{}')
        if not isinstance(stored, dict):
            raise ValueError('invalid login budget')
        for key, entry in stored.items():
            if not isinstance(entry, dict) or type(entry.get('failures')) is not int:
                raise ValueError('invalid login budget entry')
            expiry = entry.get('expires_at')
            if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or not 0 <= entry['failures'] <= MAX_FAILURES:
                raise ValueError('invalid login budget expiry')
            if expiry > now:
                guards[key] = entry
        retry = max((entry['expires_at'] - now for key in keys
                     if (entry := guards.get(key)) and entry['failures'] >= MAX_FAILURES), default=0)
        if retry > 0:
            return _failure('login_rate_limited', '登录限流保护已生效，请等待后再试。', retry_after_seconds=max(1, int(retry + 0.999)))
        if len(guards.keys() | set(keys)) > MAX_ENTRIES:
            return _failure('login_guard_full', '登录保护容量已满，本次未执行登录，请稍后重试。')
        previous = {key: dict(guards[key]) if key in guards else None for key in keys}
        for key in keys:
            entry = guards.get(key, {'failures': 0, 'expires_at': now + WINDOW_SECONDS})
            guards[key] = {**entry, 'failures': entry['failures'] + 1}
        # Reserve and persist before sending credentials. A crash/lost response
        # consumes an uncertain attempt instead of silently granting a retry.
        await plugin.set_plugin_storage(GUARD_KEY, json.dumps(guards).encode())

    attempt = {'status': 'pending' if verify else 'unverified', 'verified': False, 'attempted_at': now}
    await plugin.set_plugin_storage(ATTEMPT_KEY.format(sender), json.dumps(attempt).encode())
    result = await execute()
    verified = bool(result.get('success') and result.get('verified'))
    auth = result.get('auth') or {}
    code = str(auth.get('error_code') or result.get('error_code') or '')
    if verify:
        # Only a known failure before credential submission gets its budget
        # back. Post-submission transport failures use auth_state_unknown.
        refund = not result.get('success') and code == 'network_error'
        if verified or refund:
            for key in keys:
                if verified or previous[key] is None:
                    guards.pop(key, None)
                else:
                    guards[key] = previous[key]
            await plugin.set_plugin_storage(GUARD_KEY, json.dumps(guards).encode())
    status = 'verified' if verified else ('failed' if verify else 'unverified')
    if verify and code in {'auth_state_unknown', 'binding_verification_unavailable', 'binding_persist_failed'}:
        status = 'unknown'
    attempt.update(status=status, verified=verified, error_code=code)
    await plugin.set_plugin_storage(ATTEMPT_KEY.format(sender), json.dumps(attempt).encode())
    result['login_attempt'] = attempt
    return result
