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


def test_actual_sdk_reply_event_serializes_the_supported_reply_field():
    from langbot_plugin.api.entities import events
    from components.event_listener.qq_cards import QQCardsListener
    event = events.NormalMessageResponded.model_construct(
        launcher_type="person", launcher_id="alice", sender_id="alice",
        response_text="original", finish_reason="stop", funcs_called=[], prefix="",
    )
    ctx = SimpleNamespace(event=event, prevent_default=Mock(),
        get_bot_uuid=AsyncMock(return_value="bot"),
        get_query_vars=AsyncMock(return_value={"_henu_card": {
            "owner": ["bot", "person", "alice", "alice"], "page": PREFIX + "课表"}}))
    listener = QQCardsListener.__new__(QQCardsListener)
    listener.plugin = SimpleNamespace()
    asyncio.run(listener.present(ctx))
    field = "reply_message_chain" if "reply_message_chain" in type(event).model_fields else "reply"
    serialized = event.model_dump(mode="json", exclude_unset=True)
    assert serialized[field] == [{"type": "Plain", "text": PREFIX + "课表"}]
    ctx.prevent_default.assert_not_called()


def test_card_does_not_replace_intermediate_tool_call_response():
    from components.event_listener.qq_cards import QQCardsListener
    event = SimpleNamespace(reply=None, funcs_called=["henu_cli"], finish_reason="stop")
    ctx = SimpleNamespace(event=event, get_query_vars=AsyncMock())
    listener = QQCardsListener.__new__(QQCardsListener)
    listener.plugin = SimpleNamespace()
    asyncio.run(listener.present(ctx))
    assert event.reply is None
    ctx.get_query_vars.assert_not_awaited()


def test_terminal_card_is_consumed_once_per_query():
    from components.event_listener.qq_cards import QQCardsListener
    variables = {"_henu_card": {"owner": ["bot", "person", "alice", "alice"], "page": PREFIX + "结果"}}
    async def set_var(key, value):
        variables[key] = value
    event = SimpleNamespace(launcher_type="person", launcher_id="alice", sender_id="alice", reply=None)
    ctx = SimpleNamespace(event=event, get_query_vars=AsyncMock(return_value=variables),
        get_bot_uuid=AsyncMock(return_value="bot"), set_query_var=set_var)
    listener = QQCardsListener.__new__(QQCardsListener)
    listener.plugin = SimpleNamespace()
    asyncio.run(listener.present(ctx))
    assert event.reply[0]["text"] == PREFIX + "结果"
    event.reply = None
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


def test_same_request_multiple_tools_are_preserved_and_next_request_replaces():
    cards = CardCache()
    owner = ("bot", "person", "alice", "alice")
    cards.stage(owner, 1, "schedule day", [PREFIX + "课表\n课程A"])
    cards.stage(owner, 1, "library seats", [PREFIX + "座位\n座位B"])
    assert "课程A" in cards.page(owner, "卡片第 1 页")
    assert "座位B" in cards.page(owner, "下一页")
    cards.stage(owner, 2, "library current", [PREFIX + "预约\n记录C"])
    assert "记录C" in cards.page(owner, "卡片第 1 页")
    assert "共 1 页" in cards.page(owner, "下一页")


def test_paging_with_attachment_is_new_input_and_send_timeout_is_not_retried():
    from langbot_plugin.api.entities.builtin.platform.message import Image, Plain
    from components.event_listener.qq_cards import QQCardsListener
    listener = QQCardsListener.__new__(QQCardsListener)
    listener.plugin = SimpleNamespace()
    event = SimpleNamespace(launcher_type="person", launcher_id="alice", sender_id="alice", message_chain=SimpleNamespace(root=[Plain(text="下一页"), Image(base64="fixture-image")]))
    ctx = SimpleNamespace(event=event, get_bot_uuid=AsyncMock(return_value="bot"), prevent_default=Mock(), prevent_postorder=Mock(), reply=AsyncMock(side_effect=TimeoutError("unknown delivery")))
    assert asyncio.run(listener.paging(ctx)) is False
    ctx.reply.assert_not_awaited()
    event.message_chain.root.pop()
    assert asyncio.run(listener.paging(ctx)) is True
    assert ctx.reply.await_count == 1


def test_real_help_keeps_all_commands_before_compacting(monkeypatch):
    from components.cli_tools.henu_cli import HenuCli
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.cli import build_help_payload
    from henu_plugin.qq_cards import cache, scope, stage_card
    proxy = SimpleNamespace(get_bot_uuid=AsyncMock(return_value="bot"), set_query_var=AsyncMock())
    monkeypatch.setattr("langbot_plugin.api.proxies.query_based_api.QueryBasedAPIProxy", lambda **kwargs: proxy)
    plugin = SimpleNamespace(plugin_runtime_handler=Mock())
    session = SimpleNamespace(launcher_type="person", launcher_id="alice", sender_id="alice")
    async def base_call(self, params, session, query_id):
        result = {"success": True, "cli": {"mode": "help", "command": "help yunfz", "topic": "yunfz"}, **build_help_payload("yunfz")}
        await stage_card(plugin, session, query_id, result)
        self._prepare_delivery_result(result)
        self._normalize_for_qq_delivery(result)
        result["_henu_card_captured"] = True
        return result
    monkeypatch.setattr(HenuCli, "call", base_call)
    tool = HenuCliSafe.__new__(HenuCliSafe)
    tool.plugin = plugin
    delivered = asyncio.run(tool.call({"command": "help yunfz"}, session, 1))
    assert "_henu_card_captured" not in delivered
    all_pages = "\n".join(cache(plugin).entries[scope("bot", session)][1])
    assert "yunfz collection statistics" in all_pages


def test_actual_course_planner_schema_keeps_courses_meetings_and_ids():
    from henu_mcp.core.course_planner import generate_ranked_plans
    result = generate_ranked_plans([[{"id": "math-a", "name": "高等数学", "meetings": [{"weekday": 2, "periods": [1, 2], "weeks": [4, 5], "room": "十号楼101"}]}]], [], {}, 1)
    all_pages = "\n".join(result_card(result, "course_selection_plan").pages())
    for expected in ("高等数学", "math-a", "星期2", "第1、2节", "第4、5周", "十号楼101"):
        assert expected in all_pages


def test_actual_empty_classroom_nested_matrix_occupancy_and_freshness():
    from campus_core.empty_classroom.models import Freshness
    freshness = Freshness(strategy="cache_only", source="xk_room_schedule", fetched_at="2026-10-12T10:00:00+08:00", parsed_at="2026-10-12T10:01:00+08:00", ttl_seconds=300, age_seconds=86400, stale=True, live_checked=False).to_dict()
    old = result_card({"success": True, "rooms": [{"roomName": "十号楼101"}], "freshness": freshness}, "empty_classroom_query").pages()[0]
    assert "缓存已过期" in old and "2026-10-12" in old and "未请求上游" in old
    matrix = {"success": True, "data": {"week": 8, "dayOfWeek": 2, "periods": [{"period": 2, "periodName": "第二大节", "total": 1, "rooms": [{"roomName": "十号楼102", "roomId": "room-102", "capacity": 60}]}]}}
    page = result_card(matrix, "empty_classroom_query").pages()[0]
    assert "第二大节" in page and "十号楼102" in page and "room-102" not in page and "教学周" in page
    occupancy = {"success": True, "data": {"roomName": "十号楼103", "week": 8, "period": 3, "status": "occupied", "courses": [{"courseName": "大学英语", "sectionText": "5-6节", "weekExpr": "第1-18周"}]}}
    page = result_card(occupancy, "empty_classroom_query").pages()[0]
    assert "大学英语" in page and "5-6节" in page and "十号楼103" in page


def test_food_detail_keeps_existing_public_description_and_dishes():
    detail = {"post": {"shop": {"name": "面馆"}, "campus": "jinming", "tier": "hang", "review_text": "面筋道汤清爽", "price_reference": "12元", "hours_reference": "10:00–21:00", "dishes": [{"name": "刀削面", "price": "12元", "reason": "汤清爽"}], "author": "hidden-author", "images": ["https://private-image.test"]}}
    page = "\n".join(result_card({"success": True, "reply_text": "详情：" + json.dumps(detail, ensure_ascii=False)}, "food").pages())
    for expected in ("面筋道汤清爽", "12元", "10:00–21:00", "刀削面", "汤清爽"):
        assert expected in page
    assert "hidden-author" not in page and "private-image" not in page


def test_food_actions_and_detail_ids_share_pages_without_actor_metadata(monkeypatch):
    from components.cli_tools.henu_food import HenuFood
    from henu_plugin.qq_cards import cache, scope
    proxy = SimpleNamespace(get_bot_uuid=AsyncMock(return_value="bot"), set_query_var=AsyncMock())
    monkeypatch.setattr("langbot_plugin.api.proxies.query_based_api.QueryBasedAPIProxy", lambda **kwargs: proxy)
    async def call_food(self, params, session, query_id):
        label = params["action"] + " " + params.get("post_id", "列表A")
        return {"success": True, "reply_text": label}
    monkeypatch.setattr(HenuFood, "_call", call_food)
    tool = HenuFood.__new__(HenuFood)
    tool.plugin = SimpleNamespace(plugin_runtime_handler=Mock())
    session = SimpleNamespace(launcher_type="person", launcher_id="alice", sender_id="alice")
    async def run():
        await tool.call({"action": "list"}, session, 1)
        await tool.call({"action": "detail", "post_id": "详情B"}, session, 1)
        await tool.call({"action": "detail", "post_id": "详情C"}, session, 1)
        cards = cache(tool.plugin)
        key = scope("bot", session)
        combined = "\n".join(cards.entries[key][1])
        assert "列表A" in combined and "详情B" in combined and "详情C" in combined
        assert len(cards.entries[key][1]) == 3
        await tool.call({"action": "detail", "post_id": "详情D"}, session, 2)
        assert len(cards.entries[key][1]) == 1 and "详情D" in cards.page(key, "卡片第 1 页")
    asyncio.run(run())


def test_unverified_day_schedule_is_reference_with_readable_fields():
    result={'success':True,'date':'2026-10-19','weekday':'星期一','week_filter_applied':False,'msg':'2026-10-19 星期一 共4门课（未按教学周过滤）','schedule':[{'course':'计算机网络','period':'第3-4节','time':'1-18周 一[3-4] 金明综合楼6202(134)','location':'金明综合楼6202(134)'}],'next_commands':['schedule day --date YYYY-MM-DD','schedule week']}
    page='\n'.join(result_card(result,'schedule_query').pages())
    assert '共4门课' not in page and '已完成' not in page
    assert '教学周未核验' in page
    assert '第3–4节' in page and '周次：1–18周' in page
    assert page.count('金明综合楼6202（134）')==1
    assert '\\[' not in page and '\\(' not in page
    assert 'schedule day' not in page and '同步课表' in page


def test_four_course_day_fits_one_page_without_splitting_course_details():
    result={'success':True,'date':'2026-10-19','week_filter_applied':False,'schedule':[{'course':name,'period':'第3-4节','time':'1-18周 一[3-4]','location':'金明综合楼6202(134)'} for name in ('计算机网络','大学体育（三）','野外生存技能','国家森林公园赏析与旅游')]}
    pages=result_card(result,'schedule_query').pages()
    assert len(pages)==1
    assert '国家森林公园赏析与旅游' in pages[0]
    assert pages[0].count('地点：')==4


def test_week_schedule_is_one_compact_card_with_explicit_day_sections():
    days = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    schedule = {day: [] for day in days}
    for day, count in [('星期一', 4), ('星期二', 4), ('星期四', 2), ('星期五', 1)]:
        schedule[day] = [dict(course=f'课程{n}', period=f'第{n*2+1}-{n*2+2}节', location='金明综合楼6202（134）', time='1-18周') for n in range(count)]
    pages = result_card(dict(success=True, schedule=schedule, week_filter_applied=True,
                            teaching_week=dict(week=8, week_start_date='2026-10-19'),
                            msg='第8教学周，匹配11项课程。'), 'schedule_query').pages()
    assert len(pages) == 1
    assert '## 星期一 · 10月19日' in pages[0]
    assert '## 星期二 · 10月20日' in pages[0]
    assert '## 星期三 · 10月21日' in pages[0]
    assert '当天无课' in pages[0]
    assert '周次：' not in pages[0]
    assert pages[0].count('课程') >= 11


def test_account_status_has_no_empty_item_or_internal_next_commands():
    source = {'success': True, 'account': {'account': {'student_id': '2512345683', 'has_password': True}},
              'library_defaults': {'location': '', 'seat_no': ''},
              'next_commands': ['account status', 'schedule now']}
    page = result_card(source, 'system_status').pages()[0]
    assert '条目' not in page
    assert 'account status' not in page and 'schedule now' not in page
    assert '今天课表' in page


def test_library_regions_are_compact_and_next_step_is_a_query_not_a_reservation():
    data = {'success': True, 'date': '2026-10-20', 'locations': [
        {'name': f'自习区域{i}', 'location': f'自习区域{i}', 'area_id': str(i)} for i in range(31)],
        'next_commands': ['library seats --location <区域>', 'library reserve --location <区域>']}
    pages = result_card(data, 'library_query').pages()
    assert len(pages) == 1
    assert '自习区域30' in pages[0]
    assert 'library reserve' not in pages[0]
    assert '区域名称' in pages[0] and '开始' in pages[0] and '结束' in pages[0]
    assert all(len(page.encode()) <= MAX_BYTES for page in pages)


def test_five_classrooms_fit_one_result_first_card_without_internal_metadata():
    source = {'success': True, 'total': 5,
              'query': {'termCode': '2026,0', 'week': 8, 'dayOfWeek': 2,
                        'period': 2, 'periodName': '第二大节', 'campusName': '金明校区', 'buildingName': '金明综合楼'},
              'freshness': {'source': 'xk_room_schedule', 'fetchedAt': '2026-10-19T17:21:05',
                            'ageSeconds': 0, 'stale': False, 'liveChecked': True,
                            'note': '仅反映学校系统登记的课程占用'},
              'rooms': [{'roomName': f'金明综合楼610{i}', 'capacity': 120} for i in range(1, 6)]}
    pages = result_card(source, 'empty_classroom_query').pages()
    assert len(pages) == 1
    assert all(f'金明综合楼610{i}' in pages[0] for i in range(1, 6))
    assert '第8周' in pages[0] and '星期二' in pages[0] and '第二大节' in pages[0]
    assert '2026,0' not in pages[0] and 'xk_room_schedule' not in pages[0]
    assert '缓存年龄' not in pages[0] and '来源获取时间' not in pages[0]
    assert '不保证现场' in pages[0]
