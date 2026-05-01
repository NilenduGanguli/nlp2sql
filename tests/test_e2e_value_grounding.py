"""
E2E test for Phase 1 value grounding.

Runs only when ORACLE_DSN/USER/PASSWORD/SCHEMA env vars are set.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ORACLE_DSN"),
    reason="ORACLE_DSN not set — skipping E2E value-grounding test",
)


def test_initialize_graph_populates_value_cache_for_kyc():
    from knowledge_graph.config import GraphConfig, OracleConfig
    from knowledge_graph.init_graph import initialize_graph

    cfg = GraphConfig(oracle=OracleConfig(
        dsn=os.environ["ORACLE_DSN"],
        user=os.environ["ORACLE_USER"],
        password=os.environ["ORACLE_PASSWORD"],
        target_schemas=[os.environ.get("ORACLE_SCHEMA", "KYC")],
    ))
    graph, report, value_cache = initialize_graph(cfg)
    assert report["success"] is True
    assert len(value_cache) > 0
    stats = value_cache.stats()
    assert stats["ok"] >= 1, f"Expected >=1 ok entry but got {stats}"


def test_ddl_serialization_includes_values_annotation():
    from knowledge_graph.config import GraphConfig, OracleConfig
    from knowledge_graph.init_graph import initialize_graph
    from knowledge_graph.column_value_cache import (
        set_loaded_value_cache,
        make_value_getter,
    )
    from knowledge_graph.traversal import get_context_subgraph, serialize_context_to_ddl

    cfg = GraphConfig(oracle=OracleConfig(
        dsn=os.environ["ORACLE_DSN"],
        user=os.environ["ORACLE_USER"],
        password=os.environ["ORACLE_PASSWORD"],
        target_schemas=[os.environ.get("ORACLE_SCHEMA", "KYC")],
    ))
    graph, _report, value_cache = initialize_graph(cfg)
    set_loaded_value_cache(value_cache)

    fqn_candidates = [t["fqn"] for t in graph.get_all_nodes("Table")]
    assert fqn_candidates
    ctx = get_context_subgraph(graph, fqn_candidates[:3])

    ddl = serialize_context_to_ddl(ctx, get_values=make_value_getter(cfg))
    assert "-- Values(" in ddl, (
        "Expected '-- Values(...)' annotation in DDL but got:\n"
        + ddl[:2000]
    )
