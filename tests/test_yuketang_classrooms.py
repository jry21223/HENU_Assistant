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


def test_public_cli_keeps_quiz_identity_and_never_uses_exam_cover(monkeypatch):
    async def run():
        payloads = [course_payload(), {"errcode": 0, "data": {"activities": [
            {"type": 4, "courseware_id": 4446945, "status": 0,
             "title": "数字逻辑第2次测试", "deadline": 1792943940000}]}}]
        _, tool, session, get, push = make_tool(monkeypatch, payloads)
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        assert result["success"], result
        activity = result["exams"][0]
        assert activity["assessment_type"] == "quiz"
        assert activity["quiz_id"] == "4446945" and "exam_id" not in activity
        assert activity["classroom_id"] == "31921915"
        assert activity["deadline_china"] == "2026-10-25T23:59:00+08:00"
        assert "测验" in result["reply_text"]
        assert not push.called
        assert all("/v/exam/" not in call.args[0] for call in get.call_args_list)
    asyncio.run(run())


@pytest.mark.parametrize("cover_change", [
    {"title": "另一份试卷"},
    {"classroom_id": 999},
    {"classroom_id": None},
    {"exam_id": 999},
    {"exam_id": None},
])
def test_public_cli_rejects_exam_cover_not_bound_to_requested_activity(monkeypatch, cover_change):
    async def run():
        cover = {"title": "目标考试", "classroom_id": 31921915, "exam_id": 900,
                 "deadline": 1792943940000, **cover_change}
        payloads = [course_payload(), {"errcode": 0, "data": {"activities": [
            {"type": 5, "courseware_id": 900, "status": 0, "title": "目标考试"}]}},
            {"status": 200, "data": cover}]
        _, tool, session, _, push = make_tool(monkeypatch, payloads)
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        assert not result["success"]
        assert result["error_code"] == "rain_assessment_mismatch"
        assert not push.called and "exams" not in result
        assert "另一份试卷" not in str(result)
    asyncio.run(run())


def test_public_cli_does_not_use_idless_cover_to_override_activity_time(monkeypatch):
    async def run():
        payloads = [course_payload(), {"errcode": 0, "data": {"activities": [
            {"type": 5, "courseware_id": 900, "status": 0, "title": "目标考试",
             "deadline": 1791734340000}]}}, {"status": 200, "data": {
                 "title": "目标考试", "classroom_id": 31921915, "deadline": 1792943940000}}]
        _, tool, session, _, push = make_tool(monkeypatch, payloads)
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        assert result["success"], result
        assert result["exams"][0]["deadline_china"] == "2026-10-11T23:59:00+08:00"
        assert result["exams"][0]["cover_association_verified"] is False
        assert "封面关联未核实" in result["reply_text"] and not push.called
    asyncio.run(run())


def test_public_cli_rejects_foreign_classroom_declared_by_cover_envelope(monkeypatch):
    async def run():
        payloads = [course_payload(), {"errcode": 0, "data": {"activities": [
            {"type": 5, "courseware_id": 900, "status": 0, "title": "目标考试"}]}},
            {"status": 200, "classroom_id": 999, "data": {
                "title": "目标考试", "classroom_id": 31921915, "exam_id": 900}}]
        plugin, tool, session, _, push = make_tool(monkeypatch, payloads)
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        assert not result["success"] and result["error_code"] == "rain_assessment_mismatch"
        assert "exams" not in result and not push.called
        assert "user:classroom-test:yuketang_config" not in plugin.data
    asyncio.run(run())


@pytest.mark.parametrize("level", ["payload", "data"])
def test_public_cli_rejects_foreign_classroom_declared_by_response_envelope(monkeypatch, level):
    async def run():
        payload = {"errcode": 0, "data": {"activities": [
            {"type": 4, "courseware_id": 123, "status": 0, "title": "测验"}]}}
        envelope = payload if level == "payload" else payload["data"]
        envelope["classroom_id"] = 999
        plugin, tool, session, get, push = make_tool(monkeypatch, [course_payload(), payload])
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        assert not result["success"] and result["error_code"] == "rain_response_invalid"
        assert "exams" not in result and not push.called
        assert len(get.call_args_list) == 2
        assert "user:classroom-test:yuketang_config" not in plugin.data
    asyncio.run(run())


@pytest.mark.parametrize("activity_change", [
    {"type": None}, {"type": True}, {"type": "4"}, {"type": 6},
    {"classroom_id": 999},
])
def test_public_cli_rejects_unknown_type_or_foreign_classroom_before_cover(monkeypatch, activity_change):
    async def run():
        activity = {"type": 4, "courseware_id": 123, "status": 0,
                    "title": "测验", **activity_change}
        _, tool, session, get, push = make_tool(monkeypatch, [course_payload(),
            {"errcode": 0, "data": {"activities": [activity]}}])
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        assert not result["success"] and result["error_code"] == "rain_response_invalid"
        assert len(get.call_args_list) == 2 and not push.called
    asyncio.run(run())


@pytest.mark.parametrize("same_type", [True, False])
def test_public_cli_uses_type_and_classroom_as_part_of_activity_identity(monkeypatch, same_type):
    async def run():
        rows = [
            {"type": 4, "courseware_id": 123, "status": 2, "title": "测验"},
            {"type": 4 if same_type else 5, "courseware_id": 123, "status": 2, "title": "另一活动"},
        ]
        _, tool, session, get, push = make_tool(monkeypatch, [course_payload(),
            {"errcode": 0, "data": {"activities": rows}}])
        result = await tool.call({"command": 'yuketang exam list --course "010 班"'}, session, 1)
        if same_type:
            assert not result["success"] and result["error_code"] == "rain_response_invalid"
        else:
            assert result["success"] and result["total"] == 2
            assert result["exams"][0]["assessment_type"] == "quiz"
            assert result["exams"][1]["assessment_type"] == "exam"
            assert "quiz_id" in result["exams"][0] and "exam_id" in result["exams"][1]
        assert len(get.call_args_list) == 2 and not push.called
    asyncio.run(run())


def test_public_cli_reads_unstarted_exam_and_only_returns_metadata(monkeypatch):
    async def run():
        payloads = [course_payload(), {"errcode": 0, "data": {"activities": [
            {"type": 5, "courseware_id": 4637969, "status": 0, "title": "过程性测试1（第1-2章）"}]}},
            {"status": 200, "data": {"deadline": 1791734340000, "description": "private-never-output",
                                     "classroom_id": 31921915, "exam_id": 4637969, "title": "过程性测试1（第1-2章）",
                                     "questions": ["private-never-output"], "user": "private-never-output"}}]
        _, tool, session, get, push = make_tool(monkeypatch, payloads)
        result = await tool.call({"command": 'yuketang exam list --course "数据结构"'}, session, 1)
        assert result["success"], result
        assert result["classroom"]["classroom_label"] == "010 班"
        assert result["exams"][0]["status_label"] == "尚未开始作答"
        assert result["exams"][0]["deadline_china"] == "2026-10-11T23:59:00+08:00"
        assert result["exams"][0]["cover_association_verified"] is True
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
