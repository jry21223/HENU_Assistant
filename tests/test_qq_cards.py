import asyncio
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langbot_plugin.api.entities.builtin.platform.message import MessageChain

from henu_plugin.qq_cards import CardCache, MAX_BYTES, PREFIX, TOOL_TITLES, card_chain, result_card, text_card


def test_every_actual_service_route_has_a_card_title():
    tree = ast.parse(Path("henu_plugin/service.py").read_text())
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Attribute) and n.target.attr == "_tool_dispatch")
    routes = {key.value for key in assignment.value.keys}
    assert routes == set(TOOL_TITLES)


def test_schedule_pages_keep_time_location_and_week_warning():
    source = {"success": True, "week_filter_applied": False, "schedule": {"星期二": [{"course": f"课程{i}", "clock_start": "08:00", "clock_end": "09:40", "classroom": "十号楼101"} for i in range(35)]}}
    before = json.dumps(source)
    pages = result_card(source, "schedule_query").pages()
    assert len(pages) > 1
    assert "课程34" in "\n".join(pages)
    assert "08:00" in pages[0] and "十号楼101" in pages[0]
    assert "未按教学周过滤" in "\n".join(pages)
    assert all(len(page.encode()) <= MAX_BYTES for page in pages)
    assert json.dumps(source) == before


def test_untrusted_fields_cannot_create_image_link_or_mention():
    pages = result_card({"success": True, "rooms": [{"name": "![secret](https://evil.test/a) <@all>\n# Inject", "password": "SENSITIVE"}]}, "seminar_query").pages()
    wire = MessageChain.model_validate(card_chain(pages[0]).model_dump(mode="json")).model_dump(mode="json")
    assert wire[0]["type"] == "Plain"
    assert "![secret]" not in wire[0]["text"] and "<@all>" not in wire[0]["text"]
    assert "SENSITIVE" not in str(wire)


def test_empty_failed_unbound_confirmation_and_partial_commit_are_distinct():
    cases = [({"success": True, "seats": []}, "没有符合"), ({"success": False, "msg": "接口错误", "seats": []}, "未完成"), ({"success": False, "error_code": "auth_required"}, "需要绑定"), ({"success": False, "confirmation_required": True}, "尚未提交"), ({"success": False, "external_committed": True, "storage_persisted": False}, "不要重复提交")]
    for source, expected in cases:
        assert expected in result_card(source).pages()[0]


def test_paging_is_scoped_and_expires(monkeypatch):
    cache = CardCache()
    now = [1.0]
    monkeypatch.setattr("henu_plugin.qq_cards.time.monotonic", lambda: now[0])
    owner = ("bot1", "person", "alice", "alice")
    cache.put(owner, ["first", "second"])
    assert cache.page(("bot2", "person", "alice", "alice"), "下一页").startswith(PREFIX)
    assert "过期" in cache.page(("bot1", "person", "bob", "bob"), "下一页")
    assert cache.page(owner, "下一页") == "second"
    assert "共 2 页" in cache.page(owner, "下一页")
    now[0] = 602.0
    assert "过期" in cache.page(owner, "上一页")


def test_group_personal_cli_never_executes_or_returns_raw_data(monkeypatch):
    from components.cli_tools.henu_cli import HenuCli
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    monkeypatch.setattr(HenuCli, "call", AsyncMock(return_value={"success": True, "msg": "private-id-123"}))
    plugin = SimpleNamespace(plugin_runtime_handler=Mock())
    tool = HenuCliSafe.__new__(HenuCliSafe)
    tool.plugin = plugin
    session = SimpleNamespace(launcher_type="group", launcher_id="group-a", sender_id="alice")
    result = asyncio.run(tool.call({"command": "schedule now"}, session, 1))
    assert "私聊" in result["reply_text"]
    assert "private-id-123" not in str(result)
    HenuCli.call.assert_not_awaited()


def test_reply_event_uses_only_the_current_owner_and_preserves_fallback():
    from components.event_listener.qq_cards import QQCardsListener
    listener = QQCardsListener.__new__(QQCardsListener)
    listener.plugin = SimpleNamespace()
    event = SimpleNamespace(launcher_type="person", launcher_id="alice", sender_id="alice", reply=None)
    ctx = SimpleNamespace(event=event, get_bot_uuid=AsyncMock(return_value="bot"), get_query_vars=AsyncMock(return_value={"_henu_card": {"owner": ["bot", "person", "alice", "alice"], "page": PREFIX + "课表"}}))
    asyncio.run(listener.present(ctx))
    assert event.reply == [{"type": "Plain", "text": PREFIX + "课表"}]
    event.reply = None
    ctx.get_query_vars.return_value["_henu_card"]["owner"][3] = "bob"
    asyncio.run(listener.present(ctx))
    assert event.reply is None
    ctx.get_query_vars.side_effect = RuntimeError("query API unavailable")
    asyncio.run(listener.present(ctx))
    assert event.reply is None


def test_full_tool_card_is_captured_before_model_compaction(monkeypatch):
    from henu_plugin.qq_cards import stage_card
    variables = {}
    async def set_var(key, value): variables[key] = value
    proxy = SimpleNamespace(get_bot_uuid=AsyncMock(return_value="bot"), set_query_var=set_var)
    monkeypatch.setattr("langbot_plugin.api.proxies.query_based_api.QueryBasedAPIProxy", lambda **kwargs: proxy)
    plugin = SimpleNamespace(plugin_runtime_handler=Mock())
    session = SimpleNamespace(launcher_type="person", launcher_id="alice", sender_id="alice")
    result = {"success": True, "rooms": [{"name": f"研讨室{i}"} for i in range(30)]}
    asyncio.run(stage_card(plugin, session, 1, result))
    result["rooms"] = result["rooms"][:8]
    from henu_plugin.qq_cards import cache, scope
    assert "研讨室29" in cache(plugin).page(scope("bot", session), "卡片第 4 页")


def test_long_preview_receipt_requires_every_page_successfully_sent():
    async def run():
        cache = CardCache()
        owner = ("bot", "person", "alice", "alice")
        acknowledged = AsyncMock()
        cache.put(owner, ["first", "second", "third"], on_read=acknowledged)
        await cache.delivered(owner, "first")
        await cache.delivered(owner, cache.page(owner, "卡片第 3 页"))
        acknowledged.assert_not_awaited()
        await cache.delivered(owner, cache.page(owner, "卡片第 2 页"))
        acknowledged.assert_awaited_once()
        await cache.delivered(owner, "second")
        acknowledged.assert_awaited_once()
    asyncio.run(run())


def test_binding_link_fragment_survives_native_and_plain_rendering():
    url = "https://henukit.cn/bind/qq#short_token-ABC"
    page = text_card("HENU KIT 绑定", "请打开链接\n" + url)[0]
    assert "[打开 HENU KIT 授权页面](" + url + ")" in page


def test_food_json_uses_public_summary_without_raw_actor_metadata():
    page = result_card({"success": True, "reply_text": '我的投稿: {"posts":[{"shop":{"name":"面馆"},"campus":"金明","author":"secret-actor"}]}'}, "food").pages()[0]
    assert "面馆" in page and "金明" in page
    assert "secret-actor" not in page and '"posts"' not in page
