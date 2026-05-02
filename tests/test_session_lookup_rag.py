"""Tests for session_lookup three-way routing: short-circuit / RAG / ignore."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry
from agent.nodes.session_lookup import make_session_lookup


def _entry(query, **md):
    """Build a query_session entry whose original_query drives matching."""
    return KnowledgeEntry(
        id=md.pop("id", f"e_{query[:6]}_{time.time()}"),
        source="query_session",
        category="query_session",
        content=query,
        metadata={
            "original_query": query,
            "enriched_query": "",
            "description": md.pop("description", query),
            "key_concepts": md.pop("key_concepts", []),
            "tables_used": md.pop("tables_used", ["KYC.CUSTOMERS"]),
            "accepted_candidates": [{
                "interpretation": "x",
                "sql": md.pop("sql", "SELECT * FROM KYC.CUSTOMERS"),
                "explanation": md.pop("explanation", ""),
            }],
            "created_at": time.time(),
            **md,
        },
    )


def _graph():
    g = MagicMock()
    g.get_node = lambda label, fqn: {"fqn": fqn}    # all tables exist
    return g


def _state(query):
    return {"user_input": query, "intent": "DATA_QUERY",
            "conversation_history": [], "_trace": []}


def _store():
    return KYCKnowledgeStore(persist_path=f"/tmp/sl_{time.time()}.json")


def test_short_circuits_at_high_similarity():
    """Score >= 0.75 → has_candidates=True, session_match_entry_id set."""
    s = _store()
    s.add_session_entry(_entry("active customers by region today"))
    node = make_session_lookup(s, _graph())
    out = node(_state("active customers by region today"))
    assert out.get("has_candidates") is True
    assert out.get("session_match_entry_id") is not None


def test_rag_injects_examples_at_moderate_similarity():
    """0.30 <= score < 0.75 → no short-circuit, accepted_examples populated."""
    s = _store()
    s.add_session_entry(_entry(
        "count customers per region",
        description="regional customer counts",
    ))
    node = make_session_lookup(s, _graph())
    # Overlaps on description tokens only ({regional, customer, counts}, no
    # match on original_query tokens) → enriched score in band, legacy score 0.
    out = node(_state("regional customer counts grouped"))
    assert not out.get("has_candidates")
    examples = out.get("accepted_examples", [])
    assert len(examples) >= 1
    assert all("sql" in ex for ex in examples)
    assert all(0.30 <= ex["score"] < 0.75 for ex in examples)


def test_ignores_below_threshold():
    """Score < 0.30 → both has_candidates and accepted_examples are empty."""
    s = _store()
    s.add_session_entry(_entry("transactions and amounts only"))
    node = make_session_lookup(s, _graph())
    out = node(_state("active customer onboarding workflow"))
    assert not out.get("has_candidates")
    assert out.get("accepted_examples", []) == []


def test_skip_for_followup_intent():
    """RESULT_FOLLOWUP intent must skip session lookup entirely."""
    s = _store()
    s.add_session_entry(_entry("active customers by region"))
    node = make_session_lookup(s, _graph())
    state = _state("active customers by region")
    state["intent"] = "RESULT_FOLLOWUP"
    out = node(state)
    assert not out.get("has_candidates")
    assert out.get("accepted_examples", []) == []


def test_skip_when_history_present():
    """Mid-thread queries must skip session lookup."""
    s = _store()
    s.add_session_entry(_entry("active customers by region"))
    node = make_session_lookup(s, _graph())
    state = _state("active customers by region")
    state["conversation_history"] = [{"role": "user", "content": "earlier"}]
    out = node(state)
    assert not out.get("has_candidates")
    assert out.get("accepted_examples", []) == []
