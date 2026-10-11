from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import events

from components.cli_tools.henu_food import HenuFood


class FoodCaptureListener(EventListener):
    """Capture real Food attachments and user confirmation before the model."""

    async def initialize(self):
        await super().initialize()
        food = HenuFood()
        food.plugin = self.plugin

        @self.handler(events.PersonMessageReceived)
        async def private_message(ctx):
            await food.receive_message(ctx)

        @self.handler(events.GroupMessageReceived)
        async def group_message(ctx):
            await food.receive_message(ctx)
