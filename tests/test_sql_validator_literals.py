"""Tests for sql_validator literal-grounding integration (Phase 2)."""
from __future__ import annotations

import pytest

from agent.nodes.sql_validator import make_sql_validator
from knowledge_graph.value_cache import ValueCache, ValueCacheEntry


def _state(sql: str, **extra) -> dict:
    base = {
        "generated_sql": sql,
        "validation_errors": [],
        "validation_passed": False,
        "_trace": [],
    }
    base.update(extra)
    return base


def _cache_with(*entries) -> ValueCache:
    cache = ValueCache()
    for schema, table, col, values in entries:
        cache.set(schema, table, col, ValueCacheEntry(values=list(values)))
    return cache


def test_validator_passes_when_literal_matches_cache(kyc_graph):
    """Exact match → validation passes, no rewrites."""
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["ACTIVE", "DORMANT"]))
    validator = make_sql_validator(graph=kyc_graph, value_cache=cache)
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'ACTIVE'"
    out = validator(_state(sql))
    assert out["validation_passed"] is True
    assert out.get("value_mappings", []) == []


def test_validator_auto_fixes_confident_match(kyc_graph):
    """Lower-case literal → silently rewritten to cached upper-case value."""
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["ACTIVE", "DORMANT"]))
    validator = make_sql_validator(graph=kyc_graph, value_cache=cache)
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'active'"
    out = validator(_state(sql))
    assert out["validation_passed"] is True, f"errors: {out['validation_errors']}"
    assert "'ACTIVE'" in out["generated_sql"]
    assert "'active'" not in out["generated_sql"]
    assert len(out.get("value_mappings", [])) == 1
    m = out["value_mappings"][0]
    assert m["original"] == "active"
    assert m["mapped"] == "ACTIVE"
    assert m["column"] == "STATUS"


def test_validator_kicks_to_retry_when_no_match(kyc_graph):
    """No cached match → validation fails with a VALUE_HINT for the regenerator."""
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["A", "I", "P"]))
    validator = make_sql_validator(graph=kyc_graph, value_cache=cache)
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'WIBBLE'"
    out = validator(_state(sql))
    assert out["validation_passed"] is False
    errors = out["validation_errors"]
    assert any("[VALUE_HINT]" in e for e in errors), errors
    assert any("'A'" in e for e in errors), errors


def test_validator_no_op_when_value_cache_missing(kyc_graph):
    """When value_cache=None, the validator must not emit any literal findings."""
    validator = make_sql_validator(graph=kyc_graph, value_cache=None)
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'literally-anything'"
    out = validator(_state(sql))
    # No literal findings — only the existing checks fire (column existence
    # passes because STATUS exists; everything else passes too).
    assert out["validation_passed"] is True
    assert out.get("value_mappings", []) == []


def test_validator_does_not_apply_rewrites_when_existing_errors_present(kyc_graph):
    """If non-literal errors are already present, skip rewrites — let retry fix root cause first."""
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["ACTIVE"]))
    validator = make_sql_validator(graph=kyc_graph, value_cache=cache)
    # Use a CREATE keyword to force a blocked-keyword error.
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'active' AND 1 = 1 OR (CREATE)"
    out = validator(_state(sql))
    # Should fail validation due to CREATE keyword, regardless of literal state.
    assert out["validation_passed"] is False


def test_validator_handles_in_clause_partial_mismatch(kyc_graph):
    """IN clause with one bad value → finding for the bad one, no rewrite for the rest."""
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["ACTIVE", "DORMANT", "CLOSED"]))
    validator = make_sql_validator(graph=kyc_graph, value_cache=cache)
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS IN ('ACTIVE', 'WIBBLE')"
    out = validator(_state(sql))
    assert out["validation_passed"] is False
    assert any("WIBBLE" in e and "[VALUE_HINT]" in e for e in out["validation_errors"])
