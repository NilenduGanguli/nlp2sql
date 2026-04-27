"""Unit tests for find_session_match + add_session_entry."""
import os
import tempfile
from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry


class _StubGraph:
    """Minimal stand-in for KnowledgeGraph: only needs to know whether a table FQN exists."""

    def __init__(self, tables):
        self._tables = set(tables)

    def get_node(self, label, node_id):
        if label == "Table" and node_id in self._tables:
            return {"fqn": node_id}
        return None


def _new_store():
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    return KYCKnowledgeStore(persist_path=tmp.name)


def _session_entry(query: str, tables, eid: str = "e1"):
    return KnowledgeEntry(
        id=eid,
        source="query_session",
        category="query_session",
        content="Comprehensive session document...",
        metadata={
            "original_query": query,
            "enriched_query": query,
            "tables_used": tables,
            "accepted_candidates": [
                {"interpretation": "primary", "sql": "SELECT 1 FROM " + tables[0], "explanation": "x"}
            ],
            "created_at": 1000.0,
        },
    )


def test_find_session_match_returns_entry_above_threshold():
    s = _new_store()
    s.add_session_entry(_session_entry("show me high risk customers", ["KYC.CUSTOMERS"]))
    g = _StubGraph(["KYC.CUSTOMERS"])

    found = s.find_session_match("show me high risk customers", g)
    assert found is not None
    assert found.metadata["original_query"] == "show me high risk customers"


def test_find_session_match_below_threshold_returns_none():
    s = _new_store()
    s.add_session_entry(_session_entry("show me high risk customers", ["KYC.CUSTOMERS"]))
    g = _StubGraph(["KYC.CUSTOMERS"])

    assert s.find_session_match("what tables exist", g) is None


def test_find_session_match_skips_when_table_missing():
    s = _new_store()
    s.add_session_entry(_session_entry("show me high risk customers", ["KYC.CUSTOMERS"]))
    g = _StubGraph([])  # no tables

    assert s.find_session_match("show me high risk customers", g) is None


def test_find_session_match_picks_higher_score_then_newer():
    s = _new_store()
    e1 = _session_entry("high risk customers status", ["KYC.CUSTOMERS"], eid="old")
    e1.metadata["created_at"] = 1000.0
    e2 = _session_entry("high risk customers status", ["KYC.CUSTOMERS"], eid="new")
    e2.metadata["created_at"] = 9999.0
    s.add_session_entry(e1)
    s.add_session_entry(e2)
    g = _StubGraph(["KYC.CUSTOMERS"])

    found = s.find_session_match("high risk customers status", g)
    assert found is not None
    assert found.id == "new"  # tie broken by created_at


def test_add_session_entry_persists_and_filters():
    s = _new_store()
    s.add_session_entry(_session_entry("q1", ["KYC.A"]))
    sessions = [e for e in s.static_entries if e.source == "query_session"]
    assert len(sessions) == 1
