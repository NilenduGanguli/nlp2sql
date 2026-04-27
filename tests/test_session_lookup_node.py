"""Unit tests for the session_lookup node."""
from unittest.mock import MagicMock
from agent.knowledge_store import KnowledgeEntry
from agent.nodes.session_lookup import make_session_lookup


def _entry():
    return KnowledgeEntry(
        id="e1",
        source="query_session",
        category="query_session",
        content="...",
        metadata={
            "original_query": "high risk customers",
            "enriched_query": "high risk customers",
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [
                {"interpretation": "primary", "sql": "SELECT 1", "explanation": "x"}
            ],
            "created_at": 1000.0,
        },
    )


def test_match_short_circuits():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=_entry())
    graph = MagicMock()

    node = make_session_lookup(store, graph)
    state = {"user_input": "high risk customers", "enriched_query": "high risk customers",
             "intent": "DATA_QUERY", "conversation_history": [], "_trace": []}
    out = node(state)

    assert out["has_candidates"] is True
    assert len(out["sql_candidates"]) == 1
    assert out["sql_candidates"][0]["sql"] == "SELECT 1"
    assert out["session_match_entry_id"] == "e1"


def test_no_match_passes_through():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=None)
    graph = MagicMock()
    node = make_session_lookup(store, graph)

    state = {"user_input": "novel question", "enriched_query": "novel question",
             "intent": "DATA_QUERY", "conversation_history": [], "_trace": []}
    out = node(state)

    assert not out.get("has_candidates")
    assert out.get("session_match_entry_id") is None


def test_skipped_for_followup_intent():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=_entry())
    graph = MagicMock()
    node = make_session_lookup(store, graph)
    state = {"user_input": "more rows", "enriched_query": "more rows",
            "intent": "RESULT_FOLLOWUP", "conversation_history": [], "_trace": []}
    out = node(state)
    store.find_session_match.assert_not_called()
    assert not out.get("has_candidates")


def test_skipped_for_mid_thread():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=_entry())
    graph = MagicMock()
    node = make_session_lookup(store, graph)
    state = {"user_input": "x", "enriched_query": "x", "intent": "DATA_QUERY",
            "conversation_history": [{"role": "user", "content": "earlier"}], "_trace": []}
    out = node(state)
    store.find_session_match.assert_not_called()
    assert not out.get("has_candidates")


def test_disabled_via_none_store():
    node = make_session_lookup(None, None)
    state = {"user_input": "x", "enriched_query": "x", "intent": "DATA_QUERY",
             "conversation_history": [], "_trace": []}
    out = node(state)
    assert not out.get("has_candidates")
