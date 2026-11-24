"""Public corpus/ingestion boundaries; no real campus or LangBot writes."""

import copy
import hashlib
import json
import gzip
import base64

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ops.henusite_sync import build_documents, sync_documents, load_library, connect_henu_bot


@pytest.fixture
def reviewed_source():
    record = {
        "id": "K02ff313dd1", "campus": "jm", "month": "2026-01",
        "state": "history", "answerAvailable": True,
        "evidence": [
            {"id": "K02ff313dd1-E1", "text": "金明图书馆七楼位置有充电口吗",
             "role": "历史提问节选", "month": "2026-01", "processed": True},
            {"id": "K02ff313dd1-E4", "text": "对，靠窗靠墙（几乎每个都有没见过没有的）",
             "role": "历史回复节选", "month": "2026-01", "processed": True},
        ],
    }
    # Review approval is an independent source snapshot, not a digest of rendered output.
    payload = {k: record[k] for k in ("id", "campus", "month", "evidence")}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    selection = {"schema_version": 1, "records": [{
        "id": record["id"], "evidence_ids": [e["id"] for e in record["evidence"]],
        "reviewed_sha256": digest,
    }]}
    return record, selection


def test_only_reviewed_question_and_answer_keep_their_historical_provenance(reviewed_source):
    record, selection = reviewed_source
    documents, report = build_documents([record], selection)
    text = documents[record["id"]].decode()
    assert "来源：https://henu.site/" in text
    assert "来源月份：2026-01" in text and "校区：金明" in text
    assert "未经现行政策核验" in text and "以学校最新公告为准" in text
    assert "靠窗靠墙" in text
    assert report["accepted"] == 1


class LangBotService:
    """External service double: observe results through its public list interface."""
    def __init__(self):
        self.files = []
        self.uploads = {}
        self.lose_attach_response = False

    def get_base(self, kb):
        return {"uuid": kb, "description": "henusite-managed-v1"}

    def list_files(self, kb):
        return copy.deepcopy(self.files)

    def upload_document(self, name, content):
        fid = "storage/" + hashlib.sha256(content).hexdigest() + ".md"
        self.uploads[fid] = content
        return fid

    def attach_document(self, kb, fid):
        self.files.append({"uuid": str(len(self.files) + 1), "file_name": fid,
                           "status": "completed"})
        if self.lose_attach_response:
            self.lose_attach_response = False
            raise TimeoutError("response lost after accepted")

    def delete_document(self, kb, uuid):
        self.files = [f for f in self.files if f["uuid"] != uuid]


def test_repeat_sync_and_lost_response_resume_without_duplicate_documents(tmp_path):
    api = LangBotService()
    api.lose_attach_response = True
    state = tmp_path / "state.json"
    docs = {"K02ff313dd1": b"reviewed document"}
    with pytest.raises(TimeoutError):
        sync_documents(api, "dedicated-kb", docs, state)
    sync_documents(api, "dedicated-kb", docs, state)
    sync_documents(api, "dedicated-kb", docs, state)
    assert len(api.list_files("dedicated-kb")) == 1


def test_edit_or_withdrawal_removes_previous_answer_and_preserves_unrelated_kb(tmp_path, reviewed_source):
    record, selection = reviewed_source
    api = LangBotService()
    state = tmp_path / "state.json"
    docs, _ = build_documents([record], selection)
    sync_documents(api, "dedicated-kb", docs, state)
    record["evidence"][1]["text"] = "回复已被修改，需要重新筛选"
    docs, report = build_documents([record], selection)
    assert report["rejected"][record["id"]] == "changed_requires_review"
    sync_documents(api, "dedicated-kb", docs, state)
    assert api.list_files("dedicated-kb") == []
    with pytest.raises(ValueError, match="protected"):
        sync_documents(api, "213328db-7276-4c26-a5d7-a278fd34634a", {}, state)


def test_withdrawal_happens_even_when_an_earlier_new_document_fails(tmp_path):
    api = LangBotService()
    state = tmp_path / "state.json"
    sync_documents(api, "dedicated-kb", {"Z-withdrawn": b"outdated answer"}, state)
    old_ids = {f["file_name"] for f in api.list_files("dedicated-kb")}
    api.lose_attach_response = True
    with pytest.raises(TimeoutError):
        sync_documents(api, "dedicated-kb", {"A-new": b"new answer"}, state)
    assert not old_ids.intersection(f["file_name"] for f in api.list_files("dedicated-kb"))


def test_public_download_rejects_off_site_shards_before_requesting_them(tmp_path):
    manifest = {"format": "henu-encrypted-library-v1", "version": 3, "count": 1,
                "key": "test", "chunks": [{"url": "https://untrusted.example/data",
                  "sha256": "0" * 64, "iv": "test", "count": 1, "bytes": 32}]}
    def fetch(url):
        if url == "https://henu.site/library/manifest.json":
            return json.dumps(manifest).encode()
        pytest.fail("An untrusted shard must never receive a request")
    with pytest.raises(ValueError, match="shard"):
        load_library(tmp_path, fetch=fetch)


def test_interrupted_cached_download_recovers_and_verifies_the_complete_corpus(tmp_path):
    key, iv = bytes(range(32)), bytes(range(12))
    records = [{"id": "public-record"}]
    raw = AESGCM(key).encrypt(iv, gzip.compress(json.dumps(records).encode()), None)
    digest = hashlib.sha256(raw).hexdigest()
    def encode(b):
        return base64.urlsafe_b64encode(b).decode().rstrip("=")
    manifest = {"format": "henu-encrypted-library-v1", "version": 3, "count": 1,
                "key": encode(key), "chunks": [{"url": f"/library/{digest}.bin",
                    "sha256": digest, "iv": encode(iv), "count": 1, "bytes": len(raw)}]}
    (tmp_path / (digest + ".bin")).write_bytes(b"interrupted")
    def fetch(url):
        return json.dumps(manifest).encode() if url.endswith("manifest.json") else raw
    recovered, _ = load_library(tmp_path, fetch=fetch)
    assert recovered == [{"id": "public-record"}]
    assert (tmp_path / (digest + ".bin")).read_bytes() == raw


def test_only_henu_bot_receives_copied_pipeline_with_old_knowledge_and_tools(tmp_path):
    original = {"uuid": "original", "name": "QQ 官方 API Pipeline", "description": "existing",
                "is_default": True, "stages": ["input", "output"],
                "extensions_preferences": {"plugins": ["campus"], "mcp_servers": ["career"]},
                "config": {"ai": {"local-agent": {"knowledge-bases": ["old-kb"],
                    "model": {"uuid": "existing-model"}, "tools": ["henu_cli"],
                    "prompt": [{"role": "system", "content": "Original instructions"}]}},
                    "trigger": {"prefix": ""}}}
    class Pipelines:
        def __init__(self):
            self.pipelines = {"original": copy.deepcopy(original)}
            self.bots = {"henu": {"use_pipeline_uuid": "original"},
                         "other": {"use_pipeline_uuid": "original"}}
        def get_base(self, kb):
            return {"description": "henusite-managed-v1"}
        def get_pipeline(self, uuid):
            return copy.deepcopy(self.pipelines[uuid])
        def get_bot(self, uuid):
            return copy.deepcopy(self.bots[uuid])
        def copy_pipeline(self, uuid):
            self.pipelines["copy"] = {**copy.deepcopy(self.pipelines[uuid]), "uuid": "copy",
                                      "is_default": False}
            return "copy"
        def update_pipeline(self, uuid, data):
            self.pipelines[uuid].update(copy.deepcopy(data))
        def update_bot(self, uuid, data):
            self.bots[uuid].update(copy.deepcopy(data))
    api = Pipelines()
    ledger = tmp_path / "binding.json"
    connect_henu_bot(api, "new-kb", ledger, bot_uuid="henu", original_uuid="original")
    assert api.get_pipeline("original") == original
    assert api.get_bot("other")["use_pipeline_uuid"] == "original"
    assert api.get_bot("henu")["use_pipeline_uuid"] == "copy"
    cloned = api.get_pipeline("copy")
    assert cloned["extensions_preferences"] == original["extensions_preferences"]
    assert cloned["stages"] == original["stages"]
    agent = cloned["config"]["ai"]["local-agent"]
    assert agent["knowledge-bases"] == ["old-kb", "new-kb"]
    assert agent["model"] == {"uuid": "existing-model"} and agent["tools"] == ["henu_cli"]
    assert agent["prompt"][0] == original["config"]["ai"]["local-agent"]["prompt"][0]
    connect_henu_bot(api, "new-kb", ledger, bot_uuid="henu", original_uuid="original")
    assert len(api.pipelines) == 2
