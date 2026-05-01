"""Tests for agent.value_validator — literal extraction, fuzzy match, validate."""
from __future__ import annotations

import pytest

from agent.value_validator import (
    Finding,
    Rewrite,
    apply_rewrites,
    extract_where_literals,
    fuzzy_score,
    validate_where_literals,
)
from knowledge_graph.value_cache import ValueCache, ValueCacheEntry


# ---------------------------------------------------------------------------
# fuzzy_score
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("literal,cached,expected_min", [
    ("ACTIVE", "ACTIVE", 1.0),               # exact
    ("active", "ACTIVE", 1.0),               # case-insensitive equal
    (" ACTIVE ", "ACTIVE", 1.0),             # strip
    ("A", "ACTIVE", 0.95),                   # cached startswith literal — prefix
    ("ACTIVE", "A", 0.95),                   # literal startswith cached — prefix
    ("Pending Review", "PENDING", 0.90),     # token-contains
])
def test_fuzzy_score_strong_matches(literal, cached, expected_min):
    score = fuzzy_score(literal, cached)
    assert score >= expected_min, f"{literal!r} vs {cached!r}: got {score}"


def test_fuzzy_score_low_for_unrelated():
    assert fuzzy_score("ACTIVE", "DELETED") < 0.85
    assert fuzzy_score("ACTIVE", "X") == 0.0  # too short to be prefix-related


def test_fuzzy_score_handles_numeric_string():
    assert fuzzy_score("1", "1") == 1.0
    assert fuzzy_score(1, "1") == 1.0     # numeric input coerced
    assert fuzzy_score("1", 1) == 1.0


# ---------------------------------------------------------------------------
# extract_where_literals
# ---------------------------------------------------------------------------

def test_extract_simple_equals():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'ACTIVE'"
    lits = extract_where_literals(sql)
    assert len(lits) == 1
    assert lits[0].alias == "A"
    assert lits[0].column == "STATUS"
    assert lits[0].literal == "ACTIVE"
    assert lits[0].operator == "="


def test_extract_in_clause():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS IN ('ACTIVE', 'DORMANT')"
    lits = extract_where_literals(sql)
    assert len(lits) == 2
    cols = {l.literal for l in lits}
    assert cols == {"ACTIVE", "DORMANT"}
    assert all(l.operator == "IN" for l in lits)


def test_extract_skips_like_pattern():
    sql = "SELECT * FROM KYC.CUSTOMERS c WHERE c.FIRST_NAME LIKE 'JOHN%'"
    lits = extract_where_literals(sql)
    assert lits == []  # LIKE is intentionally skipped


def test_extract_skips_between():
    sql = "SELECT * FROM KYC.TXN t WHERE t.AMOUNT BETWEEN 100 AND 500"
    lits = extract_where_literals(sql)
    assert lits == []


def test_extract_unqualified_column_skipped():
    """A literal without a clear alias.column form is skipped."""
    sql = "SELECT * FROM KYC.ACCOUNTS WHERE STATUS = 'ACTIVE'"
    lits = extract_where_literals(sql)
    # Without an alias mapping in the WHERE itself, we can't resolve to FQN.
    # The extractor still records it (alias='').
    # The validator step is what filters by FQN match.
    assert lits == [] or all(l.alias == "" for l in lits)


def test_extract_having_clause():
    sql = (
        "SELECT a.STATUS, COUNT(*) FROM KYC.ACCOUNTS a "
        "GROUP BY a.STATUS HAVING a.STATUS = 'CLOSED'"
    )
    lits = extract_where_literals(sql)
    assert any(l.literal == "CLOSED" for l in lits)


# ---------------------------------------------------------------------------
# validate_where_literals
# ---------------------------------------------------------------------------

def _cache_with(*entries):
    """Build a ValueCache from (schema, table, col, [values]) tuples."""
    cache = ValueCache()
    for schema, table, col, values in entries:
        cache.set(schema, table, col, ValueCacheEntry(values=list(values)))
    return cache


def test_validate_passes_when_literal_in_cache():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'ACTIVE'"
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["ACTIVE", "DORMANT"]))
    findings, rewrites = validate_where_literals(sql, cache)
    assert findings == []
    assert rewrites == []


def test_validate_auto_fixes_case_insensitive_match():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'active'"
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["ACTIVE", "DORMANT"]))
    findings, rewrites = validate_where_literals(sql, cache)
    assert findings == []
    assert len(rewrites) == 1
    r = rewrites[0]
    assert r.original == "active"
    assert r.replacement == "ACTIVE"


def test_validate_auto_fixes_unique_prefix():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'ACTIVE'"
    # Cached values are short codes; literal is the long form
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["A", "I", "P"]))
    findings, rewrites = validate_where_literals(sql, cache)
    assert findings == [], f"Unexpected findings: {findings}"
    assert len(rewrites) == 1
    assert rewrites[0].replacement == "A"


def test_validate_returns_finding_when_no_match():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'WIBBLE'"
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["A", "I", "P"]))
    findings, rewrites = validate_where_literals(sql, cache)
    assert len(findings) == 1
    assert findings[0].bad_literal == "WIBBLE"
    assert "A" in findings[0].allowed_values
    assert rewrites == []


def test_validate_returns_finding_when_ambiguous_match():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'A'"
    # Two cached values both start with 'A' → ambiguous → kick to regenerate
    cache = _cache_with(("KYC", "ACCOUNTS", "STATUS", ["ACTIVE", "ARCHIVED", "I"]))
    findings, rewrites = validate_where_literals(sql, cache)
    # 'A' is exactly cached value? No — cached is ACTIVE, ARCHIVED, I.
    # 'A' is a prefix of two — ambiguous → finding, no rewrite.
    assert len(findings) == 1
    assert rewrites == []


def test_validate_skips_when_cache_missing():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'ACTIVE'"
    findings, rewrites = validate_where_literals(sql, value_cache=None)
    assert findings == []
    assert rewrites == []


def test_validate_skips_too_many_columns():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.NAME = 'Foo'"
    cache = ValueCache()
    cache.set("KYC", "ACCOUNTS", "NAME", ValueCacheEntry(values=[], too_many=True))
    findings, rewrites = validate_where_literals(sql, cache)
    assert findings == []
    assert rewrites == []


# ---------------------------------------------------------------------------
# apply_rewrites
# ---------------------------------------------------------------------------

def test_apply_rewrites_swaps_literal_in_place():
    sql = "SELECT * FROM KYC.ACCOUNTS a WHERE a.STATUS = 'active'"
    rw = [Rewrite(
        table_fqn="KYC.ACCOUNTS",
        column="STATUS",
        original="active",
        replacement="ACTIVE",
        reason="case-insensitive equal",
    )]
    new_sql = apply_rewrites(sql, rw)
    assert "'ACTIVE'" in new_sql
    assert "'active'" not in new_sql


def test_apply_rewrites_preserves_other_literals():
    sql = "SELECT * FROM KYC.A a WHERE a.STATUS = 'active' AND a.NOTES = 'active customer'"
    rw = [Rewrite("KYC.A", "STATUS", "active", "ACTIVE", "match")]
    new_sql = apply_rewrites(sql, rw)
    assert "'ACTIVE'" in new_sql
    # The notes literal must NOT be rewritten — apply only swaps exact-quoted
    # literals that are the rewrite target.
    # We use single-quoted exact-match → 'active' inside 'active customer'
    # remains untouched because the substring 'active customer' is the literal.
    assert "'active customer'" in new_sql
