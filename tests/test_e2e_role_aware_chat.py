"""End-to-end: signal capture, three curator accepts → verified pattern,
consumer query auto-pins it. No HTTP server, no LLM — pure data path."""
from __future__ import annotations

from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry
from agent.signal_log import SignalLog, SignalEvent
from agent.pattern_aggregator import aggregate_patterns
from agent.nodes.session_lookup import make_session_lookup
from knowledge_graph.graph_store import KnowledgeGraph


def _g():
    g = KnowledgeGraph()
    g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})
    return g


def _accept(store, sigs, eid, query, sql):
    entry = KnowledgeEntry(
        id=eid, source="query_session", category="query_session",
        content=query,
        metadata={
            "original_query": query, "enriched_query": query,
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [{"interpretation": "x", "sql": sql, "explanation": ""}],
            "rejected_candidates": [], "clarifications": [], "created_at": 1.0,
        },
    )
    store.add_session_entry(entry)
    aggregate_patterns(store, entry, sigs, mode="curator")
    return entry


def test_three_curator_accepts_promote_pattern_consumer_query_matches(tmp_path):
    g = _g()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"

    _accept(store, sigs, "e1", "show me high risk customers",       sql)
    _accept(store, sigs, "e2", "list high-risk customers please",   sql)
    _accept(store, sigs, "e3", "high risk customers please",        sql)

    # A pattern should now exist
    assert any(p.accept_count >= 3 for p in store.patterns)

    # Now run session_lookup with a NEW similar query (consumer mode).
    # Query must clear SESSION_MATCH_THRESHOLD (0.65) Jaccard against the
    # exemplar — which is e3's "high risk customers please".
    node = make_session_lookup(store, g)
    out = node({
        "user_input": "show me high risk customers please",
        "enriched_query": "show me high risk customers please",
        "intent": "DATA_QUERY", "conversation_history": [], "_trace": [],
    })

    assert out["has_candidates"] is True
    summary = out["_trace"][-1]["output_summary"]
    assert summary["match_kind"] == "verified_pattern"
    assert out["sql_candidates"][0]["is_verified"] is True


def test_signal_log_persists_across_processes(tmp_path):
    sigs_a = SignalLog(persist_dir=str(tmp_path))
    sigs_a.append(SignalEvent(
        event="ran_unchanged", session_id="s1", entry_id="e1",
        mode="curator", sql_hash="abc", metadata={},
    ))
    sigs_b = SignalLog(persist_dir=str(tmp_path))
    loaded = sigs_b.load(event="ran_unchanged")
    assert len(loaded) == 1


def test_negative_signals_block_promotion(tmp_path):
    g = _g()  # noqa: F841 — kept for parity with the e2e fixture
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    for eid, q in [("e1", "show high risk customers"),
                   ("e2", "list high-risk customers"),
                   ("e3", "high risk customers please")]:
        e = KnowledgeEntry(
            id=eid, source="query_session", category="query_session",
            content=q,
            metadata={
                "original_query": q, "enriched_query": q,
                "tables_used": ["KYC.CUSTOMERS"],
                "accepted_candidates": [{"interpretation": "x", "sql": sql, "explanation": ""}],
                "rejected_candidates": [], "clarifications": [], "created_at": 1.0,
            },
        )
        store.add_session_entry(e)
        for _ in range(5):
            sigs.append(SignalEvent(event="abandoned_session", session_id="s", entry_id=eid,
                                    mode="curator", sql_hash="x", metadata={}))
        aggregate_patterns(store, e, sigs, mode="curator")

    # 15 abandonments (weight 0.5 each) vs 3 accepts → neg=7.5, threshold 1.5 → blocked
    assert store.patterns == []
