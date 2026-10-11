"""SDK message -> Food tool -> loopback MCP publication boundary."""

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langbot_plugin.api.entities import events
from langbot_plugin.api.entities.builtin.platform import (
    entities as pe,
    events as ev,
    message as pm,
)

from test_food_tool import SECRET, UID, claims, food_http as http_fixture, tool


@pytest.fixture
def food_http(request):
    yield from http_fixture.__wrapped__(request)


CONTENT = {
    "action": "create",
    "venue_name": "大同刀削面",
    "campus": "jinming",
    "tier": "hang",
    "review_text": "真好吃真好吃",
}
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2S0AAAAASUVORK5CYII="


class Chat:
    async def start(self, monkeypatch, settings, sender="alice", bot="bot"):
        from components.event_listener.food_capture import FoodCaptureListener

        self.tool, self.session = tool(monkeypatch, settings, sender, bot)
        self.bot = bot
        self.variables = {}
        self.sequence = 0
        self.replies = []
        self.listener = FoodCaptureListener()
        self.listener.plugin = self.tool.plugin
        await self.listener.initialize()

        async def send_reply(chain):
            if getattr(self, "fail_delivery", False):
                raise OSError("fixture SDK delivery failure")
            self.replies.append(str(chain))

        proxy = SimpleNamespace(
            get_query_vars=AsyncMock(side_effect=lambda: self.variables),
            get_bot_uuid=AsyncMock(return_value=bot),
            reply=AsyncMock(side_effect=send_reply),
        )
        monkeypatch.setattr(
            "components.cli_tools.henu_food.QueryBasedAPIProxy", lambda **kw: proxy
        )
        return self

    async def message(self, text, images=(), source=None, timestamp=None):
        self.sequence += 1
        self.variables = {
            "sender_id": self.session.sender_id,
            "launcher_id": self.session.launcher_id,
            "launcher_type": self.session.launcher_type,
        }
        chain = pm.MessageChain(
            [
                pm.Source(
                    id=source or f"message-{self.sequence}",
                    time=timestamp or datetime.now(timezone.utc),
                ),
                pm.Plain(text=text),
                *[x if isinstance(x, pm.Image) else pm.Image(base64=x) for x in images],
            ]
        )
        event = events.PersonMessageReceived(
            launcher_type="person",
            launcher_id=self.session.launcher_id,
            sender_id=self.session.sender_id,
            message_chain=chain,
            message_event=ev.FriendMessage(
                sender=pe.Friend(
                    id=self.session.sender_id, nickname="fixture", remark=""
                ),
                message_chain=chain,
            ),
        )
        if getattr(self, "wire_roundtrip", False):
            event = events.PersonMessageReceived.model_validate(
                event.model_dump(mode="json")
            )

        async def set_var(key, value):
            self.variables[key] = value

        async def reply(chain, **kw):
            if getattr(self, "fail_delivery", False):
                raise OSError("fixture SDK delivery failure")
            self.replies.append(str(chain))

        ctx = SimpleNamespace(
            event=event,
            query_id=self.sequence,
            get_bot_uuid=AsyncMock(return_value=self.bot),
            get_query_vars=AsyncMock(side_effect=lambda: self.variables),
            set_query_var=set_var,
            reply=reply,
            prevent_default=lambda: None,
            is_prevent_default=False,
        )
        ctx.prevent_default = lambda: setattr(ctx, "is_prevent_default", True)
        for handler in self.listener.registered_handlers[events.PersonMessageReceived]:
            await handler(ctx)
        return ctx

    async def prepare(self, **fields):
        return await self.tool.call(CONTENT | fields, self.session, self.sequence)


def test_four_fields_only_prepare_an_unpublished_preview(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("金明 夯 大同刀削面 真好吃真好吃")
        result = await chat.prepare()
        assert result["success"] and "尚未发布" in result["reply_text"]
        assert not calls, "Four fields must never publish a public post"

    asyncio.run(run())


@pytest.mark.parametrize("food_http", ["publication_json"], indirect=True)
def test_publication_receipt_is_readable_and_repeated_confirmation_returns_it(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿", [PNG])
        await chat.prepare(price_reference="人均12元")
        await chat.message("确认发布")
        receipt = chat.replies[-1]
        assert "request_id" not in receipt and '"post"' not in receipt
        assert "fixture-author" not in receipt and "/api/v1/food" not in receipt
        assert "大同刀削面" in receipt and "金明" in receipt and "夯" in receipt
        assert "人均12元" in receipt and "图片：1 张" in receipt
        assert (
            "https://henukit.cn/food/post/11111111-1111-4111-8111-111111111111"
            in receipt
        )
        assert receipt.count("绑定 HENU KIT") == 1
        await chat.message("确认发布")
        assert chat.replies[-1] == receipt and len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("food_http", ["malformed_publication"], indirect=True)
def test_unparseable_success_receipt_remains_claimed_without_raw_json_or_repost(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿")
        await chat.prepare()
        await chat.message("确认发布")
        receipt = chat.replies[-1]
        assert (
            "核验" in receipt
            and "request_id" not in receipt
            and "{invalid" not in receipt
        )
        await chat.message("确认发布")
        assert chat.replies[-1] == receipt and len(calls) == 1

    asyncio.run(run())


def test_legacy_finished_json_receipt_is_upgraded_without_publishing_again(
    monkeypatch, food_http, tmp_path
):
    # A synthetic pre-upgrade persisted-state fixture; assertions use SDK replies.
    import hashlib
    import hmac
    import json
    import sqlite3
    import time

    settings, calls = food_http
    db_path = tmp_path / "drafts.sqlite3"
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(db_path))
    scope = hmac.new(
        SECRET.encode(),
        b"henu-food-draft-v1\n" + b'["bot","alice","person","alice"]',
        hashlib.sha256,
    ).hexdigest()
    old_receipt = (
        "投稿已发布（创建即公开）: "
        + json.dumps(
            {
                "post": {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "images": ["/api/v1/food/posts/fixture/images/0"],
                },
                "request_id": "old-private-request",
            }
        )
        + "\n当前使用游客身份，私聊发送“绑定 HENU KIT”可关联后续投稿。"
    )
    state = {
        "draft": {
            "status": "finished",
            "args": {k: v for k, v in CONTENT.items() if k != "action"},
            "images": [],
            "receipt": old_receipt,
            "expires": time.time() + 600,
        }
    }
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE drafts (scope TEXT PRIMARY KEY, updated REAL, state TEXT)"
        )
        db.execute(
            "INSERT INTO drafts VALUES (?, ?, ?)",
            (scope, time.time(), json.dumps(state)),
        )

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("确认发布", source="new-confirm")
        receipt = chat.replies[-1]
        assert "request_id" not in receipt and "图片：1 张" in receipt
        assert receipt.count("绑定 HENU KIT") == 1
        assert "https://henukit.cn/food/post/" in receipt
        reloaded = await Chat().start(monkeypatch, settings)
        await reloaded.message("确认发布", source="reloaded-confirm")
        assert reloaded.replies[-1] == receipt and not calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "question", ["你能收到图片吗？", "图片收到了吗", "你这边能看到照片吗?"]
)
def test_picture_receipt_question_then_repeated_confirmation_publishes_once(
    monkeypatch, food_http, tmp_path, question
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("投稿美食 金明 夯 大同刀削面炒菜凉菜 便宜好吃", [PNG])
        await chat.prepare(
            venue_name="大同刀削面炒菜凉菜", price_reference="人均12左右"
        )
        await chat.message(question)
        assert "已收到 1 张图片" in chat.replies[-1]
        await chat.message("确认发布")
        assert len(calls) == 1, (
            "A picture receipt question must not invalidate the draft"
        )
        assert calls[0][0]["arguments"]["price_reference"] == "人均12左右"
        assert calls[0][0]["arguments"]["images"] == [
            {"content_type": "image/png", "data": PNG}
        ]
        await chat.message("确认发布")
        assert len(calls) == 1

    asyncio.run(run())


def test_receipt_question_does_not_clear_pending_price_changes(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿", [PNG])
        await chat.prepare(price_reference="人均12元")
        await chat.message("图片收到了吗？人均改成15元")
        await chat.message("你能收到图片吗？")
        assert "待整理" in chat.replies[-1]
        await chat.message("确认发布")
        await chat.message("确认发布")
        assert not calls
        await chat.message("人均改为15元")
        await chat.tool.call(
            {"action": "create", "price_reference": "人均15元"},
            chat.session,
            chat.sequence,
        )
        await chat.message("确认发布")
        assert (
            len(calls) == 1
            and calls[0][0]["arguments"]["price_reference"] == "人均15元"
        )

    asyncio.run(run())


def test_invalid_or_unavailable_images_block_publication(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿", [pm.Image(url="http://127.0.0.1:1/private")])
        result = await chat.prepare()
        assert "暂不能发布" in result["reply_text"]
        await chat.message("确认发布")
        assert not calls
        await chat.message("取消美食草稿")
        await chat.message("我要投稿", [PNG] * 7)
        result = await chat.prepare()
        assert "超过 6 张" in result["reply_text"]
        await chat.message("确认发布")
        assert not calls

    asyncio.run(run())


def test_late_or_replayed_content_cannot_change_or_publish_a_draft(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿", source="original")
        await chat.prepare()
        await chat.message("补充价格")
        late = await chat.tool.call(
            CONTENT | {"price_reference": "旧调用价格"}, chat.session, 1
        )
        assert not late["success"]
        await chat.message("确认发布", source="original")
        await chat.message(
            "确认发布", timestamp=datetime.now(timezone.utc) - timedelta(minutes=11)
        )
        assert not calls

    asyncio.run(run())


def test_expired_picture_buffer_is_not_attached_to_a_later_draft(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)
    now = datetime.now(timezone.utc)
    clock = [now.timestamp()]
    monkeypatch.setattr(
        "henu_plugin.food_drafts.time", SimpleNamespace(time=lambda: clock[0])
    )

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("", [PNG], timestamp=now)
        clock[0] += 601
        await chat.message("我要投稿", timestamp=now + timedelta(seconds=601))
        result = await chat.prepare()
        assert "图片：0 张" in result["reply_text"]
        clock[0] += 601
        await chat.message("确认发布", timestamp=now + timedelta(seconds=1202))
        assert not calls and "有效草稿" in chat.replies[-1]

    asyncio.run(run())


def test_binding_is_resolved_at_publication_and_guest_failure_is_nonblocking(
    monkeypatch, food_http, tmp_path
):
    from henu_plugin.kit_client import KitError

    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr(
        "components.cli_tools.henu_food.kit_settings", lambda: {"bot_uuid": "bot"}
    )
    resolutions = []

    def resolve(*args):
        resolutions.append(args)
        if len(resolutions) == 1:
            return {"bound": True, "user_id": UID, "display_name": "同学"}
        raise KitError()

    monkeypatch.setattr("components.cli_tools.henu_food.kit_request", resolve)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿")
        await chat.prepare(venue_name="店<&>\u2028")
        assert not resolutions
        await chat.message("确认发布")
        assert claims(calls[0])["user_id"] == UID
        # Go encoding/json canonical fixture, independent known-good expectation.
        import hashlib

        canonical = '{"campus":"jinming","review_text":"真好吃真好吃","tier":"hang","venue_name":"店\\u003c\\u0026\\u003e\\u2028"}'
        assert (
            claims(calls[0])["arguments_sha256"]
            == hashlib.sha256(canonical.encode()).hexdigest()
        )
        await chat.message("投稿另一家店")
        await chat.prepare(venue_name="另一家店")
        await chat.message("确认发布")
        assert claims(calls[1])["kind"] == "guest"
        assert "绑定 HENU KIT" in chat.replies[-1]

    asyncio.run(run())


def test_draft_survives_reload_and_other_chat_cannot_confirm(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        original = await Chat().start(monkeypatch, settings)
        await original.message("我要投稿", [PNG], source="alice-draft")
        await original.prepare()
        for sender, bot, launcher in [
            ("bob", "bot", "bob"),
            ("alice", "other-bot", "alice"),
            ("alice", "bot", "other-conversation"),
        ]:
            other = await Chat().start(monkeypatch, settings, sender, bot)
            other.session.launcher_id = launcher
            await other.message("确认发布", source="other-confirm")
        assert not calls
        reloaded = await Chat().start(monkeypatch, settings)
        await reloaded.message("确认发布", source="alice-confirm")
        assert len(calls) == 1 and calls[0][0]["arguments"]["images"][0]["data"] == PNG
        await reloaded.message("确认发布", source="alice-confirm")
        assert len(calls) == 1

    asyncio.run(run())


def test_model_cannot_publish_or_supply_image_bytes(monkeypatch, food_http, tmp_path):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("确认发布")
        result = await chat.prepare()
        assert not result["success"] and not calls
        await chat.message("我要投稿")
        for parameter in (
            {"confirmed": True},
            {"images": [{"content_type": "image/png", "data": PNG}]},
        ):
            assert not (await chat.prepare(**parameter))["success"]
        assert not calls

    asyncio.run(run())


def test_unrelated_campus_messages_are_not_intercepted_by_food_replay_checks(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        now = datetime.now(timezone.utc)
        await chat.message("今天有什么课", source="schedule", timestamp=now)
        repeated = await chat.message("今天有什么课", source="schedule", timestamp=now)
        older = await chat.message("借书查询", timestamp=now - timedelta(seconds=10))
        assert not repeated.is_prevent_default and not older.is_prevent_default
        assert not chat.replies and not calls

    asyncio.run(run())


def test_confirmation_created_before_latest_preview_cannot_publish(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿")
        early = datetime.now(timezone.utc)
        await chat.prepare()
        await chat.message("确认发布", timestamp=early)
        assert not calls
        await chat.message("确认发布")
        assert len(calls) == 1

    asyncio.run(run())


def test_failed_picture_preview_delivery_blocks_confirmation(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿")
        await chat.prepare()
        chat.fail_delivery = True
        await chat.message("", [PNG])
        chat.fail_delivery = False
        await chat.message("确认发布")
        assert not calls and "尚未送达" in chat.replies[-1]
        await chat.message("重新整理最新投稿")
        assert (await chat.prepare())["success"]
        await chat.message("确认发布")
        assert len(calls) == 1 and len(calls[0][0]["arguments"]["images"]) == 1

    asyncio.run(run())


def test_serialized_sdk_event_preserves_picture_and_later_confirmation(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)
    clock = [datetime.now(timezone.utc).timestamp()]
    monkeypatch.setattr(
        "henu_plugin.food_drafts.time", SimpleNamespace(time=lambda: clock[0])
    )

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        chat.wire_roundtrip = True
        await chat.message(
            "我要投稿",
            ["data:image/png;base64," + PNG],
            timestamp=datetime.fromtimestamp(clock[0], timezone.utc),
        )
        assert (await chat.prepare())["success"]
        clock[0] += 2  # SDK wire Source timestamps have second precision.
        await chat.message(
            "确认发布", timestamp=datetime.fromtimestamp(clock[0], timezone.utc)
        )
        assert len(calls) == 1 and calls[0][0]["arguments"]["images"][0]["data"] == PNG

    asyncio.run(run())


def test_two_simultaneous_confirmations_publish_only_once(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿")
        await chat.prepare()
        await asyncio.gather(
            chat.message("确认发布", source="confirm-a"),
            chat.message("确认发布", source="confirm-b"),
        )
        assert len(calls) == 1
        assert any("投稿已发布" in reply for reply in chat.replies)

    asyncio.run(run())


@pytest.mark.parametrize("food_http", ["lost_reply"], indirect=True)
def test_lost_publication_reply_never_causes_automatic_repost(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿")
        await chat.prepare()
        await chat.message("确认发布")
        assert len(calls) == 1 and "不会自动重复" in chat.replies[-1]
        await chat.message("确认发布")
        assert len(calls) == 1

    asyncio.run(run())


def test_later_confirmation_publishes_price_and_real_chat_picture_once(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message(
            "金明 夯 大同刀削面 真好吃真好吃", ["data:image/png;base64," + PNG]
        )
        preview = await chat.prepare(price_reference="小碗8元，大碗10元，人均13元以内")
        assert (
            "尚未发布" in preview["reply_text"]
            and "图片：1 张" in preview["reply_text"]
        )
        assert not calls
        await chat.message("确认发布")
        assert len(calls) == 1
        arguments = calls[0][0]["arguments"]
        assert arguments["price_reference"] == "小碗8元，大碗10元，人均13元以内"
        assert arguments["images"] == [{"content_type": "image/png", "data": PNG}]
        await chat.message("确认发布")
        assert len(calls) == 1
        assert "绑定 HENU KIT" in chat.replies[-1]

    asyncio.run(run())


def test_content_and_successive_picture_updates_replace_preview(
    monkeypatch, food_http, tmp_path
):
    settings, calls = food_http
    monkeypatch.setenv("HENU_FOOD_DRAFT_DB", str(tmp_path / "drafts.sqlite3"))
    monkeypatch.setattr("components.cli_tools.henu_food.kit_settings", lambda: None)

    async def run():
        chat = await Chat().start(monkeypatch, settings)
        await chat.message("我要投稿 金明 夯 大同刀削面 真好吃真好吃")
        await chat.prepare()
        await chat.message("小碗8元，大碗10元")
        update = await chat.tool.call(
            {"action": "create", "price_reference": "小碗8元，大碗10元"},
            chat.session,
            chat.sequence,
        )
        assert "真好吃真好吃" in update["reply_text"]
        await chat.message("", [PNG])
        await chat.message("这个是图片", [PNG])
        assert "图片：2 张" in chat.replies[-1] and not calls
        await chat.message("人均13元以内")  # not yet processed into a preview
        await chat.message("确认发布")
        assert not calls and "未整理" in chat.replies[-1]
        await chat.message("人均13元以内")
        await chat.prepare(price_reference="小碗8元，大碗10元，人均13元以内")
        await chat.message("确认发布")
        assert len(calls) == 1 and len(calls[0][0]["arguments"]["images"]) == 2

    asyncio.run(run())
