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
    store.find_verified_pattern = MagicMock(return_value=None)
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
    store.find_verified_pattern = MagicMock(return_value=None)
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
    store.find_verified_pattern = MagicMock(return_value=None)
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
    store.find_verified_pattern = MagicMock(return_value=None)
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


def test_verified_pattern_takes_precedence_over_raw_session(tmp_path):
    from agent.knowledge_store import KYCKnowledgeStore, VerifiedPattern, KnowledgeEntry
    from knowledge_graph.graph_store import KnowledgeGraph

    g = KnowledgeGraph()
    g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))

    store.add_session_entry(KnowledgeEntry(
        id="raw1", source="query_session", category="query_session",
        content="x",
        metadata={
            "original_query": "show me high risk customers",
            "enriched_query": "show me high risk customers",
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [{"interpretation": "raw", "sql": "SELECT * FROM KYC.CUSTOMERS",
                                     "explanation": ""}],
            "rejected_candidates": [], "clarifications": [], "created_at": 1.0,
        },
    ))

    store.add_pattern(VerifiedPattern(
        pattern_id="vp_x",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show me high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS WHERE risk='HIGH'",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=5, consumer_uses=10, negative_signals=0,
        score=15.0, promoted_at=1.0, source_entry_ids=["raw1"], manual_promotion=False,
    ))

    node = make_session_lookup(store, g)
    out = node({
        "user_input": "show me high risk customers please",
        "enriched_query": "show me high risk customers please",
        "intent": "DATA_QUERY", "conversation_history": [], "_trace": [],
    })

    assert out.get("has_candidates") is True
    summary = out["_trace"][-1]["output_summary"]
    assert summary["action"] == "match"
    assert summary.get("match_kind") == "verified_pattern"
    assert "WHERE risk" in out["sql_candidates"][0]["sql"]
