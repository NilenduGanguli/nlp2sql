"""Tests for KYCKnowledgeStore.rank_accepted_entries — graded ranking, top-K."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry


def _entry(content, **md):
    return KnowledgeEntry(
        id=md.pop("id", f"id_{content[:6]}_{time.time()}"),
        source="query_session",
        category="query_session",
        content=content,
        metadata={
            "original_query": md.pop("original_query", ""),
            "enriched_query": md.pop("enriched_query", ""),
            "description": md.pop("description", ""),
            "why_this_sql": md.pop("why_this_sql", ""),
            "key_concepts": md.pop("key_concepts", []),
            "tags": md.pop("tags", []),
            "tables_used": md.pop("tables_used", []),
            "created_at": md.pop("created_at", time.time()),
            **md,
        },
    )


def _store_with(*entries):
    s = KYCKnowledgeStore(persist_path=f"/tmp/test_rank_{time.time()}.json")
    for e in entries:
        s.add_session_entry(e)
    return s


def _graph_with_tables(*table_fqns):
    """Mock graph that says all listed FQNs exist."""
    g = MagicMock()
    g.get_node = lambda label, fqn: {"fqn": fqn} if fqn in table_fqns else None
    return g


def test_rank_returns_top_k_sorted_by_score():
    s = _store_with(
        _entry("active customers by region",
               original_query="active customers by region",
               description="counts of active customers per region",
               key_concepts=["active customer", "region"],
               tables_used=["KYC.CUSTOMERS"]),
        _entry("transactions per account",
               original_query="transactions per account",
               description="counts transactions for each account",
               key_concepts=["transaction", "account"],
               tables_used=["KYC.TRANSACTIONS"]),
        _entry("inactive customers count",
               original_query="how many inactive customers",
               description="counts customers with inactive status",
               key_concepts=["inactive customer"],
               tables_used=["KYC.CUSTOMERS"]),
    )
    g = _graph_with_tables("KYC.CUSTOMERS", "KYC.TRANSACTIONS")
    ranked = s.rank_accepted_entries(
        "show me active customers in each region", top_k=3, graph=g,
    )
    assert len(ranked) == 3
    assert "region" in ranked[0][0].content
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_skips_entries_whose_tables_are_missing():
    s = _store_with(
        _entry("active customers",
               original_query="active customers",
               tables_used=["KYC.GONE_TABLE"]),
    )
    g = _graph_with_tables("KYC.CUSTOMERS")
    ranked = s.rank_accepted_entries("active customers", top_k=3, graph=g)
    assert ranked == []


def test_rank_falls_back_to_legacy_jaccard_when_description_missing():
    """Entries without the new fields still rank correctly via the legacy path."""
    e = _entry(
        "old style entry",
        original_query="how many active customers per region",
    )
    e.metadata.pop("description", None)
    e.metadata.pop("key_concepts", None)
    s = _store_with(e)
    g = _graph_with_tables("KYC.CUSTOMERS")
    ranked = s.rank_accepted_entries("active customers per region", top_k=3, graph=g)
    assert len(ranked) == 1
    assert ranked[0][1] > 0.0


def test_rank_returns_empty_when_query_too_short():
    s = _store_with(_entry("anything"))
    ranked = s.rank_accepted_entries("a", top_k=3, graph=_graph_with_tables())
    assert ranked == []


def test_rank_top_k_caps_results():
    """top_k=2 → max 2 results even when 3 entries are similar."""
    s = _store_with(
        _entry("e1", original_query="active customers",
               description="active customers list", tables_used=[]),
        _entry("e2", original_query="active customers list",
               description="another active customers query", tables_used=[]),
        _entry("e3", original_query="active customers count",
               description="active customers count", tables_used=[]),
    )
    g = _graph_with_tables()
    ranked = s.rank_accepted_entries("active customers", top_k=2, graph=g)
    assert len(ranked) == 2
