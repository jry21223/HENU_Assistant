"""Background features must survive SDK 0.7 invocation teardown."""

import asyncio
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import langbot_plugin

invocation = pytest.importorskip("langbot_plugin.api.proxies.invocation")
from langbot_plugin.api.proxies.langbot_api import LangBotAPIProxy
from langbot_plugin.entities.io.context import InstallationBinding
from langbot_plugin.entities.io.actions.enums import RuntimeToPluginAction

from henu_plugin.schedule_reminders import ScheduleReminders
from henu_plugin.library_automation import LibraryAutomation
from henu_plugin.yuketang_login import YuketangLoginCoordinator

spec = importlib.util.spec_from_file_location(
    "sdk_lifecycle_dispatch", Path(langbot_plugin.__file__).parent / "cli/run/handler.py"
)
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


def binding():
    return InstallationBinding(instance_uuid="instance", workspace_uuid="workspace",
                               placement_generation=1, installation_uuid="installation",
                               runtime_revision=1, artifact_digest="a" * 64)


@pytest.mark.parametrize("feature", [ScheduleReminders, LibraryAutomation])
def test_background_registry_remains_accessible_after_initialize_returns(feature):
    async def run():
        scope = binding()
        async def initialize(_settings):
            reminders.start()

        rpc = sdk.PluginRuntimeHandler(None, initialize)
        rpc.plugin_container = SimpleNamespace(plugin_config={})
        rpc.call_action = AsyncMock(return_value={
            "value_base64": base64.b64encode(b'{"saved-user": {}}').decode()
        })
        plugin = LangBotAPIProxy(rpc)
        release = asyncio.Event()
        observed = asyncio.get_running_loop().create_future()
        reminders = feature(plugin)

        async def scan():
            await release.wait()
            try:
                observed.set_result(await reminders.read())
            except Exception as exc:
                observed.set_exception(exc)

        async def refresh():
            await release.wait()

        reminders.run = scan
        reminders.run_refresh = refresh
        action = RuntimeToPluginAction.INITIALIZE_PLUGIN
        with rpc.action_invocation_scope(action.value, scope):
            await rpc.actions[action.value]({"plugin_settings": {}})
        release.set()
        try:
            assert await asyncio.wait_for(observed, 1) == {"saved-user": {}}
            assert rpc.call_action.await_count == 1
        finally:
            tasks = [reminders.task]
            if hasattr(reminders, "refresh_task"):
                tasks.append(reminders.refresh_task)
            await asyncio.gather(*tasks)

    asyncio.run(run())


@pytest.mark.parametrize("feature", [ScheduleReminders, LibraryAutomation])
def test_shared_installation_cannot_start_process_owned_background_features(feature):
    async def run():
        rpc = SimpleNamespace(bound_action_context=None, call_action=AsyncMock())
        plugin = LangBotAPIProxy(rpc)
        background = feature(plugin)
        with invocation.bind_invocation(rpc, binding=binding()):
            with pytest.raises(RuntimeError, match="dedicated installation"):
                background.start()
        assert background.task is None
        rpc.call_action.assert_not_awaited()

    asyncio.run(run())


def test_private_credential_confirmation_is_saved_after_event_returns():
    from tests.test_yuketang_login import context

    async def run():
        scope = binding()
        rpc = SimpleNamespace(bound_action_context=scope, call_action=AsyncMock(return_value={}))
        plugin = LangBotAPIProxy(rpc)
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=plugin))
        request = context(10, "yuketang account set --account 13800000000 --password 'synthetic'")
        with invocation.bind_invocation(rpc, binding=scope):
            assert await flow.handle(request, is_group=False)
        await asyncio.gather(*flow.tasks.values())
        assert rpc.call_action.await_count == 1
        payload = rpc.call_action.call_args.args[1]
        assert payload["key"] == "user:user-a:yuketang_pending_credentials"
        assert "synthetic" not in str(payload)
        assert "确认" in str(request.reply.call_args)

    asyncio.run(run())
