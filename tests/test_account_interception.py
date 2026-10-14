from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langbot_plugin.api.entities import context, events
from langbot_plugin.api.entities.builtin.platform import message as platform_message

from components.event_listener.identity_capture import IdentityCaptureListener


def _ctx(
    query_id: int,
    text: str,
    launcher_type: str = "person",
    sender_id: str = "10001",
):
    event_type = (
        events.PersonMessageReceived
        if launcher_type == "person"
        else events.GroupMessageReceived
    )
    # The production SDK requires a platform message_event that is owned by the
    # host process. Build the real SDK event model without validating that
    # host-only object, while still exercising EventContext/MessageChain.
    event = event_type.model_construct(
        launcher_type=launcher_type,
        launcher_id="20001",
        sender_id=sender_id,
        message_event=SimpleNamespace(),
        message_chain=platform_message.MessageChain(
            [platform_message.Plain(text=text)]
        ),
    )
    return context.EventContext(
        query_id=query_id,
        event_name=event.__class__.__name__,
        event=event,
    )


def _listener() -> IdentityCaptureListener:
    listener = IdentityCaptureListener.__new__(IdentityCaptureListener)
    listener.plugin = SimpleNamespace(service=SimpleNamespace(run_tool=object()))
    listener._account_command_tasks = {}
    listener._account_command_ack_claimed = set()
    listener._account_command_result_claimed = set()
    listener._account_user_tasks = {}
    listener._account_user_last_started = {}
    return listener


def test_real_event_context_prevents_before_auth_and_executes_exactly_once(
    monkeypatch,
) -> None:
    listener = _listener()
    calls = []
    gate = asyncio.Event()
    replies = []

    async def run_with_storage(*args):
        calls.append(args)
        await gate.wait()
        return {"success": True, "msg": "账号初始化完成"}

    async def reply(ctx, chain, quote_origin=False):
        replies.append((ctx.query_id, str(chain), quote_origin))

    listener._run_with_user_storage = run_with_storage
    monkeypatch.setattr(context.EventContext, "reply", reply)
    first = _ctx(101, "account set --student-id 20230001 --password 'DemoSecret'")
    duplicate = _ctx(
        101, "account set --student-id 20230001 --password 'DemoSecret'"
    )

    async def scenario():
        first_result = await asyncio.wait_for(
            listener._maybe_handle_account_set(first), timeout=0.1
        )
        assert first_result is True
        assert first.is_prevented_default()
        assert first.is_prevented_postorder()
        duplicate_result = await asyncio.wait_for(
            listener._maybe_handle_account_set(duplicate), timeout=0.1
        )
        assert duplicate_result is True
        assert duplicate.is_prevented_default()
        assert duplicate.is_prevented_postorder()
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert len(replies) == 1
        gate.set()
        await listener._account_command_tasks[101]

    asyncio.run(scenario())
    assert len(calls) == 1
    assert sum("正在验证" in text for _, text, _ in replies) == 1
    assert sum("账号初始化完成" in text for _, text, _ in replies) == 1


def test_account_set_limits_parallel_and_immediate_repeat_per_user(monkeypatch) -> None:
    listener = _listener()
    calls = []
    gate = asyncio.Event()
    replies = []

    async def run_with_storage(*args):
        calls.append(args)
        await gate.wait()
        return {"success": True, "msg": "账号登录验证成功，已完成绑定"}

    async def reply(ctx, chain, quote_origin=False):
        replies.append((ctx.query_id, str(chain), quote_origin))

    listener._run_with_user_storage = run_with_storage
    monkeypatch.setattr(context.EventContext, "reply", reply)
    first = _ctx(111, "account set --student-id 20230001 --password 'DemoSecret'")
    parallel = _ctx(112, "account set --student-id 20230001 --password 'DemoSecret'")
    immediate_repeat = _ctx(113, "account set --student-id 20230001 --password 'DemoSecret'")

    async def scenario():
        assert await listener._maybe_handle_account_set(first) is True
        await asyncio.sleep(0)
        assert await listener._maybe_handle_account_set(parallel) is True
        assert len(calls) == 1
        gate.set()
        await listener._account_command_tasks[111]
        assert await listener._maybe_handle_account_set(immediate_repeat) is True

    asyncio.run(scenario())
    assert len(calls) == 1
    assert any("正在进行" in text for _, text, _ in replies)
    assert any("请求过于频繁" in text for _, text, _ in replies)


def test_real_event_context_bad_format_group_and_naked_secret_never_authenticate(
    monkeypatch,
) -> None:
    listener = _listener()
    calls = []
    replies = []

    async def run_with_storage(*args):
        calls.append(args)
        return {"success": True}

    async def reply(ctx, chain, quote_origin=False):
        replies.append((ctx.query_id, str(chain), quote_origin))

    listener._run_with_user_storage = run_with_storage
    monkeypatch.setattr(context.EventContext, "reply", reply)
    bad = _ctx(201, "account set --student-id20230001 --password DemoSecret")
    group_inputs = [
        "学号 20230001 密码 DemoSecret",
        "账号 20230001 password DemoSecret",
        "password DemoSecret 用户名 20230001",
        "student id 20230001 密码 DemoSecret",
        "passwd DemoSecret username 20230001",
    ]
    groups = [
        _ctx(202 + index, text, launcher_type="group")
        for index, text in enumerate(group_inputs)
    ]
    naked = _ctx(220, "DemoSecret")

    async def scenario():
        assert await listener._maybe_handle_account_set(bad) is True
        for group in groups:
            assert await listener._maybe_handle_account_set(group) is True
        assert await listener._maybe_handle_account_set(naked) is False

    asyncio.run(scenario())
    assert calls == []
    assert bad.is_prevented_default()
    assert all(group.is_prevented_default() for group in groups)
    assert not naked.is_prevented_default()
    assert len(replies) == 1 + len(groups)
    assert all("DemoSecret" not in text for _, text, _ in replies)


def test_account_set_without_authoritative_sender_fails_closed(monkeypatch) -> None:
    listener = _listener()
    calls = []
    replies = []

    async def run_with_storage(*args):
        calls.append(args)
        return {"success": True}

    async def reply(ctx, chain, quote_origin=False):
        replies.append((ctx.query_id, str(chain), quote_origin))

    listener._run_with_user_storage = run_with_storage
    monkeypatch.setattr(context.EventContext, "reply", reply)
    missing_sender = _ctx(
        230,
        "account set --student-id 20230001 --password 'DemoSecret'",
        sender_id="",
    )

    assert asyncio.run(listener._maybe_handle_account_set(missing_sender)) is True
    assert calls == []
    assert missing_sender.is_prevented_default()
    assert any("无法确认" in text for _, text, _ in replies)


def test_account_status_and_runtime_prompt_expose_latest_login_verification() -> None:
    listener = _listener()
    result = {
        "success": True,
        "session_binding": {"qq": "10001"},
        "account": {
            "account": {
                "student_id": "20230001",
                "has_password": True,
                "login_verification": {
                    "status": "verified",
                    "verified": True,
                    "attempted_at": "2026-09-21T10:00:00+08:00",
                    "auth_mode": "ids_cas",
                    "error_code": "",
                    "message": "IDS 统一认证登录成功",
                },
                "last_login_attempt": {
                    "status": "verified",
                    "verified": True,
                    "attempted_at": "2026-09-21T10:00:00+08:00",
                    "subject_student_id": "20230001",
                    "error_code": "",
                    "message": "IDS 统一认证登录成功",
                },
            }
        },
    }
    runtime_context = {
        "binding": {"qq": "10001", "sender_id": "10001"},
        "account": result["account"]["account"],
        "server_time": {
            "now_text": "2026-09-21 10:01:00",
            "weekday_cn": "星期一",
            "timezone": "Asia/Shanghai",
        },
    }

    status_text = listener._format_account_status(result)
    prompt = listener._format_runtime_prompt_block(runtime_context, "")
    altered = listener._format_user_message_with_context(
        runtime_context, "", "帮我查课表"
    )

    assert "最近登录校验: 成功" in status_text
    assert "最近一次在线登录校验: 成功" in prompt
    assert "不要把该账号说成未登录" in prompt
    assert "最近登录校验】成功" in altered


def test_private_followup_always_gets_runtime_context_gate() -> None:
    listener = _listener()
    followup = SimpleNamespace(text_message="1")

    assert listener._should_inject_runtime_context(
        followup,
        {"henu_launcher_type": "person"},
    ) is True
    assert listener._should_inject_runtime_context(
        followup,
        {"henu_launcher_type": "group"},
    ) is False


def test_ack_failure_still_returns_blocked_context_without_authentication(
    monkeypatch,
) -> None:
    listener = _listener()
    calls = []

    async def run_with_storage(*args):
        calls.append(args)
        return {"success": True}

    async def reply(_ctx, _chain, quote_origin=False):
        del quote_origin
        raise TimeoutError("host reply timeout")

    listener._run_with_user_storage = run_with_storage
    monkeypatch.setattr(context.EventContext, "reply", reply)
    ctx = _ctx(301, "account set --student-id 20230001 --password 'DemoSecret'")

    handled = asyncio.run(listener._maybe_handle_account_set(ctx))

    assert handled is True
    assert ctx.is_prevented_default()
    assert ctx.is_prevented_postorder()
    assert calls == []


def test_rejection_reply_failure_still_returns_blocked_context(
    monkeypatch,
) -> None:
    listener = _listener()
    calls = []

    async def run_with_storage(*args):
        calls.append(args)
        return {"success": True}

    async def reply(_ctx, _chain, quote_origin=False):
        del quote_origin
        raise TimeoutError("host reply timeout")

    listener._run_with_user_storage = run_with_storage
    monkeypatch.setattr(context.EventContext, "reply", reply)
    ctx = _ctx(
        302,
        "账号 20230001 密码 DemoSecret",
        launcher_type="group",
    )

    handled = asyncio.run(listener._maybe_handle_account_set(ctx))

    assert handled is True
    assert ctx.is_prevented_default()
    assert ctx.is_prevented_postorder()
    assert calls == []
