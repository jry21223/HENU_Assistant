"""Fresh, fail-closed membership checks using the signed QQ subject boundary.

Callers supply only the trusted runtime QQ sender, never a user-reported KIT UUID.
No local boolean, binding cache, or past successful check authorizes an action.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re

from henu_plugin.kit_client import KitError, kit_request, kit_settings

ASYNC_DEADLINE_SECONDS = 12.0

BENEFITS = frozenset({'yuketang', 'ucampus', 'chaoxing', 'library_schedule'})


class MembershipError(RuntimeError):
    def __init__(self, code='MEMBERSHIP_UNAVAILABLE'):
        self.code = code
        super().__init__(code)

    def public_result(self):
        messages = {
            'NOT_BOUND': '请先在本人私聊发送“绑定 HENU KIT”并完成网页绑定，再使用会员权益。',
            'LIFETIME_REQUIRED': '此功能包含在 HENU KIT 终身会员中，请先开通会员；服务费用已包含，无需另付费或扣积分。',
            'EXECUTOR_UNAVAILABLE': '雨课堂执行端尚未完成会员资格约束升级，本次未启用或启动新执行；已有状态、停用和退出仍可操作。',
        }
        text = messages.get(self.code, '会员资格暂时无法核实，本次未提交新业务操作；请稍后重试。已有结果查询、停用、删除账号和取消仍可使用。')
        return {'success':False, 'error_code':self.code, 'msg':text, 'reply_text':text}


def require_benefit(subject: str, benefit: str) -> dict:
    """Synchronous HTTP/executor gate; each call performs a fresh owner query."""
    benefit = 'ucampus' if benefit == 'ucampus_ai' else benefit
    if (benefit not in BENEFITS or not isinstance(subject, str)
            or subject in {'0', 'None', 'none'}
            or not re.fullmatch(r'[0-9A-Za-z._-]{1,128}', subject)):
        raise MembershipError('NOT_BOUND')
    try:
        settings = kit_settings()
        if settings is None:
            raise MembershipError()
        data = kit_request(settings, 'benefits', {'subject': subject})
        lifetime = data.get('lifetime')
        expected = 'lifetime' if lifetime is True else 'free'
        benefits = data.get('benefits')
        checked_at = data.get('checked_at')
        if (data.get('bound') is not True or type(lifetime) is not bool
                or type(data.get('allowed')) is not bool or data['allowed'] != lifetime
                or data.get('plan') != expected or not isinstance(benefits, list)
                or any(not isinstance(value, str) for value in benefits)
                or len(benefits) != len(set(benefits))
                or set(benefits) != (BENEFITS if lifetime else set())
                or not isinstance(checked_at, str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z', checked_at)):
            raise MembershipError()
        checked = datetime.fromisoformat(checked_at.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        if not 0 <= age <= 30:
            raise MembershipError()
        if not lifetime:
            raise MembershipError('LIFETIME_REQUIRED')
        return data
    except KitError as exc:
        raise MembershipError('NOT_BOUND' if exc.code == 'NOT_BOUND' else 'MEMBERSHIP_UNAVAILABLE') from exc
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise MembershipError() from exc


async def require_benefit_async(subject: str, benefit: str) -> dict:
    """Async message/worker gate; network time is bounded by the KIT client."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(require_benefit, subject, benefit), ASYNC_DEADLINE_SECONDS)
    except TimeoutError as exc:
        raise MembershipError() from exc


def yuketang_needs_benefit(tool: str | None, params: dict) -> bool:
    return bool(tool and tool.startswith('yuketang_')
                and tool not in {'yuketang_status', 'yuketang_config_show', 'yuketang_logout'}
                and not (tool == 'yuketang_set_enabled' and params.get('enabled') in ('off', False)))


async def require_yuketang_entry(subject: str) -> None:
    """Qualify at the async public entry, before any global runtime context."""
    await require_benefit_async(subject, 'yuketang')
    require_yuketang_execution()


def require_yuketang_execution() -> None:
    """Pure local refusal: v1 has no verified per-action membership capability.

    No HTTP may happen here: the service invokes this within its global runtime
    context. Neither daemon_wired nor an unverified assertion safely enables v1.
    """
    raise MembershipError('EXECUTOR_UNAVAILABLE')
