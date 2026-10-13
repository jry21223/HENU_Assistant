from __future__ import annotations

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import events
from henu_plugin.qq_cards import handle_card_page, present_card


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
        await present_card(ctx)

    async def paging(self, ctx):
        return await handle_card_page(self.plugin, ctx)
