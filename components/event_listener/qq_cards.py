from __future__ import annotations

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import events
from langbot_plugin.api.entities.builtin.platform.message import Plain

from henu_plugin.qq_cards import cache, card_chain, is_page_request, scope, text_card


class QQCardsListener(EventListener):
    """Replace only this request's campus reply and handle read-only paging."""

    async def initialize(self):
        await super().initialize()

        @self.handler(events.NormalMessageResponded)
        async def response(ctx):
            await self.present(ctx)

        @self.handler(events.PersonMessageReceived)
        async def private_page(ctx):
            await self.paging(ctx)

        @self.handler(events.GroupMessageReceived)
        async def group_page(ctx):
            await self.paging(ctx)

    async def present(self, ctx):
        try:
            variables = await ctx.get_query_vars()
            card = variables.get("_henu_card")
            if not isinstance(card, dict):
                return
            owner = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
            if owner is None or card.get("owner") != list(owner):
                return
            ctx.event.reply = card_chain(card["page"]).model_dump(mode="json")
        except Exception:
            # Preserve the pipeline's original text response on API failure.
            return

    async def paging(self, ctx):
        chain = getattr(getattr(ctx.event, "message_chain", None), "root", [])
        text = "".join(item.text for item in chain if isinstance(item, Plain)).strip()
        if not is_page_request(text):
            return False
        ctx.prevent_default()
        ctx.prevent_postorder()
        try:
            key = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
            page = cache(self.plugin).page(key, text) if key else text_card("无法翻页", "无法确认当前会话，请重新查询。")[0]
            await ctx.reply(card_chain(page))
            if key:
                await cache(self.plugin).delivered(key, page)
        except Exception:
            await ctx.reply(card_chain("卡片读取失败，请重新查询。"))
        return True
