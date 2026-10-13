from __future__ import annotations

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import events
from henu_plugin.qq_cards import cache, card_chain, page_request_text, scope, set_card_reply, text_card


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
            set_card_reply(ctx.event, card["page"])
        except Exception:
            # Preserve the pipeline's original text response on API failure.
            return

    async def paging(self, ctx):
        chain = getattr(getattr(ctx.event, "message_chain", None), "root", [])
        text = page_request_text(chain)
        if not text:
            return False
        ctx.prevent_default()
        ctx.prevent_postorder()
        try:
            key = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
            page = cache(self.plugin).page(key, text) if key else text_card("无法翻页", "无法确认当前会话，请重新查询。")[0]
        except Exception:
            key = None
            page = "卡片读取失败，请重新查询。"
        try:
            await ctx.reply(card_chain(page))
        except Exception:
            return True  # The first send may already have arrived; never repeat.
        if key:
            try:
                await cache(self.plugin).delivered(key, page)
            except Exception:
                pass  # No complete-preview acknowledgment, so confirmation stays blocked.
        return True
