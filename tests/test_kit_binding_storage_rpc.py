"""Binding storage behavior through the real SDK's two RPC hops."""

import asyncio
import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langbot_plugin.api.proxies.langbot_api import LangBotAPIProxy
from langbot_plugin.entities.io.actions.enums import RuntimeToLangBotAction
from langbot_plugin.entities.io.resp import ActionResponse
from langbot_plugin.runtime.context import RuntimeContext
from langbot_plugin.runtime.io.connection import Connection
from langbot_plugin.runtime.io.handler import Handler
from langbot_plugin.runtime.io.handlers.plugin import PluginConnectionHandler

from henu_plugin.kit_binding import KitBindingCoordinator
from tests.test_kit_binding import kit_event


class MemoryConnection(Connection):
    def __init__(self):
        self.messages = asyncio.Queue()
        self.peer = None

    async def send(self, message):
        await self.peer.messages.put(message)

    async def receive(self):
        return await self.messages.get()

    async def close(self):
        pass


def connection_pair():
    first, second = MemoryConnection(), MemoryConnection()
    first.peer, second.peer = second, first
    return first, second


@asynccontextmanager
async def storage_api(monkeypatch, tmp_path, read_error=None, *, malformed_success=False):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("henu_plugin.kit_binding.ACTIVATION_DELAY_SECONDS", -120)
    monkeypatch.setattr(
        "henu_plugin.kit_binding.kit_settings",
        lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
    )
    main_conn, control_conn = connection_pair()
    plugin_conn, runtime_conn = connection_pair()
    main, control, client = (
        Handler(main_conn),
        Handler(control_conn),
        Handler(plugin_conn),
    )
    context = RuntimeContext()
    context.control_handler = control
    context.plugin_mgr = SimpleNamespace(plugins=[])
    runtime = PluginConnectionHandler(runtime_conn, context)
    context.plugin_mgr.plugins.append(
        SimpleNamespace(
            _runtime_plugin_handler=runtime,
            manifest=SimpleNamespace(
                metadata=SimpleNamespace(author="test", name="binding")
            ),
        )
    )
    storage = {}

    @main.action(RuntimeToLangBotAction.GET_BINARY_STORAGE)
    async def get_storage(data):
        if malformed_success:
            return ActionResponse.success({})
        if read_error and (message := read_error(data["key"])):
            return ActionResponse.error(message)
        if data["key"] not in storage:
            # This is the production main handler's missing-record response.
            return ActionResponse.error(f"Storage with key {data['key']} not found")
        return ActionResponse.success(
            {"value_base64": base64.b64encode(storage[data["key"]]).decode()}
        )

    @main.action(RuntimeToLangBotAction.SET_BINARY_STORAGE)
    async def set_storage(data):
        storage[data["key"]] = base64.b64decode(data["value_base64"])
        return ActionResponse.success({})

    tasks = [
        asyncio.create_task(handler.run())
        for handler in (main, control, client, runtime)
    ]
    try:
        yield LangBotAPIProxy(client)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize(
    ("command", "action", "reply"),
    [
        ("HENU KIT 状态", "status", "尚未绑定 HENU KIT"),
        ("绑定 HENU KIT", "start", "请打开链接登录 HENU KIT"),
    ],
)
def test_first_command_through_real_storage_rpc(
    monkeypatch, tmp_path, command, action, reply
):
    async def run():
        async with storage_api(monkeypatch, tmp_path) as api:
            flow = KitBindingCoordinator(SimpleNamespace(plugin=api))
            remote = Mock(return_value={"bound": False, "token": "a" * 43})
            monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
            request = kit_event(command, 1, "first-source")
            try:
                assert await flow.handle(request, is_group=False)
                assert remote.call_count == 1
                assert remote.call_args.args[1] == action
                assert reply in str(request.reply.call_args)
            finally:
                for watcher in flow.watchers.values():
                    watcher.cancel()
                await asyncio.gather(*flow.watchers.values(), return_exceptions=True)

    asyncio.run(run())


def test_unlink_plain_confirmation_through_real_storage_rpc(monkeypatch, tmp_path):
    async def run():
        async with storage_api(monkeypatch, tmp_path) as api:
            flow = KitBindingCoordinator(SimpleNamespace(plugin=api))
            remote = Mock(return_value={"bound": False})
            monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
            prompt = kit_event("解绑 HENU KIT", 1, "unlink-source")
            assert await flow.handle(prompt, is_group=False)
            assert "后续操作不再使用" in str(prompt.reply.call_args)
            request = kit_event("确认", 2, "confirm-source")
            assert await flow.handle(request, is_group=False)
            assert remote.call_count == 1
            assert remote.call_args.args[1] == "unlink"
            assert "已解除 HENU KIT 绑定" in str(request.reply.call_args)

    asyncio.run(run())


@pytest.mark.parametrize(
    "message",
    [
        "Storage backend unavailable",
        "Storage with key different-key not found",
        "ActionCallError: Storage with key {key} not found",
    ],
)
def test_status_rpc_errors_fail_closed(monkeypatch, tmp_path, message):
    async def run():
        def read_error(key):
            return message.format(key=key)

        async with storage_api(monkeypatch, tmp_path, read_error) as api:
            flow = KitBindingCoordinator(SimpleNamespace(plugin=api))
            remote = Mock()
            monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
            request = kit_event("HENU KIT 状态", 1, "status-source")
            assert await flow.handle(request, is_group=False)
            remote.assert_not_called()
            assert "暂时无法读取或保存" in str(request.reply.call_args)

    asyncio.run(run())


def test_unlink_other_pending_rpc_error_fail_closed(monkeypatch, tmp_path):
    async def run():
        def read_error(key):
            return "Storage backend unavailable" if key.startswith("user:") else None

        async with storage_api(monkeypatch, tmp_path, read_error) as api:
            flow = KitBindingCoordinator(SimpleNamespace(plugin=api))
            remote = Mock()
            monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
            prompt = kit_event("解绑 HENU KIT", 1, "unlink-source")
            assert await flow.handle(prompt, is_group=False)
            assert "后续操作不再使用" in str(prompt.reply.call_args)
            request = kit_event("确认", 2, "confirm-source")
            assert await flow.handle(request, is_group=False)
            remote.assert_not_called()
            assert "暂时无法读取或保存" in str(request.reply.call_args)

    asyncio.run(run())


def test_malformed_storage_success_blocks_status(monkeypatch, tmp_path):
    async def run():
        async with storage_api(monkeypatch, tmp_path, malformed_success=True) as api:
            flow = KitBindingCoordinator(SimpleNamespace(plugin=api))
            remote = Mock()
            monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
            request = kit_event("HENU KIT 状态", 1, "status-source")
            assert await flow.handle(request, is_group=False)
            remote.assert_not_called()
            assert "暂时无法读取或保存" in str(request.reply.call_args)

    asyncio.run(run())
