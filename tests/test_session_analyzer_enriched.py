"""Tests for enriched analyze_accepted_session output (Phase 1)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agent.llm_knowledge_analyzer import analyze_accepted_session


class _FakeResp:
    def __init__(self, content):
        self.content = content


def _digest():
    """Match the shape produced by agent.session_digest.build_session_digest."""
    return {
        "session_id": "test-session-1",
        "original_query": "How many active customers per region?",
        "enriched_query": "Count of customers with STATUS='A' grouped by REGION",
        "candidates": [{
            "id": "c1",
            "interpretation": "active = STATUS='A'",
            "sql": "SELECT REGION, COUNT(*) FROM KYC.CUSTOMERS WHERE STATUS='A' GROUP BY REGION",
            "explanation": "Counts customers with active status by region.",
            "accepted": True,
            "executed": True,
        }],
        "clarifications": [
            {"question": "Active means STATUS='A'?", "answer": "Yes"},
        ],
        "schema_context_tables": ["KYC.CUSTOMERS"],
        "tool_calls": [],
        "result_shape": {},
        "created_at": 1714600000.0,
    }


def test_analyze_accepted_session_produces_all_enriched_fields():
    fake_payload = {
        "title": "Active customers by region",
        "content": "Count of customers with STATUS='A' grouped by REGION.",
        "description": "Counts how many customers are currently active, broken down by region.",
        "why_this_sql": "Filters CUSTOMERS to STATUS='A' (the active code in this DB) and "
                        "groups by REGION; no joins needed because both columns live on CUSTOMERS.",
        "key_concepts": ["active customer", "regional breakdown"],
        "tags": ["customer", "status-filter", "aggregation"],
        "anticipated_clarifications": [
            {"question": "What does 'active' mean?", "answer": "STATUS='A'"},
            {"question": "How is region defined?", "answer": "CUSTOMERS.REGION column"},
        ],
        "key_filter_values": {"STATUS": ["A"]},
    }
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=_FakeResp(json.dumps(fake_payload)))

    entry = analyze_accepted_session(fake_llm, _digest())
    assert entry is not None
    md = entry.metadata
    assert md["description"].startswith("Counts how many")
    assert "STATUS='A'" in md["why_this_sql"]
    assert "active customer" in md["key_concepts"]
    assert "status-filter" in md["tags"]
    assert any(c["answer"] == "STATUS='A'" for c in md["anticipated_clarifications"])
    assert md["key_filter_values"] == {"STATUS": ["A"]}


def test_analyze_accepted_session_handles_partial_llm_output():
    """LLM omits some optional fields → entry still saved, missing fields are []/{}/''.."""
    minimal = {
        "title": "X",
        "content": "Y",
        # description, why_this_sql, etc. absent
    }
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=_FakeResp(json.dumps(minimal)))

    entry = analyze_accepted_session(fake_llm, _digest())
    assert entry is not None
    md = entry.metadata
    assert md.get("description", "") == ""
    assert md.get("key_concepts", []) == []
    assert md.get("anticipated_clarifications", []) == []
    assert md.get("key_filter_values", {}) == {}
