"""Select existing result cards; never invoke a campus operation."""
from __future__ import annotations

from langbot_plugin.api.definition.components.tool.tool import Tool
from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy

from henu_plugin.presentation import presentations, publication_lock
from henu_plugin.qq_cards import scope


class HenuPresent(Tool):
    async def call(self, params, session, query_id):
        try:
            proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=self.plugin.plugin_runtime_handler)
            owner = scope(str(await proxy.get_bot_uuid() or ''), session)
            if owner is None:
                return {'success': False, 'error_code': 'identity_missing'}
            async with publication_lock(self.plugin, owner, query_id):
                # Every replacement attempt supersedes an earlier arrangement,
                # including one with invalid references. Exact receipts are separate.
                await proxy.set_query_var('_henu_presentation', None)
                variables = await proxy.get_query_vars()
                card = variables.get('_henu_card') if isinstance(variables, dict) else None
                if isinstance(card, dict) and card.get('authoritative') and card.get('owner') == list(owner):
                    return {'success': False, 'error_code': 'authoritative_receipt', 'llm_hint': '精确确认或提交回执由系统展示，不得改写。'}
                rendered = presentations(self.plugin).render(owner, query_id, params)
                await proxy.set_query_var('_henu_presentation', {'owner': list(owner), 'query_id': query_id, 'arrangement': params, **rendered})
                return {'success': True, 'reply_ready': True, 'llm_hint': '本轮业务卡片及说明已准备，由系统展示；不要重复抄写卡片或输出内部引用。'}
        except ValueError as exc:
            hints = {
                'request_superseded': '本轮选座已被后续查询替代，不要重放旧引用；说明以最新查询结果为准，不猜测新条件。',
                'too_many_pages': '组合结果超过20页，请缩小查询范围，不能声称完整展示。',
                'too_many_messages': '卡片与座位图过多，请分开查询，不省略用户需要的结果。',
                'invalid_campus': 'campus仅用于library locations整表；seats已限定实际区域，不传campus。',
            }
            return {'success': False, 'error_code': str(exc), 'llm_hint': hints.get(str(exc), '只引用当前轮display_ref；请按error_code核对选择，必要时重新查询。')}
        except Exception:
            return {'success': False, 'error_code': 'presentation_unavailable', 'llm_hint': '展示暂时不可用，请用简短自然语言说明结果，不输出内部JSON或引用。'}
