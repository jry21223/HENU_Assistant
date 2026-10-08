"""Actual private service + bridge/executor failure-closed boundaries."""
import asyncio
from unittest.mock import Mock

import pytest

from test_yuketang_transactions import Storage, ctx
from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
from henu_plugin.yuketang_login import YuketangLoginCoordinator
from henu_plugin import bridge_client


def test_no_member_or_unverified_executor_never_changes_config_or_logs_in(core, monkeypatch):
    data,_,_ = core
    post = Mock()
    monkeypatch.setattr(bridge_client,'login_password',post)
    monkeypatch.setattr(bridge_client,'push_config',post)
    async def run():
        plugin = Storage()
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        flow = YuketangLoginCoordinator(listener)
        request = ctx(1,'')
        data.update(plan='free',lifetime=False,allowed=False,benefits=[])
        denied = await flow.call_service(request,'henu_cli',{'command':'yuketang enable'})
        assert denied['error_code'] == 'LIFETIME_REQUIRED'
        data.update(plan='lifetime',lifetime=True,allowed=True,benefits=['yuketang','ucampus','chaoxing','library_schedule'])
        for command in ('yuketang enable','yuketang domain set --domain pro'):
            blocked = await flow.call_service(request,'henu_cli',{'command':command})
            assert blocked['error_code'] == 'EXECUTOR_UNAVAILABLE'
        blocked = await flow.call_service(request,'yuketang_login',{})
        assert blocked['error_code'] == 'EXECUTOR_UNAVAILABLE'
        data.update(plan='free',lifetime=False,allowed=False,benefits=[])
        assert (await flow.call_service(request,'henu_cli',{'command':'yuketang disable'}))['success']
        assert not post.called
    asyncio.run(run())


def test_bridge_v1_cannot_accept_new_execution_or_enabled_configuration(monkeypatch):
    post = Mock()
    monkeypatch.setattr('requests.post',post)
    for call in (lambda:bridge_client.push_config('alice',{'enabled':True}),
                 lambda:bridge_client.login_password('alice','student','secret'),
                 lambda:bridge_client.login_start('alice')):
        with pytest.raises(bridge_client.BridgeError, match='会员'):
            call()
    assert not post.called


def test_other_qq_stop_finishes_while_core_qualification_is_blocked(core, monkeypatch):
    import threading
    import requests
    entered, release = threading.Event(), threading.Event()
    original_post = requests.post
    def blocked(url, **kwargs):
        entered.set()
        assert release.wait(2)
        return original_post(url, **kwargs)
    monkeypatch.setattr('requests.post',blocked)
    async def run():
        plugin = Storage()
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        flow = YuketangLoginCoordinator(listener)
        alice = ctx(20,'')
        bob = ctx(21,'')
        bob.event.sender_id = bob.event.launcher_id = 'test-b'
        check = asyncio.create_task(flow.call_service(alice,'yuketang_login',{}))
        try:
            assert await asyncio.to_thread(entered.wait,1)
            stopped = await asyncio.wait_for(flow.call_service(bob,'yuketang_set_enabled',{'enabled':'off'}),.3)
            assert stopped['success']
        finally:
            release.set()
            await check
    asyncio.run(run())
