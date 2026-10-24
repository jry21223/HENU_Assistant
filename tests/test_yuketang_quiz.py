from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes, Session

from components.cli_tools.henu_cli_safe import HenuCliSafe
from henu_plugin import bridge_client, yuketang_config
from henu_plugin.hardened_service import HardenedHenuPluginService


ROOT = Path(__file__).resolve().parents[1]
TARGET = '--course "合成课程" --quiz-id 9001'
ANSWERS = {"601": ["A"], "602": ["A", "C"], "603": {str(i): f"value-{i}" for i in range(1, 19)}}


class QuizServer:
    """External Rain HTTP fixture, including its independently readable cache."""

    def __init__(self):
        self.status = 1
        self.cache = {}
        self.posts = []
        self.user_id = 41
        self.visible = True
        self.grade_overrides = {}
        self.other_activities = []
        self.bridge_status = {"ok": True, "cookie": {"cookie": "csrftoken=synthetic; sessionid=private-cookie"},
            "daemon_wired": True, "daemon": {"openids": [], "age_s": 1}, "config_present": True,
            "config_hash": bridge_client.canonical_hash(yuketang_config.default_config())}
        self.title = "合成测验"
        self.slides = [
            {"Shapes": [{"Text": "合成选择题", "URL": "https://rain-public-qn.yuketang.cn/public/synthetic/one.png"}],
             "Problem": {"ProblemID": 601, "Type": "MultipleChoice", "Score": 1,
                         "Bullets": [{"Label": "A", "Text": "选项甲"}, {"Label": "B", "Text": "选项乙"}],
                         "Answer": "hidden-standard-answer", "Remark": "hidden-standard-answer"}},
            {"Shapes": [{"Text": "合成多选题"}], "Problem": {
                "ProblemID": 602, "Type": "MultipleChoiceMA", "Score": 1,
                "Bullets": [{"Label": label} for label in "ABC"], "Answer": "hidden-standard-answer"}},
            {"Shapes": [{"Text": "完整题面" * 800}], "Problem": {
                "ProblemID": 603, "Type": "FillBlank", "Score": 18,
                "Blanks": [{"Num": i, "Answers": ["hidden-standard-answer"]} for i in range(1, 19)],
                "Answer": "hidden-standard-answer"}},
        ]

    @staticmethod
    def response(payload, status=200):
        return SimpleNamespace(status_code=status, json=lambda: deepcopy(payload))

    def get(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        path = urlsplit(url).path
        if path.endswith("/courses/list"):
            data = {"list": [{"name": "合成班", "classroom_id": 123, "course": {"name": "合成课程"}}]}
        elif "/logs/learn/" in path:
            data = {"activities": [{"type": 4, "courseware_id": 9001, "status": self.status,
                                     "title": self.title, "deadline": 4102444800}, *self.other_activities]}
        elif path.endswith("/quiz/quiz_info"):
            data = {"id": 9001, "title": self.title, "problem_count": 3, "limit": 0,
                    "deadline": 4102444800, "total_score": 20, "university_id": 7}
        elif path.endswith("/quiz/user_quiz_status"):
            data = {"status": self.status}
        elif path.endswith("/userinfo"):
            data = [{"user_id": self.user_id, "name": "private-student-name"}]
        elif path.endswith("/quiz/shapes"):
            data = {"content": {"Slides": self.slides}}
        elif path.endswith("/quiz/get_result_cache"):
            data = self.cache
        elif path.endswith("/quiz/quiz_result"):
            data = {"quiz_visible": self.visible, "Title": self.title, "score": 20, "total_score": 20,
                    "problem_count": 3, "incorrect_list": [], "unfinished_list": [],
                    "Slides": [{**slide, "score": slide["Problem"]["Score"]} for slide in self.slides],
                    **self.grade_overrides}
        else:
            raise AssertionError(f"Unexpected external GET: {path}")
        return self.response({"errcode": 0, "data": data})

    def post(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        path = urlsplit(url).path
        self.posts.append((path, deepcopy(kwargs.get("json"))))
        if path.endswith("/quiz/start_quiz"):
            self.status = 1
            return self.response({"errcode": 0, "data": {}})
        if path.endswith("/quiz/cache_each_result"):
            for result in kwargs["json"]["results"]:
                self.cache[str(result["problemID"])] = deepcopy(result["result"])
            return self.response({"errcode": 0, "data": {}})
        if path.endswith("/quiz/entire_quiz_submit"):
            self.status = 2
            return self.response({"errcode": 0, "data": {}})
        raise AssertionError(f"Unexpected external POST: {path}")


class Storage:
    def __init__(self):
        self.data = {}
        self.service = HardenedHenuPluginService(ROOT)

    async def get_plugin_storage_keys(self): return list(self.data)
    async def get_plugin_storage(self, key): return self.data.get(key, b"{}")
    async def set_plugin_storage(self, key, value): self.data[key] = value


@pytest.fixture
def workflow(monkeypatch):
    import requests
    server = QuizServer()
    monkeypatch.setattr(requests, "get", server.get)
    monkeypatch.setattr(requests, "post", server.post)
    monkeypatch.setattr(bridge_client, "fetch_status", lambda *args, **kwargs: deepcopy(server.bridge_status))
    storage = Storage()
    tool = HenuCliSafe()
    tool.plugin = storage
    session = Session(sender_id="quiz-user", launcher_id="quiz-user", launcher_type=LauncherTypes.PERSON)
    return server, storage, tool, session


def test_question_preserves_complete_machine_content_without_standard_answers(workflow):
    async def run():
        server, _, tool, session = workflow
        result = await tool.call({"command": f"yuketang quiz question {TARGET} --index 3"}, session, 1)
        assert result["success"], result
        assert result["question"]["problem_id"] == "603"
        assert result["question"]["blank_numbers"] == list(range(1, 19))
        assert result["question"]["texts"] == ["完整题面" * 800]
        assert len(result["quiz"]["paper_revision"]) == 64
        assert all(value not in json.dumps(result) for value in (
            "hidden-standard-answer", "private-cookie", "private-student-name"))
        assert not server.posts
    asyncio.run(run())


async def confirm(storage, tool, session, query_id):
    pending = json.loads(storage.data["user:quiz-user:pending_operation"])
    return await tool.call({"command": "confirm " + pending["token"]}, session, query_id)


def test_start_needs_its_own_confirmation_and_does_not_submit(workflow):
    async def run():
        server, storage, tool, session = workflow
        server.status = 0
        preview = await tool.call({"command": f"yuketang quiz start {TARGET}"}, session, 1)
        assert preview.get("confirmation_required"), preview
        assert "合成测验" in preview["reply_text"] and "不交卷" in preview["reply_text"]
        assert not server.posts
        result = await confirm(storage, tool, session, 2)
        assert result["success"] and result["quiz"]["status"] == 1, result
        assert [path for path, _ in server.posts] == ["/v2/api/web/quiz/start_quiz"]
    asyncio.run(run())


def save_command(revision, answers=ANSWERS):
    return f"yuketang quiz save {TARGET} --revision {revision} --answers-json " + shlex.quote(json.dumps(answers))


def test_saving_answers_only_updates_official_cache_and_verifies_without_submitting(workflow):
    async def run():
        server, storage, tool, session = workflow
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        preview = await tool.call({"command": save_command(shown["quiz"]["paper_revision"])}, session, 2)
        assert preview.get("confirmation_required"), preview
        assert not server.posts
        saved = await confirm(storage, tool, session, 3)
        assert saved["success"], saved
        assert saved["quiz"]["saved_count"] == 3 and saved["quiz"]["status"] == 1
        assert saved["submission_state"] == "not_submitted"
        assert "未交卷" in saved["reply_text"]
        assert [path for path, _ in server.posts] == ["/v2/api/web/quiz/cache_each_result"]
        assert server.cache == ANSWERS
    asyncio.run(run())


def test_submit_requires_a_fresh_confirmation_and_uses_only_official_saved_answers(workflow):
    async def run():
        server, storage, tool, session = workflow
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        await tool.call({"command": save_command(shown["quiz"]["paper_revision"])}, session, 2)
        save_token = json.loads(storage.data["user:quiz-user:pending_operation"])["token"]
        await confirm(storage, tool, session, 3)
        preview = await tool.call({"command": f"yuketang quiz submit {TARGET}"}, session, 4)
        assert preview.get("confirmation_required"), preview
        assert "正式交卷" in preview["reply_text"] and "3/3" in preview["reply_text"]
        submit_token = json.loads(storage.data["user:quiz-user:pending_operation"])["token"]
        assert submit_token != save_token
        replay = await tool.call({"command": "confirm " + save_token}, session, 5)
        assert not replay["success"] and len(server.posts) == 1
        submitted = await confirm(storage, tool, session, 6)
        assert submitted["success"] and submitted["submission_state"] == "submitted", submitted
        assert submitted["quiz"]["status"] == 2 and server.status == 2
        assert submitted["quiz"]["status_label"] == "已交卷"
        payload = server.posts[-1][1]
        assert payload["user_id"] == 41
        assert [entry["result"] for entry in payload["results"][:2]] == ["A", "AC"]
        assert payload["results"][2]["result"] == ANSWERS["603"]
        again = await tool.call({"command": f"yuketang quiz submit {TARGET}"}, session, 7)
        assert again["success"] and not again.get("confirmation_required")
        assert len(server.posts) == 2
    asyncio.run(run())


@pytest.mark.parametrize("visible", [True, False])
def test_result_uses_only_the_current_students_published_grade(workflow, visible):
    async def run():
        server, _, tool, session = workflow
        server.status = 2
        server.visible = visible
        result = await tool.call({"command": f"yuketang quiz result {TARGET}"}, session, 1)
        assert result["success"], result
        assert result["grade"]["visible"] is visible
        if visible:
            assert result["grade"]["score"] == result["grade"]["total_score"] == 20
            assert result["grade"]["incorrect_numbers"] == []
            assert len(result["grade"]["questions"]) == 3
        else:
            assert "score" not in result["grade"] and "questions" not in result["grade"]
            assert "未公布" in result["reply_text"]
        assert not server.posts
    asyncio.run(run())


def test_confirmation_cannot_move_to_another_conversation(workflow):
    async def run():
        server, storage, tool, session = workflow
        server.status = 0
        await tool.call({"command": f"yuketang quiz start {TARGET}"}, session, 1)
        other = Session(sender_id="quiz-user", launcher_id="another-private-chat", launcher_type=LauncherTypes.PERSON)
        result = await confirm(storage, tool, other, 2)
        assert not result["success"] and not server.posts
    asyncio.run(run())


def test_duplicate_blank_numbers_are_rejected_before_returning_a_savable_paper(workflow):
    async def run():
        server, _, tool, session = workflow
        server.slides[2]["Problem"]["Blanks"][1]["Num"] = 1
        result = await tool.call({"command": f"yuketang quiz question {TARGET} --index 3"}, session, 1)
        assert not result["success"] and "question" not in result and not server.posts
    asyncio.run(run())


def test_submit_stops_if_saved_answers_or_authenticated_account_changed(workflow):
    async def run():
        server, storage, tool, session = workflow
        server.cache = deepcopy(ANSWERS)
        await tool.call({"command": f"yuketang quiz submit {TARGET}"}, session, 1)
        server.cache["601"] = ["B"]
        changed = await confirm(storage, tool, session, 2)
        assert not changed["success"] and changed["error_code"] == "quiz_revision_changed"
        server.cache = deepcopy(ANSWERS)
        server.user_id = 99
        changed_account = await confirm(storage, tool, session, 3)
        assert not changed_account["success"] and not server.posts
    asyncio.run(run())


def test_uncertain_submit_is_durably_fenced_until_official_submission_is_observed(workflow, monkeypatch):
    async def run():
        import requests
        server, storage, tool, session = workflow
        server.cache = deepcopy(ANSWERS)
        await tool.call({"command": f"yuketang quiz submit {TARGET}"}, session, 1)
        old_token = json.loads(storage.data["user:quiz-user:pending_operation"])["token"]
        def uncertain(url, **kwargs):
            server.posts.append((urlsplit(url).path, kwargs["json"]))
            raise requests.Timeout("private-cookie must never escape")
        monkeypatch.setattr(requests, "post", uncertain)
        result = await confirm(storage, tool, session, 2)
        assert not result["success"] and result["retry_safe"] is False
        assert "private-cookie" not in str(result)
        replay = await tool.call({"command": "confirm " + old_token}, session, 3)
        assert not replay["success"]
        restarted_storage = Storage()
        restarted_storage.data = deepcopy(storage.data)
        restarted_tool = HenuCliSafe()
        restarted_tool.plugin = restarted_storage
        blocked = await restarted_tool.call({"command": f"yuketang quiz submit {TARGET}"}, session, 4)
        assert blocked["error_code"] == "quiz_write_pending" and len(server.posts) == 1
        server.status = 2
        reconciled = await restarted_tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 5)
        assert reconciled["success"] and not reconciled.get("write_pending")
        done = await restarted_tool.call({"command": f"yuketang quiz submit {TARGET}"}, session, 6)
        assert done["success"] and len(server.posts) == 1
    asyncio.run(run())


def test_uncertain_cache_write_is_reconciled_without_another_post(workflow, monkeypatch):
    async def run():
        import requests
        server, storage, tool, session = workflow
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        command = save_command(shown["quiz"]["paper_revision"])
        await tool.call({"command": command}, session, 2)
        def uncertain(url, **kwargs):
            server.post(url, **kwargs)
            raise requests.Timeout("synthetic uncertain response")
        monkeypatch.setattr(requests, "post", uncertain)
        result = await confirm(storage, tool, session, 3)
        assert not result["success"]
        readback = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 4)
        assert readback["success"] and readback["quiz"]["saved_count"] == 3
        same = await tool.call({"command": command}, session, 5)
        assert same["success"] and not same.get("confirmation_required") and len(server.posts) == 1
    asyncio.run(run())


@pytest.mark.parametrize("answers", [
    {"601": ["Z"]}, {"601": ["A", "B"]}, {"602": ["A", "A"]},
    {"999": ["A"]}, {"603": {"1": "incomplete"}}, {},
])
def test_invalid_answers_never_create_confirmation_or_write(workflow, answers):
    async def run():
        server, _, tool, session = workflow
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        result = await tool.call({"command": save_command(shown["quiz"]["paper_revision"], answers)}, session, 2)
        assert not result["success"] and not result.get("confirmation_required") and not server.posts
    asyncio.run(run())


def test_existing_different_answers_are_preserved(workflow):
    async def run():
        server, _, tool, session = workflow
        server.cache = {"601": ["B"]}
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        result = await tool.call({"command": save_command(shown["quiz"]["paper_revision"])}, session, 2)
        assert result["error_code"] == "quiz_answer_conflict" and not server.posts
        assert server.cache == {"601": ["B"]}
    asyncio.run(run())


def test_journal_failure_prevents_any_post(workflow, monkeypatch):
    async def run():
        server, storage, tool, session = workflow
        server.status = 0
        await tool.call({"command": f"yuketang quiz start {TARGET}"}, session, 1)
        setter = storage.set_plugin_storage
        async def fail_journal(key, value):
            if key.endswith(":quiz_write_journal"):
                raise OSError("synthetic storage unavailable")
            await setter(key, value)
        monkeypatch.setattr(storage, "set_plugin_storage", fail_journal)
        result = await confirm(storage, tool, session, 2)
        assert result["error_code"] == "quiz_journal_unavailable" and not server.posts
    asyncio.run(run())


def test_save_preview_does_not_claim_unwritten_answers_are_saved(workflow):
    async def run():
        server, _, tool, session = workflow
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        command = save_command(shown["quiz"]["paper_revision"]).replace("quiz save", "quiz preview --operation save", 1)
        result = await tool.call({"command": command}, session, 2)
        assert result["success"] and result["quiz"]["saved_count"] == 0
        assert "已保存" not in result["reply_text"] and not server.posts
    asyncio.run(run())


def test_failed_bridge_stop_verification_prevents_writing(workflow, monkeypatch):
    async def run():
        server, storage, tool, session = workflow
        server.status = 0
        await tool.call({"command": f"yuketang quiz start {TARGET}"}, session, 1)
        calls = 0
        def status(*args, **kwargs):
            nonlocal calls
            calls += 1
            return {**deepcopy(server.bridge_status), "ok": calls < 3}
        monkeypatch.setattr(bridge_client, "fetch_status", status)
        result = await confirm(storage, tool, session, 2)
        assert not result["success"] and not server.posts
    asyncio.run(run())


def test_oversized_published_answer_is_rejected_instead_of_flooding_model(workflow):
    async def run():
        server, _, tool, session = workflow
        server.status = 2
        server.slides[0]["Problem"]["Answer"] = "oversized" * 10000
        result = await tool.call({"command": f"yuketang quiz result {TARGET} --index 1"}, session, 1)
        assert not result["success"] and "review" not in result and not server.posts
    asyncio.run(run())


def test_changed_question_requires_new_confirmation(workflow):
    async def run():
        server, storage, tool, session = workflow
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        await tool.call({"command": save_command(shown["quiz"]["paper_revision"])}, session, 2)
        server.slides[0]["Shapes"][0]["Text"] = "官方变更后的题面"
        result = await confirm(storage, tool, session, 3)
        assert not result["success"] and not server.posts
    asyncio.run(run())


def test_cache_success_receipt_requires_matching_readback(workflow, monkeypatch):
    async def run():
        import requests
        server, storage, tool, session = workflow
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        command = save_command(shown["quiz"]["paper_revision"])
        await tool.call({"command": command}, session, 2)
        def acknowledged_without_saving(url, **kwargs):
            server.posts.append((urlsplit(url).path, kwargs["json"]))
            return server.response({"errcode": 0, "data": {}})
        monkeypatch.setattr(requests, "post", acknowledged_without_saving)
        result = await confirm(storage, tool, session, 3)
        assert not result["success"] and result["external_committed"] and not result["retry_safe"]
        blocked = await tool.call({"command": command}, session, 4)
        assert blocked["error_code"] == "quiz_write_pending" and len(server.posts) == 1
    asyncio.run(run())


def test_result_reports_wrong_question_and_pending_grading(workflow):
    async def run():
        server, _, tool, session = workflow
        server.status = 2
        slides = [{**deepcopy(slide), "score": slide["Problem"]["Score"]} for slide in server.slides]
        slides[0].update(score=0, result="B")
        slides[0]["Problem"]["Answer"] = "A"
        server.grade_overrides = {"score": 19, "incorrect_list": [601], "Slides": slides}
        result = await tool.call({"command": f"yuketang quiz result {TARGET} --index 1"}, session, 1)
        assert result["success"] and result["grade"]["incorrect_numbers"] == [1]
        assert result["grade"]["score"] == 19 and result["review"] == {
            "submitted_answer": "B", "correct_answer": "A", "correct_blanks": None}
        slides[0]["score"] = -1
        server.grade_overrides["score"] = -1
        result = await tool.call({"command": f"yuketang quiz result {TARGET}"}, session, 2)
        assert result["success"] and result["grade"]["grading_pending"] and result["grade"]["score"] is None
        assert not server.posts
    asyncio.run(run())


@pytest.mark.parametrize("scenario", ["group", "other_user", "same_query", "expired"])
def test_quiz_confirmation_is_private_owned_fresh_and_requires_a_new_message(workflow, scenario):
    async def run():
        server, storage, tool, session = workflow
        server.status = 0
        await tool.call({"command": f"yuketang quiz start {TARGET}"}, session, 1)
        pending_key = "user:quiz-user:pending_operation"
        pending = json.loads(storage.data[pending_key])
        token = pending["token"]
        if scenario == "group":
            session = Session(sender_id="quiz-user", launcher_id="group-123", launcher_type=LauncherTypes.GROUP)
        elif scenario == "other_user":
            session = Session(sender_id="other-user", launcher_id="other-user", launcher_type=LauncherTypes.PERSON)
        elif scenario == "expired":
            pending["expires_at"] = 1
            storage.data[pending_key] = json.dumps(pending).encode()
        result = await tool.call({"command": "confirm " + token}, session, 1 if scenario == "same_query" else 2)
        assert not result["success"] and not server.posts
    asyncio.run(run())


def test_simultaneous_confirmations_consume_the_token_once(workflow, monkeypatch):
    async def run():
        server, storage, tool, session = workflow
        server.status = 0
        await tool.call({"command": f"yuketang quiz start {TARGET}"}, session, 1)
        token = json.loads(storage.data["user:quiz-user:pending_operation"])["token"]
        getter = storage.get_plugin_storage
        async def scheduled_get(key):
            value = await getter(key)
            await asyncio.sleep(0)
            return value
        monkeypatch.setattr(storage, "get_plugin_storage", scheduled_get)
        results = await asyncio.gather(*[
            tool.call({"command": "confirm " + token}, session, query) for query in (2, 3)])
        assert sum(result["success"] for result in results) == 1
        assert len(server.posts) == 1
    asyncio.run(run())


def test_quiz_flow_never_reads_another_exams_cover(workflow):
    async def run():
        server, storage, tool, session = workflow
        server.other_activities = [{"type": 5, "courseware_id": 9002, "status": 0, "title": "另一场考试"}]
        shown = await tool.call({"command": f"yuketang quiz show {TARGET}"}, session, 1)
        assert shown["success"], shown
        server.cache = deepcopy(ANSWERS)
        preview = await tool.call({"command": f"yuketang quiz submit {TARGET}"}, session, 2)
        assert preview.get("confirmation_required"), preview
        submitted = await confirm(storage, tool, session, 3)
        assert submitted["success"] and server.status == 2, submitted
    asyncio.run(run())


@pytest.mark.parametrize("visibility", ["0", "false", 1.0, None, {}])
def test_ambiguous_grade_visibility_never_exposes_answers(workflow, visibility):
    async def run():
        server, _, tool, session = workflow
        server.status = 2
        server.visible = visibility
        result = await tool.call({"command": f"yuketang quiz result {TARGET} --index 1"}, session, 1)
        assert "review" not in result and "hidden-standard-answer" not in str(result)
        assert not result.get("grade", {}).get("visible")
    asyncio.run(run())


@pytest.mark.parametrize("problem", ["unwired", "old", "missing_age", "bool_age", "invalid_ids", "config_changed"])
def test_direct_write_requires_a_fresh_verified_listener_stop(workflow, problem):
    async def run():
        server, storage, tool, session = workflow
        server.status = 0
        await tool.call({"command": f"yuketang quiz start {TARGET}"}, session, 1)
        if problem == "unwired":
            server.bridge_status["daemon_wired"] = False
        elif problem == "old":
            server.bridge_status["daemon"]["age_s"] = 3600
        elif problem == "missing_age":
            server.bridge_status["daemon"].pop("age_s")
        elif problem == "bool_age":
            server.bridge_status["daemon"]["age_s"] = True
        elif problem == "invalid_ids":
            server.bridge_status["daemon"]["openids"] = {}
        else:
            server.bridge_status["config_hash"] = "changed"
        result = await confirm(storage, tool, session, 2)
        assert not result["success"] and not server.posts
    asyncio.run(run())


@pytest.mark.parametrize("flag", ["--answers-json", "--answers_json"])
def test_malformed_answer_quoting_is_still_redacted(workflow, capsys, flag):
    async def run():
        server, _, tool, session = workflow
        command = f"yuketang quiz save {TARGET} --revision {'a' * 64} {flag} '{{\"601\":[\"private-answer\"]}}"
        result = await tool.call({"command": command}, session, 1)
        assert not result["success"] and not server.posts
        assert "private-answer" not in str(result)
    asyncio.run(run())
    assert "private-answer" not in capsys.readouterr().out
