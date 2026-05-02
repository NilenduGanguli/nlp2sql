"""Tests for the /api/teach/{analyze,save} endpoints (Phase 2)."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app_config import AppConfig
from backend.main import app
from agent.knowledge_store import KYCKnowledgeStore


class _FakeResp:
    def __init__(self, content):
        self.content = content


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_CACHE_PATH", str(tmp_path))
    app.state.config = AppConfig()

    # Fresh knowledge store per test, persisted under tmp_path so tests don't leak.
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    app.state.knowledge_store = store
    return TestClient(app)


def test_analyze_returns_empty_when_no_llm(client):
    """No app.state.llm → returns a valid (empty) TeachAnalysis, no 500."""
    if hasattr(app.state, "llm"):
        delattr(app.state, "llm")
    r = client.post("/api/teach/analyze", json={
        "user_input": "active customers",
        "expected_sql": "SELECT * FROM KYC.CUSTOMERS WHERE STATUS='A'",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == ""
    assert body["description"] == ""
    assert body["key_concepts"] == []
    assert body["anticipated_clarifications"] == []


def test_analyze_returns_llm_output(client):
    """LLM produces structured analysis → endpoint returns it verbatim."""
    fake_payload = {
        "title": "Active customers",
        "content": "Counts customers with STATUS='A'.",
        "description": "Customers currently active.",
        "why_this_sql": "Filter STATUS='A' on CUSTOMERS.",
        "key_concepts": ["active customer"],
        "tags": ["customer", "status-filter"],
        "anticipated_clarifications": [
            {"question": "What is 'active'?", "answer": "STATUS='A'"},
        ],
        "key_filter_values": {"STATUS": ["A"]},
    }
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=_FakeResp(json.dumps(fake_payload)))
    app.state.llm = fake_llm

    r = client.post("/api/teach/analyze", json={
        "user_input": "active customers",
        "expected_sql": "SELECT * FROM KYC.CUSTOMERS WHERE STATUS='A'",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Active customers"
    assert body["description"] == "Customers currently active."
    assert "active customer" in body["key_concepts"]
    assert body["key_filter_values"] == {"STATUS": ["A"]}
    assert body["anticipated_clarifications"][0]["answer"] == "STATUS='A'"


def test_save_persists_session_entry_patterns_and_siblings(client):
    payload = {
        "user_input": "active customers",
        "expected_sql": "SELECT * FROM KYC.CUSTOMERS WHERE STATUS='A'",
        "tables_used": ["KYC.CUSTOMERS"],
        "explanation": "stub",
        "curator_notes": "Use this for Q1 reports only.",
        "analysis": {
            "title": "Active customers",
            "description": "Customers currently active.",
            "why_this_sql": "Filter STATUS='A'.",
            "key_concepts": ["active customer"],
            "tags": ["customer", "status-filter"],
            "anticipated_clarifications": [
                {"question": "What is 'active'?", "answer": "STATUS='A'"},
                {"question": "Time range?", "answer": "current snapshot"},
            ],
            "key_filter_values": {"STATUS": ["A"]},
        },
        "siblings": [
            {"content": "ACTIVE = currently transacting customer", "category": "glossary"},
        ],
    }
    r = client.post("/api/teach/save", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "saved"
    assert body["session_entry_id"].startswith("teach_")
    assert len(body["learned_pattern_ids"]) == 2
    assert len(body["sibling_entry_ids"]) == 1

    store: KYCKnowledgeStore = app.state.knowledge_store
    saved = next(
        (e for e in store.static_entries if e.id == body["session_entry_id"]),
        None,
    )
    assert saved is not None
    md = saved.metadata
    assert md["original_query"] == payload["user_input"]
    assert md["accepted_candidates"][0]["sql"] == payload["expected_sql"]
    assert md["description"].startswith("Customers currently active.")
    assert "Curator notes: Use this for Q1 reports only." in md["description"]
    assert md["key_filter_values"] == {"STATUS": ["A"]}
    assert md["source_workflow"] == "teach"

    learned_ids = {p.id for p in store.learned_patterns}
    assert all(pid in learned_ids for pid in body["learned_pattern_ids"])

    sibling = next(
        (e for e in store.static_entries if e.id in body["sibling_entry_ids"]),
        None,
    )
    assert sibling is not None
    assert sibling.category == "glossary"
    assert "ACTIVE" in sibling.content


def test_save_with_no_clarifications_or_siblings_still_works(client):
    payload = {
        "user_input": "x",
        "expected_sql": "SELECT 1 FROM DUAL",
        "tables_used": [],
        "analysis": {
            "title": "minimal",
            "description": "",
            "why_this_sql": "",
            "key_concepts": [],
            "tags": [],
            "anticipated_clarifications": [],
            "key_filter_values": {},
        },
        "siblings": [],
    }
    r = client.post("/api/teach/save", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "saved"
    assert body["learned_pattern_ids"] == []
    assert body["sibling_entry_ids"] == []
