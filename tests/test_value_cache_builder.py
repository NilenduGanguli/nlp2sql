"""Tests for value_cache_builder module — heuristic marking, LLM nomination, DISTINCT probe."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from knowledge_graph.config import GraphConfig, ValueCacheConfig


def test_value_cache_config_defaults_match_design():
    cfg = ValueCacheConfig()
    assert cfg.enabled is True
    assert cfg.max_values == 30
    assert cfg.probe_workers == 8
    assert cfg.probe_timeout_ms == 5000
    assert cfg.llm_nominate is True
    assert cfg.llm_batch_size == 50


def test_value_cache_config_reads_env(monkeypatch):
    monkeypatch.setenv("VALUE_CACHE_ENABLED", "false")
    monkeypatch.setenv("VALUE_CACHE_MAX_VALUES", "50")
    monkeypatch.setenv("VALUE_CACHE_PROBE_WORKERS", "16")
    cfg = ValueCacheConfig()
    assert cfg.enabled is False
    assert cfg.max_values == 50
    assert cfg.probe_workers == 16


def test_graph_config_composes_value_cache_config():
    gcfg = GraphConfig()
    assert isinstance(gcfg.value_cache, ValueCacheConfig)


# ---------------------------------------------------------------------------
# Heuristic marker pass
# ---------------------------------------------------------------------------

from knowledge_graph.value_cache_builder import mark_filter_candidates_heuristic


def test_mark_filter_candidates_heuristic_flags_kyc_columns(kyc_graph):
    n_flagged = mark_filter_candidates_heuristic(kyc_graph)
    assert n_flagged > 0

    expected_flagged = [
        "KYC.CUSTOMERS.RISK_RATING",
        "KYC.ACCOUNTS.STATUS",
        "KYC.ACCOUNTS.ACCOUNT_TYPE",
        "KYC.ACCOUNTS.CURRENCY",
        "KYC.KYC_REVIEWS.STATUS",
        "KYC.PEP_STATUS.IS_PEP",
        "KYC.PEP_STATUS.PEP_TYPE",
        "KYC.TRANSACTIONS.IS_FLAGGED",
        "KYC.TRANSACTIONS.TRANSACTION_TYPE",
        "KYC.RISK_ASSESSMENTS.RISK_LEVEL",
    ]
    for fqn in expected_flagged:
        node = kyc_graph.get_node("Column", fqn)
        assert node is not None, f"Column {fqn} not in graph"
        assert node.get("is_filter_candidate") is True, f"{fqn} should be flagged"
        assert node.get("filter_reason", "").startswith("heuristic:"), \
            f"{fqn} should have heuristic source"


def test_mark_filter_candidates_heuristic_skips_high_cardinality(kyc_graph):
    mark_filter_candidates_heuristic(kyc_graph)
    not_flagged = [
        "KYC.CUSTOMERS.CUSTOMER_ID",
        "KYC.CUSTOMERS.FIRST_NAME",
        "KYC.CUSTOMERS.LAST_NAME",
        "KYC.TRANSACTIONS.AMOUNT",
        "KYC.ACCOUNTS.BALANCE",
    ]
    for fqn in not_flagged:
        node = kyc_graph.get_node("Column", fqn)
        assert node is not None
        assert not node.get("is_filter_candidate"), f"{fqn} should NOT be flagged"


def test_mark_filter_candidates_heuristic_idempotent(kyc_graph):
    n1 = mark_filter_candidates_heuristic(kyc_graph)
    n2 = mark_filter_candidates_heuristic(kyc_graph)
    assert n1 == n2


# ---------------------------------------------------------------------------
# DISTINCT probe pass
# ---------------------------------------------------------------------------

from knowledge_graph.value_cache import ValueCache
from knowledge_graph.value_cache_builder import probe_filter_candidates


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def execute(self, sql, *args, **kwargs):
        self._executed = sql
        return self
    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.callTimeout = None
    def cursor(self):
        return _FakeCursor(self._rows)
    def close(self):
        pass


def test_probe_filter_candidates_populates_cache(kyc_graph, graph_config):
    mark_filter_candidates_heuristic(kyc_graph)
    fake_conn = _FakeConn([("ACTIVE",), ("DORMANT",), ("CLOSED",)])
    with patch("knowledge_graph.value_cache_builder.oracledb") as mock_oracledb:
        mock_oracledb.connect.return_value = fake_conn
        cache = probe_filter_candidates(kyc_graph, graph_config, max_workers=2)

    assert len(cache) > 0
    entry = cache.get("KYC", "ACCOUNTS", "STATUS")
    assert entry is not None
    assert entry.values == ["ACTIVE", "DORMANT", "CLOSED"]
    assert entry.too_many is False
    assert entry.error is None


def test_probe_filter_candidates_marks_too_many(kyc_graph, graph_config):
    mark_filter_candidates_heuristic(kyc_graph)
    fake_conn = _FakeConn([(f"V{i}",) for i in range(31)])
    with patch("knowledge_graph.value_cache_builder.oracledb") as mock_oracledb:
        mock_oracledb.connect.return_value = fake_conn
        cache = probe_filter_candidates(kyc_graph, graph_config, max_workers=2)

    entry = cache.get("KYC", "ACCOUNTS", "STATUS")
    assert entry is not None
    assert entry.too_many is True
    assert entry.values == []


def test_probe_filter_candidates_records_error(kyc_graph, graph_config):
    mark_filter_candidates_heuristic(kyc_graph)
    with patch("knowledge_graph.value_cache_builder.oracledb") as mock_oracledb:
        mock_oracledb.connect.side_effect = RuntimeError("ORA-12541: TNS no listener")
        cache = probe_filter_candidates(kyc_graph, graph_config, max_workers=2)

    entry = cache.get("KYC", "ACCOUNTS", "STATUS")
    assert entry is not None
    assert entry.error is not None
    assert entry.values == []


# ---------------------------------------------------------------------------
# initialize_graph integration
# ---------------------------------------------------------------------------

def test_initialize_graph_returns_tuple_with_value_cache():
    """Smoke test: initialize_graph returns (graph, report, value_cache)."""
    from knowledge_graph.config import GraphConfig, OracleConfig
    from knowledge_graph.init_graph import initialize_graph
    from knowledge_graph.value_cache import ValueCache

    # Force early-exit via failed connectivity check — we only assert the shape.
    with patch("knowledge_graph.init_graph.OracleMetadataExtractor") as cls:
        cls.return_value.check_connectivity.return_value = False
        cfg = GraphConfig(oracle=OracleConfig(
            dsn="x", user="y", password="z", target_schemas=["KYC"],
        ))
        result = initialize_graph(cfg)

    assert isinstance(result, tuple)
    assert len(result) == 3
    _graph, _report, value_cache = result
    assert isinstance(value_cache, ValueCache)
