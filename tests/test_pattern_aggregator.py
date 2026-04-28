import pytest
from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry
from agent.signal_log import SignalLog, SignalEvent
from agent.pattern_aggregator import aggregate_patterns


def _seed_session(store, entry_id, query, sql, tables):
    entry = KnowledgeEntry(
        id=entry_id, source="query_session", category="query_session",
        content=query,
        metadata={
            "original_query": query,
            "enriched_query": query,
            "tables_used": tables,
            "accepted_candidates": [{"interpretation": "x", "sql": sql, "explanation": ""}],
            "rejected_candidates": [], "clarifications": [],
            "created_at": 1000.0,
        },
    )
    store.add_session_entry(entry)
    return entry


def test_aggregator_promotes_after_three_curator_accepts(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    e1 = _seed_session(store, "e1", "show high risk customers",   sql, ["KYC.CUSTOMERS"])
    e2 = _seed_session(store, "e2", "list high-risk customers",   sql.replace("HIGH", "VERY_HIGH"), ["KYC.CUSTOMERS"])
    e3 = _seed_session(store, "e3", "high risk customers please", sql, ["KYC.CUSTOMERS"])

    aggregate_patterns(store, e3, sigs, mode="curator")
    verified = [p for p in store.patterns if p.accept_count >= 3]
    assert len(verified) == 1
    assert verified[0].source_entry_ids and "e3" in verified[0].source_entry_ids


def test_aggregator_holds_off_when_only_two_distinct_sessions(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    _seed_session(store, "e1", "show high risk customers", sql, ["KYC.CUSTOMERS"])
    e2 = _seed_session(store, "e2", "high risk customers", sql, ["KYC.CUSTOMERS"])

    aggregate_patterns(store, e2, sigs, mode="curator")
    assert all(p.accept_count < 3 for p in store.patterns)


def test_aggregator_blocks_promotion_when_negative_signals_dominate(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    sql_hash = "abc"  # we'll lie about the hash; aggregator looks up by skeleton

    for eid, q in [("e1", "show high risk customers"),
                   ("e2", "list high-risk customers"),
                   ("e3", "high risk customers please")]:
        _seed_session(store, eid, q, sql, ["KYC.CUSTOMERS"])
        # 4 abandonments per session = strong negative
        for _ in range(4):
            sigs.append(SignalEvent(event="abandoned_session", session_id="any",
                                    entry_id=eid, mode="curator",
                                    sql_hash=sql_hash, metadata={}))

    aggregate_patterns(store, store.static_entries[-1], sigs, mode="curator")
    # No pattern should be in store (negatives dominate positives/2)
    promoted = [p for p in store.patterns if p.accept_count >= 3 and p.negative_signals < p.accept_count / 2]
    assert promoted == []


def test_aggregator_manual_promotion_skips_threshold(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS"
    e1 = _seed_session(store, "e1", "lone session", sql, ["KYC.CUSTOMERS"])

    aggregate_patterns(store, e1, sigs, mode="curator", manual_promotion=True)
    assert any(p.manual_promotion for p in store.patterns)
