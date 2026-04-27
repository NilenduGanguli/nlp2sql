"""End-to-end: seed a query_session entry, run a similar query through
session_lookup, assert the pipeline short-circuits with the saved candidates.

Uses real KnowledgeGraph + KYCKnowledgeStore. Does NOT require Oracle, an LLM,
or the FastAPI app — exercises the data path the SSE route relies on.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry
from agent.nodes.session_lookup import make_session_lookup
from knowledge_graph.graph_store import KnowledgeGraph


def _seed_session_entry(store: KYCKnowledgeStore) -> None:
    entry = KnowledgeEntry(
        id="seed1",
        source="query_session",
        category="query_session",
        content="seeded query session",
        metadata={
            "original_query": "show me high risk customers",
            "enriched_query": "show me high risk customers risk_rating='HIGH'",
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [
                {
                    "interpretation": "active customers only",
                    "sql": "SELECT * FROM KYC.CUSTOMERS WHERE STATUS='ACTIVE'",
                    "explanation": "filter to currently-active rows",
                },
                {
                    "interpretation": "include historical",
                    "sql": "SELECT * FROM KYC.CUSTOMERS",
                    "explanation": "no status filter",
                },
            ],
            "rejected_candidates": [],
            "clarifications": [],
            "created_at": 1000.0,
        },
    )
    store.add_session_entry(entry)


def _graph_with_customers() -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})
    return g


def _initial_state(query: str) -> Dict[str, Any]:
    return {
        "user_input": query,
        "enriched_query": query,
        "intent": "DATA_QUERY",
        "conversation_history": [],
        "_trace": [],
    }


def test_session_match_short_circuits_pipeline(tmp_path):
    """Seed a query_session entry; run a similar query through session_lookup;
    assert match metadata + candidates are returned."""
    g = _graph_with_customers()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    _seed_session_entry(store)

    node = make_session_lookup(store, g)
    out = node(_initial_state("show me high risk customers please"))

    assert out["has_candidates"] is True
    assert out["session_match_entry_id"] == "seed1"
    assert len(out["sql_candidates"]) == 2
    sqls = {c["sql"] for c in out["sql_candidates"]}
    assert any("KYC.CUSTOMERS" in s for s in sqls)
    interpretations = {c["interpretation"] for c in out["sql_candidates"]}
    assert "active customers only" in interpretations
    assert "include historical" in interpretations

    # Trace must record the match for InvestigatePage to surface it.
    trace_steps = out["_trace"]
    session_steps = [s for s in trace_steps if s.get("node") == "session_lookup"]
    assert len(session_steps) == 1
    summary = session_steps[0]["output_summary"]
    assert summary["action"] == "match"
    assert summary["matched_entry_id"] == "seed1"
    assert summary["candidate_count"] == 2


def test_no_match_passes_through_when_query_unrelated(tmp_path):
    """Unrelated query → session_lookup returns the state unchanged with a
    miss-action trace step."""
    g = _graph_with_customers()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    _seed_session_entry(store)

    node = make_session_lookup(store, g)
    out = node(_initial_state("list every employee in the finance department"))

    assert "has_candidates" not in out or not out.get("has_candidates")
    assert "session_match_entry_id" not in out
    assert "sql_candidates" not in out

    trace_steps = out["_trace"]
    session_steps = [s for s in trace_steps if s.get("node") == "session_lookup"]
    assert len(session_steps) == 1
    assert session_steps[0]["output_summary"]["action"] == "miss"


def test_no_match_when_referenced_table_missing_from_graph(tmp_path):
    """If the seeded entry references a table no longer in the graph, the
    match is rejected and the pipeline is not short-circuited."""
    g = KnowledgeGraph()
    g.merge_node("Table", "KYC.SOMETHING_ELSE", {"name": "SOMETHING_ELSE", "schema": "KYC"})
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    _seed_session_entry(store)

    node = make_session_lookup(store, g)
    out = node(_initial_state("show me high risk customers please"))

    assert not out.get("has_candidates")
    trace_steps = out["_trace"]
    session_steps = [s for s in trace_steps if s.get("node") == "session_lookup"]
    assert session_steps[0]["output_summary"]["action"] == "miss"


def test_skip_when_mid_conversation_history(tmp_path):
    """Mid-thread queries should bypass session lookup entirely."""
    g = _graph_with_customers()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    _seed_session_entry(store)

    node = make_session_lookup(store, g)
    state = _initial_state("show me high risk customers please")
    state["conversation_history"] = [{"role": "user", "content": "previous turn"}]
    out = node(state)

    assert not out.get("has_candidates")
    summary = out["_trace"][-1]["output_summary"]
    assert summary["action"] == "skip"
    assert summary["reason"] == "followup_or_mid_thread"


def test_persistence_round_trip(tmp_path):
    """Save the entry, recreate the store from disk, and confirm it still
    matches and short-circuits."""
    persist = str(tmp_path / "ks.json")
    g = _graph_with_customers()

    store_a = KYCKnowledgeStore(persist_path=persist)
    _seed_session_entry(store_a)

    store_b = KYCKnowledgeStore(persist_path=persist)
    node = make_session_lookup(store_b, g)
    out = node(_initial_state("show me high risk customers please"))

    assert out["has_candidates"] is True
    assert out["session_match_entry_id"] == "seed1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
