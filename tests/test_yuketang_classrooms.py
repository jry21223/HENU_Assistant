from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from langbot_plugin.api.entities.builtin.provider import session as provider_session

from components.cli_tools.henu_cli import HenuCli
from henu_plugin import bridge_client
from henu_plugin.hardened_service import HardenedHenuPluginService


ROOT = Path(__file__).resolve().parents[1]
COURSE = {"name": "010 班", "classroom_id": 31921915, "course": {"name": "数据结构"}}


class Storage:
    def __init__(self):
        self.data = {}
        self.service = HardenedHenuPluginService(ROOT)

    async def get_plugin_storage_keys(self): return list(self.data)
    async def get_plugin_storage(self, key): return self.data.get(key, b"{}")
    async def set_plugin_storage(self, key, value): self.data[key] = value


def test_confirmed_exam_whitelist_resolves_course_name_before_commit_and_bridge(monkeypatch):
    async def run():
        plugin = Storage()
        tool = HenuCli()
        tool.plugin = plugin
        session = provider_session.Session(sender_id="classroom-test", launcher_id="classroom-test",
                                           launcher_type=provider_session.LauncherTypes.PERSON)
        monkeypatch.setattr(bridge_client, "bridge_settings", lambda: {"url": "https://test.invalid"})
        status = Mock(return_value={"ok": True, "cookie": {"cookie": "synthetic-cookie"}})
        monkeypatch.setattr(bridge_client, "fetch_status", status)
        import requests
        response = SimpleNamespace(status_code=200, json=lambda: {"errcode": 0, "data": {"list": [COURSE]}})
        get = Mock(return_value=response)
        monkeypatch.setattr(requests, "get", get)

        def push(user, config):
            stored = json.loads(plugin.data["user:classroom-test:yuketang_config"])
            assert stored["exam"]["classroomWhiteList"] == ["010 班"]
            assert config["exam"]["classroomWhiteList"] == ["010 班"]
            return {"ok": True, "hash": bridge_client.canonical_hash(config)}

        push_mock = Mock(side_effect=push)
        monkeypatch.setattr(bridge_client, "push_config", push_mock)
        pending = await tool.call({"command": "yuketang exam whitelist add 数据结构"}, session, 1)
        assert pending["confirmation_required"] and not push_mock.called and not get.called
        record = json.loads(plugin.data["user:classroom-test:pending_operation"])
        result = await tool.call({"command": "confirm " + record["token"]}, session, 2)
        assert result["success"], result
        assert json.loads(plugin.data["user:classroom-test:yuketang_config"])["exam"]["classroomWhiteList"] == ["010 班"]
        assert "数据结构" in result["reply_text"] and "010 班" in result["reply_text"]
        assert push_mock.call_count == 1
        assert get.call_args.kwargs["allow_redirects"] is False
        assert "synthetic-cookie" not in str(result)
    asyncio.run(run())


def make_tool(monkeypatch, payloads):
    plugin = Storage()
    tool = HenuCli()
    tool.plugin = plugin
    session = provider_session.Session(sender_id="classroom-test", launcher_id="classroom-test",
                                       launcher_type=provider_session.LauncherTypes.PERSON)
    monkeypatch.setattr(bridge_client, "bridge_settings", lambda: {"url": "https://test.invalid"})
    monkeypatch.setattr(bridge_client, "fetch_status", lambda *args, **kwargs: {
        "ok": True, "cookie": {"cookie": "synthetic-cookie"}})
    import requests
    get = Mock(side_effect=[SimpleNamespace(status_code=200, json=lambda payload=payload: payload) for payload in payloads])
    monkeypatch.setattr(requests, "get", get)
    push = Mock(side_effect=lambda user, config: {"ok": True, "hash": bridge_client.canonical_hash(config)})
    monkeypatch.setattr(bridge_client, "push_config", push)
    return plugin, tool, session, get, push


def course_payload(courses=None):
    return {"errcode": 0, "data": {"list": [COURSE] if courses is None else courses}}


def test_public_cli_reads_unstarted_exam_and_only_returns_metadata(monkeypatch):
    async def run():
        payloads = [course_payload(), {"errcode": 0, "data": {"activities": [
            {"courseware_id": 4637969, "status": 0, "title": "过程性测试1（第1-2章）"}]}},
            {"status": 200, "data": {"deadline": 1791734340000, "description": "private-never-output",
                                     "questions": ["private-never-output"], "user": "private-never-output"}}]
        _, tool, session, get, push = make_tool(monkeypatch, payloads)
        result = await tool.call({"command": 'yuketang exam list --course "数据结构"'}, session, 1)
        assert result["success"], result
        assert result["classroom"]["classroom_label"] == "010 班"
        assert result["exams"][0]["status_label"] == "尚未开始作答"
        assert result["exams"][0]["deadline_china"] == "2026-10-11T23:59:00+08:00"
        assert len(get.call_args_list) == 3 and not push.called
        urls = [call.args[0] for call in get.call_args_list]
        assert urls[-1] == "https://www.yuketang.cn/v/exam/cover?exam_id=4637969&classroom_id=31921915"
        assert all(call.kwargs["allow_redirects"] is False for call in get.call_args_list)
        assert "private-never-output" not in str(result) and "synthetic-cookie" not in str(result)
    asyncio.run(run())


@pytest.mark.parametrize("courses, items, code", [
    ([COURSE, {**COURSE, "name": "011 班", "classroom_id": 31921916}], "数据结构", "rain_course_ambiguous"),
    ([COURSE], "不存在", "rain_course_not_found"),
    ([COURSE], "数据结构 不存在", "rain_course_not_found"),
    ([COURSE, {**COURSE, "classroom_id": 9, "course": {"name": "操作系统"}}], "数据结构", "rain_classroom_label_ambiguous"),
])
def test_invalid_or_ambiguous_whitelist_is_not_committed_or_pushed(monkeypatch, courses, items, code):
    async def run():
        plugin, tool, session, _, push = make_tool(monkeypatch, [course_payload(courses)])
        await tool.call({"command": "yuketang exam whitelist add " + items}, session, 1)
        record = json.loads(plugin.data["user:classroom-test:pending_operation"])
        result = await tool.call({"command": "confirm " + record["token"]}, session, 2)
        assert result["success"] is False and result["error_code"] == code, result
        assert not push.called
        assert "user:classroom-test:yuketang_config" not in plugin.data
    asyncio.run(run())


@pytest.mark.parametrize("command", ['yuketang exam whitelist remove "数据结构"', "yuketang exam whitelist clear"])
def test_removing_old_configuration_remains_possible_without_login(monkeypatch, command):
    async def run():
        plugin, tool, session, get, _ = make_tool(monkeypatch, [])
        from henu_plugin import yuketang_config
        from henu_plugin.storage_adapter import PluginStorageAdapter
        config = yuketang_config.default_config()
        config["exam"]["classroomWhiteList"] = ["数据结构"]
        adapter = PluginStorageAdapter(plugin, "classroom-test")
        paths = await adapter.load_all()
        yuketang_config.save_config(paths.yuketang_config_file, config)
        await adapter.save_all()
        status = Mock(side_effect=bridge_client.BridgeError("offline"))
        monkeypatch.setattr(bridge_client, "fetch_status", status)
        monkeypatch.setattr(bridge_client, "bridge_settings", lambda: None)
        await tool.call({"command": command}, session, 1)
        record = json.loads(plugin.data["user:classroom-test:pending_operation"])
        result = await tool.call({"command": "confirm " + record["token"]}, session, 2)
        assert result["success"], result
        assert json.loads(plugin.data["user:classroom-test:yuketang_config"])["exam"]["classroomWhiteList"] == []
        assert not get.called and not status.called
    asyncio.run(run())


@pytest.mark.parametrize("empty, expected_success", [(True, True), (False, False)])
def test_empty_exams_are_success_and_business_query_errors_are_not(monkeypatch, empty, expected_success):
    async def run():
        payload = {"errcode": 0, "data": {"activities": []}} if empty else {"errcode": 401, "data": {}}
        _, tool, session, _, push = make_tool(monkeypatch, [course_payload(), payload])
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        assert result["success"] is expected_success
        assert not push.called
        if empty:
            assert result["total"] == 0 and result["exams"] == []
        else:
            assert result["error_code"] == "rain_query_failed"
    asyncio.run(run())


def test_course_list_readonly_and_login_failure_are_credential_safe(monkeypatch):
    async def run():
        _, tool, session, get, push = make_tool(monkeypatch, [course_payload()])
        result = await tool.call({"command": "yuketang courses"}, session, 1)
        assert result["success"] and result["courses"] == [
            {"course_name": "数据结构", "classroom_label": "010 班", "classroom_id": "31921915"}]
        assert get.call_count == 1 and not push.called
        monkeypatch.setattr(bridge_client, "fetch_status", lambda *args, **kwargs: {"ok": True})
        result = await tool.call({"command": "yuketang courses"}, session, 2)
        assert result["success"] is False and result["error_code"] == "rain_login_required"
        assert get.call_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["redirect", "network", "malformed"])
def test_query_transport_failures_do_not_leak_credentials(monkeypatch, failure):
    async def run():
        _, tool, session, get, _ = make_tool(monkeypatch, [])
        import requests
        get.side_effect = None
        if failure == "network":
            get.side_effect = requests.ConnectionError("synthetic-cookie-private-error")
        elif failure == "redirect":
            get.return_value = SimpleNamespace(status_code=302)
        else:
            get.return_value = SimpleNamespace(status_code=200, json=lambda: ["synthetic-cookie"])
        result = await tool.call({"command": "yuketang courses"}, session, 1)
        assert result["success"] is False
        assert "synthetic-cookie" not in str(result)
    asyncio.run(run())


def test_composite_exam_title_does_not_echo_private_payload(monkeypatch):
    async def run():
        payload = {"errcode": 0, "data": {"activities": [
            {"courseware_id": 4637969, "status": 2, "title": {"password": "synthetic-private-field"}}]}}
        _, tool, session, _, _ = make_tool(monkeypatch, [course_payload(), payload])
        result = await tool.call({"command": "yuketang exam list --course 数据结构"}, session, 1)
        assert result["success"] is False and result["error_code"] == "rain_response_invalid"
        assert "synthetic-private-field" not in str(result)
    asyncio.run(run())
