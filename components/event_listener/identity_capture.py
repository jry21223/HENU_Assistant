from __future__ import annotations

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import context, events


class IdentityCaptureListener(EventListener):
    async def initialize(self):
        await super().initialize()

        @self.handler(events.PersonMessageReceived)
        async def on_person_message(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.GroupMessageReceived)
        async def on_group_message(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.PersonNormalMessageReceived)
        async def on_person_normal_message(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.GroupNormalMessageReceived)
        async def on_group_normal_message(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.PersonCommandSent)
        async def on_person_command(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.GroupCommandSent)
        async def on_group_command(ctx: context.EventContext):
            await self._persist_identity(ctx)

    async def _persist_identity(self, ctx: context.EventContext) -> None:
        event = ctx.event
        await ctx.set_query_var("henu_sender_id", str(getattr(event, "sender_id", "") or ""))
        await ctx.set_query_var("henu_launcher_id", str(getattr(event, "launcher_id", "") or ""))
        await ctx.set_query_var("henu_launcher_type", str(getattr(event, "launcher_type", "") or ""))
