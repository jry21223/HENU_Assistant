from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from langbot_plugin.api.definition.components.tool.tool import Tool
from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy

from henu_plugin.food_client import actor_metadata, call_food, food_settings
from henu_plugin.kit_client import KitError, kit_request, kit_settings

ACTIONS = {
    "create": (
        "create_food_post",
        {
            "venue_name",
            "campus",
            "tier",
            "review_text",
            "price_reference",
            "hours_reference",
            "dishes",
            "images",
        },
    ),
    "mine": ("list_my_food_posts", set()),
    "list": ("list_food_posts", {"campus"}),
    "detail": ("get_food_post", {"post_id"}),
    "venues": ("list_food_venues", {"campus"}),
}


class HenuFood(Tool):
    async def call(
        self, params: dict[str, Any], session, query_id: int
    ) -> dict[str, Any]:
        action = params.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            return self._failure("请选择 create/mine/list/detail/venues。")
        name, allowed = ACTIONS[action]
        if set(params) - allowed - {"action"}:
            return self._failure(
                "美食工具只接收投稿内容和查询条件；账号由系统自动读取，无需输入 UUID。"
            )
        arguments = {key: value for key, value in params.items() if key != "action"}
        notice = ""
        try:
            settings = food_settings()
            meta = None
            if action in {"create", "mine"}:
                bot, sender = await self._identity(session, query_id)
                account, notice = await asyncio.to_thread(self._account, bot, sender)
                meta = actor_metadata(settings, name, arguments, bot, sender, account)
            result = await call_food(settings, name, arguments, meta)
        except ValueError:
            return self._failure("无法确认当前用户身份或美食服务配置，请稍后重试。")
        except Exception:
            # Creation may have reached Food before a response was lost. Never
            # invite automatic retries that would create a second public post.
            return self._failure(
                "暂时无法确认美食服务结果，请先查询“我的美食投稿”再决定是否重试。"
            )
        if notice and result["success"]:
            if "私聊发送“绑定 HENU KIT”" not in result["reply_text"]:
                result["reply_text"] += "\n" + notice
            elif notice.startswith("暂时"):
                result["reply_text"] = (
                    "暂时无法读取 Kit 绑定。\n" + result["reply_text"]
                )
            result["msg"] = result["reply_text"]
        result["qq_reply_hint"] = (
            "复述 reply_text；不要索要账号 UUID，不要自动重复投稿。"
        )
        return result

    async def _identity(self, session, query_id: int) -> tuple[str, str]:
        proxy = QueryBasedAPIProxy(
            query_id=query_id, plugin_runtime_handler=self.plugin.plugin_runtime_handler
        )
        variables = await proxy.get_query_vars()
        sources = {
            str(value or "").strip()
            for value in (
                variables.get("henu_sender_id"),
                variables.get("sender_id"),
                getattr(session, "sender_id", ""),
            )
        } - {""}
        if len(sources) != 1:
            raise ValueError("missing or conflicting sender")
        sender = sources.pop()
        bot = str(await proxy.get_bot_uuid() or "").strip()
        if (
            sender in {"", "0", "None", "none", "unknown"}
            or not bot
            or len(sender) > 256
            or len(bot) > 160
        ):
            raise ValueError("missing identity")
        return bot, sender

    @staticmethod
    def _account(bot: str, sender: str) -> tuple[dict | None, str]:
        prompt = "当前使用游客身份；私聊发送“绑定 HENU KIT”可将后续投稿关联到账号，投稿不受影响。"
        try:
            settings = kit_settings()
            if not settings or bot != settings["bot_uuid"]:
                return None, prompt
            result = kit_request(settings, "resolve", {"subject": sender})
            if result.get("bound") is not True:
                return None, prompt
            uid = UUID(str(result.get("user_id", "")))
            name = result.get("display_name") or "Kit 用户"
            if (
                not uid.int
                or not isinstance(name, str)
                or len(name) > 120
                or "\r" in name
                or "\n" in name
            ):
                raise ValueError("invalid account response")
            return {"user_id": str(uid), "display_name": name}, ""
        except KitError as exc:
            if exc.code == "NOT_BOUND":
                return None, prompt
        except (ValueError, TypeError, AttributeError, KeyError):
            pass
        return (
            None,
            "暂时无法读取 Kit 绑定，当前使用游客身份，投稿不受影响；稍后可私聊发送“绑定 HENU KIT”。",
        )

    @staticmethod
    def _failure(message: str) -> dict[str, Any]:
        return {"success": False, "msg": message, "reply_text": message}
