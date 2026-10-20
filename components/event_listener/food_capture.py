from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import events

from components.cli_tools.henu_food import HenuFood
from henu_plugin.qq_cards import handle_card_page
from henu_plugin.qq_navigation import handle_navigation


class FoodCaptureListener(EventListener):
    """Capture real Food attachments and user confirmation before the model."""

    async def initialize(self):
        await super().initialize()
        food = HenuFood()
        food.plugin = self.plugin

        @self.handler(events.PersonMessageReceived)
        async def private_message(ctx):
            if await handle_navigation(self.plugin, ctx, defer_binding=True):
                return
            if await handle_card_page(self.plugin, ctx):
                return
            await food.receive_message(ctx)

        @self.handler(events.GroupMessageReceived)
        async def group_message(ctx):
            if await handle_navigation(self.plugin, ctx, defer_binding=True):
                return
            if await handle_card_page(self.plugin, ctx):
                return
            await food.receive_message(ctx)
