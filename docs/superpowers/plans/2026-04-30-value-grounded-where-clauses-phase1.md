# Value-Grounded WHERE Clauses — Phase 1 (Layer 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-compute and persist the distinct values of every "filter-likely" column in the Oracle schema, then expose them to the SQL-generator's DDL context so generated WHERE clauses use real database values, not LLM-inferred ones.

**Architecture:** Three-step pipeline runs once per graph build: (1) cheap heuristic flags filter-candidate columns, (2) LLM nominates additional candidates the heuristic missed, (3) parallel `SELECT DISTINCT … FETCH FIRST 31 ROWS ONLY` populates a value cache, persisted to disk as JSON next to the graph pickle. The SQL-generator system prompt is updated to mandate verbatim use of annotated values.

**Tech Stack:** Python 3.11, `oracledb` (already used), `concurrent.futures.ThreadPoolExecutor`, JSON, `pytest`.

**Why JSON for the value cache (not pickle):** the cache contents originate from the DB — string lists + small primitives. JSON is safe to deserialize from any source, easy to inspect/debug on disk, and matches how Layer-3 will surface values to the UI later.

---

## Out of Scope (deferred to later phases)

- Layer 3 (literal validation + auto-fix on retry) → Phase 2
- UI controls (rebuild button, status panel, value mappings panel) → Phase 3
- LIKE / BETWEEN validation → not in scope
- Frequency / count storage per value → not in scope (YAGNI)

---

## File Structure

| Status | Path | Responsibility |
|---|---|---|
| **CREATE** | `knowledge_graph/value_cache.py` | Disk-persistent JSON cache of distinct values per (schema, table, column). save/load/get/invalidate. |
| **CREATE** | `knowledge_graph/value_cache_builder.py` | Heuristic marker pass + parallel DISTINCT probe. |
| **CREATE** | `tests/test_value_cache.py` | JSON round-trip, get/set/miss, schema-hash key. |
| **CREATE** | `tests/test_value_cache_builder.py` | Heuristic rules (positive/negative), DISTINCT probe with mocked Oracle, too-many cap, error path. |
| **CREATE** | `tests/test_llm_nominator.py` | LLM batch sizing, parsing, partial-failure tolerance, idempotency. |
| **CREATE** | `tests/test_column_value_cache.py` | Widened heuristic + disk-loaded cache integration. |
| **MODIFY** | `knowledge_graph/config.py` | Add `ValueCacheConfig` dataclass; expose on `GraphConfig`. |
| **MODIFY** | `knowledge_graph/column_value_cache.py` | Widen `is_likely_enum_column` heuristic; route lazy fetches through the new disk-loaded cache. |
| **MODIFY** | `knowledge_graph/llm_enhancer.py` | Add `nominate_filter_candidates_llm()` (skips already-flagged cols). |
| **MODIFY** | `knowledge_graph/init_graph.py` | After graph build, optionally run heuristic + LLM nomination + DISTINCT probe; return `ValueCache` from `initialize_graph()`. |
| **MODIFY** | `app.py` | Load/save value cache alongside graph; new `_GraphBundle` field `value_cache`. |
| **MODIFY** | `agent/nodes/context_builder.py` | (No change required if the existing `make_value_getter` route is left intact — it transparently picks up the disk-loaded cache through `set_loaded_value_cache`.) |
| **MODIFY** | `prompts/sql_generator_system.txt` + `agent/nodes/sql_generator.py:_SYSTEM_PROMPT` | Add rules 17–19 (verbatim use of annotated values). |

---

## Conventions

- All Column-node mutations: `graph.merge_node("Column", col_fqn, {"is_filter_candidate": True, "filter_reason": "<source>:<rule>"})`
- `filter_reason` prefix: `"heuristic:name_word"`, `"heuristic:abbrev"`, `"heuristic:flag_prefix"`, `"heuristic:short_string"`, `"heuristic:short_numeric"`, `"llm:<short reason>"`
- Cache key: `(schema_upper, table_upper, column_upper)` in memory; serialized as `"SCHEMA|TABLE|COL"` strings in JSON.
- Probe SQL identifier quoting: `"SCHEMA"."TABLE"."COL"` (matches existing `column_value_cache.py`)
- All commits use Conventional Commits: `feat(value_cache): …`, `test(value_cache): …`

---

## Task 1: ValueCacheConfig dataclass

**Files:**
- Modify: `knowledge_graph/config.py:64-91`
- Test: `tests/test_value_cache_builder.py` (new file)

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_value_cache_builder.py`:

```python
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
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `python -m pytest tests/test_value_cache_builder.py::test_value_cache_config_defaults_match_design -v`

Expected: FAIL — `ImportError: cannot import name 'ValueCacheConfig'`

- [ ] **Step 1.3: Add `ValueCacheConfig` to `knowledge_graph/config.py`**

Insert after the existing `GraphConfig` class (after line 90):

```python
@dataclass
class ValueCacheConfig:
    """
    Configuration for the column-value cache.

    Drives the precomputed distinct-value lookup that grounds SQL WHERE
    clauses in real database values rather than LLM-inferred guesses.
    """

    enabled: bool = field(
        default_factory=lambda: os.getenv("VALUE_CACHE_ENABLED", "true").lower()
        not in ("false", "0", "no")
    )
    max_values: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_MAX_VALUES", "30"))
    )
    probe_workers: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_PROBE_WORKERS", "8"))
    )
    probe_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_PROBE_TIMEOUT_MS", "5000"))
    )
    llm_nominate: bool = field(
        default_factory=lambda: os.getenv("VALUE_CACHE_LLM_NOMINATE", "true").lower()
        not in ("false", "0", "no")
    )
    llm_batch_size: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_LLM_BATCH_SIZE", "50"))
    )
```

Then add `value_cache: ValueCacheConfig = field(default_factory=ValueCacheConfig)` to `GraphConfig` (insert as a new field, before `def validate`).

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_value_cache_builder.py -v`

Expected: PASS for all three tests.

- [ ] **Step 1.5: Commit**

```bash
git add knowledge_graph/config.py tests/test_value_cache_builder.py
git commit -m "feat(value_cache): add ValueCacheConfig dataclass

Phase 1 / Layer 1 — config surface for the precomputed column-value cache."
```

---

## Task 2: Persistent JSON value cache module

**Files:**
- Create: `knowledge_graph/value_cache.py`
- Test: `tests/test_value_cache.py`

- [ ] **Step 2.1: Write the failing test**

Create `tests/test_value_cache.py`:

```python
"""Tests for the disk-persistent JSON value cache."""
from __future__ import annotations

import json
import os
import time

import pytest

from knowledge_graph.value_cache import (
    ValueCache,
    ValueCacheEntry,
    get_value_cache_path,
    load_value_cache,
    save_value_cache,
)


def test_value_cache_entry_defaults():
    e = ValueCacheEntry(values=["A", "B"])
    assert e.values == ["A", "B"]
    assert e.too_many is False
    assert e.error is None
    assert e.probed_at > 0


def test_value_cache_set_and_get():
    cache = ValueCache()
    cache.set("KYC", "ACCOUNTS", "STATUS", ValueCacheEntry(values=["ACTIVE", "DORMANT"]))
    entry = cache.get("KYC", "ACCOUNTS", "STATUS")
    assert entry is not None
    assert entry.values == ["ACTIVE", "DORMANT"]


def test_value_cache_get_missing_returns_none():
    cache = ValueCache()
    assert cache.get("KYC", "NOPE", "X") is None


def test_value_cache_keys_are_uppercased():
    cache = ValueCache()
    cache.set("kyc", "accounts", "status", ValueCacheEntry(values=["A"]))
    assert cache.get("KYC", "ACCOUNTS", "STATUS") is not None
    assert cache.get("kYc", "AccountS", "stATus") is not None


def test_value_cache_round_trip(tmp_path):
    cache = ValueCache()
    cache.set("KYC", "ACCOUNTS", "STATUS", ValueCacheEntry(values=["ACTIVE", "CLOSED"]))
    cache.set("KYC", "ACCOUNTS", "BIG_COL", ValueCacheEntry(values=[], too_many=True))
    cache.set("KYC", "ACCOUNTS", "ERR_COL", ValueCacheEntry(values=[], error="ORA-12541"))

    path = tmp_path / "values_test.json"
    assert save_value_cache(cache, str(path)) is True
    assert os.path.exists(path)

    # Verify on-disk format is human-readable JSON
    with open(path) as fh:
        raw = json.load(fh)
    assert raw["version"] == "1"
    assert "entries" in raw

    loaded = load_value_cache(str(path))
    assert loaded is not None
    assert loaded.get("KYC", "ACCOUNTS", "STATUS").values == ["ACTIVE", "CLOSED"]
    assert loaded.get("KYC", "ACCOUNTS", "BIG_COL").too_many is True
    assert loaded.get("KYC", "ACCOUNTS", "ERR_COL").error == "ORA-12541"


def test_load_value_cache_missing_returns_none(tmp_path):
    assert load_value_cache(str(tmp_path / "does_not_exist.json")) is None


def test_load_value_cache_corrupt_returns_none(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("not valid json{{{")
    assert load_value_cache(str(p)) is None


def test_get_value_cache_path_uses_graph_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAPH_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("ORACLE_DSN", "host:1521/X")
    monkeypatch.setenv("ORACLE_USER", "u")
    monkeypatch.setenv("ORACLE_TARGET_SCHEMAS", "KYC")
    p = get_value_cache_path()
    assert p.startswith(str(tmp_path))
    assert p.endswith(".json")
    assert "values_" in os.path.basename(p)
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `python -m pytest tests/test_value_cache.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'knowledge_graph.value_cache'`

- [ ] **Step 2.3: Implement `knowledge_graph/value_cache.py`**

```python
"""
Disk-persistent value cache (JSON)
===================================
Stores the distinct values of every "filter-likely" column so the SQL
generator can ground WHERE clauses in real DB values instead of guessing.

Layout mirrors graph_cache.py:
  - One JSON file per (DSN, user, schemas, format-version) tuple
  - Atomic write via ``.tmp`` + ``os.replace``
  - Lives next to graph_*.pkl in GRAPH_CACHE_PATH

JSON (rather than pickle) is used because:
  - The contents originate from the database — JSON cannot execute code
    on load, so an attacker-controlled cache file cannot pwn the process.
  - The file is human-readable, which makes debugging and ops trivial.
  - The data shape (list[str] + small primitives) is trivially serialisable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, Tuple

from knowledge_graph.graph_cache import _compute_schema_hash, get_cache_dir

logger = logging.getLogger(__name__)

_VALUE_CACHE_FORMAT_VERSION = "1"


@dataclass
class ValueCacheEntry:
    """Cached probe result for one (schema, table, column) triple."""

    values: list = field(default_factory=list)
    too_many: bool = False              # column had > max_values distinct values
    error: Optional[str] = None         # set when the probe failed
    probed_at: float = field(default_factory=time.time)


class ValueCache:
    """In-memory map keyed by (SCHEMA, TABLE, COLUMN) — case-insensitive on input."""

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str, str], ValueCacheEntry] = {}

    def get(self, schema: str, table: str, column: str) -> Optional[ValueCacheEntry]:
        return self._entries.get(self._key(schema, table, column))

    def set(self, schema: str, table: str, column: str, entry: ValueCacheEntry) -> None:
        self._entries[self._key(schema, table, column)] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def items(self):
        return self._entries.items()

    def stats(self) -> Dict[str, int]:
        ok = sum(1 for e in self._entries.values() if e.values and not e.error)
        too_many = sum(1 for e in self._entries.values() if e.too_many)
        errors = sum(1 for e in self._entries.values() if e.error)
        return {"total": len(self), "ok": ok, "too_many": too_many, "errors": errors}

    @staticmethod
    def _key(schema: str, table: str, column: str) -> Tuple[str, str, str]:
        return (schema.upper(), table.upper(), column.upper())


def get_value_cache_path(config=None) -> str:
    """Return the disk path of the value cache for *config*."""
    cache_dir = get_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    schema_hash = _compute_schema_hash(config)
    return os.path.join(cache_dir, f"values_{schema_hash}.json")


def save_value_cache(cache: ValueCache, path: str) -> bool:
    """Serialise *cache* to *path* atomically as JSON. Returns True on success."""
    try:
        entries_serialised = {
            "|".join(key): asdict(entry)
            for key, entry in cache._entries.items()
        }
        payload = {
            "version": _VALUE_CACHE_FORMAT_VERSION,
            "created_at": time.time(),
            "entries": entries_serialised,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        size_mb = os.path.getsize(path) / 1_000_000
        logger.info(
            "Value cache saved: %s (%.2f MB, %d entries)",
            path, size_mb, len(cache),
        )
        return True
    except Exception as exc:
        logger.warning("Failed to save value cache to %s: %s", path, exc)
        try:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        except OSError:
            pass
        return False


def load_value_cache(path: str) -> Optional[ValueCache]:
    """Load a previously saved value cache from JSON. None if missing/corrupt/wrong-version."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:
        logger.warning("Value cache at %s is unreadable: %s", path, exc)
        return None
    if payload.get("version") != _VALUE_CACHE_FORMAT_VERSION:
        logger.info(
            "Value cache version mismatch (%r vs %r) — discarding.",
            payload.get("version"), _VALUE_CACHE_FORMAT_VERSION,
        )
        return None
    cache = ValueCache()
    for key_str, entry_dict in payload.get("entries", {}).items():
        parts = key_str.split("|")
        if len(parts) != 3:
            continue
        schema, table, column = parts
        cache.set(schema, table, column, ValueCacheEntry(
            values=entry_dict.get("values", []),
            too_many=bool(entry_dict.get("too_many", False)),
            error=entry_dict.get("error"),
            probed_at=float(entry_dict.get("probed_at", time.time())),
        ))
    age_h = (time.time() - payload.get("created_at", time.time())) / 3600
    logger.info(
        "Value cache loaded from %s (age=%.1fh, %d entries)",
        path, age_h, len(cache),
    )
    return cache


def invalidate_value_cache(path: str) -> bool:
    """Remove the value cache file. Returns True if it was removed."""
    try:
        os.remove(path)
        logger.info("Value cache invalidated: %s", path)
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.warning("Could not remove value cache %s: %s", path, exc)
        return False
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_value_cache.py -v`

Expected: PASS for all 8 tests.

- [ ] **Step 2.5: Commit**

```bash
git add knowledge_graph/value_cache.py tests/test_value_cache.py
git commit -m "feat(value_cache): disk-persistent JSON ValueCache

Mirrors graph_cache.py: atomic write, schema-hash filename, version-gated
load. JSON (not pickle) so cache files originating from the DB cannot
execute code on deserialisation."
```

---

## Task 3: Widen `is_likely_enum_column` heuristic

**Files:**
- Modify: `knowledge_graph/column_value_cache.py:39-55`
- Test: `tests/test_column_value_cache.py` (new file)

- [ ] **Step 3.1: Write the failing test**

Create `tests/test_column_value_cache.py`:

```python
"""Tests for is_likely_enum_column heuristic — widened to catch KYC abbreviations."""
from __future__ import annotations

import pytest

from knowledge_graph.column_value_cache import is_likely_enum_column


@pytest.mark.parametrize("name, dtype, length", [
    # English enum words (existing behaviour)
    ("STATUS",        "VARCHAR2", 20),
    ("ACCOUNT_STATUS","VARCHAR2", 20),
    ("RISK_RATING",   "VARCHAR2", 10),
    ("CURRENCY",      "VARCHAR2", 3),
    # Abbreviation suffixes — NEW
    ("STS_CD",        "VARCHAR2", 5),
    ("RSK_LVL",       "VARCHAR2", 3),
    ("ACCT_TYP",      "VARCHAR2", 5),
    ("PAY_FLG",       "CHAR",     1),
    ("REASON_CD",     "VARCHAR2", 8),
    # Short string types — existing
    ("CODE",          "VARCHAR2", 5),
    ("FLAG",          "CHAR",     1),
])
def test_is_enum_positive(name, dtype, length):
    assert is_likely_enum_column(name, dtype, length) is True


@pytest.mark.parametrize("name, dtype, length, precision", [
    # Tiny numeric flags — NEW
    ("IS_ACTIVE",     "NUMBER", 0,  1),
    ("HAS_PEP",       "NUMBER", 0,  1),
    ("CAN_TRADE",     "NUMBER", 0,  1),
    ("PRIORITY_LVL",  "NUMBER", 0,  2),
])
def test_is_enum_positive_numeric(name, dtype, length, precision):
    assert is_likely_enum_column(name, dtype, length, precision) is True


@pytest.mark.parametrize("name, dtype, length, precision", [
    # High-cardinality identifiers
    ("CUSTOMER_ID",      "NUMBER",   0,   10),
    ("ACCOUNT_ID",       "NUMBER",   0,   12),
    ("FIRST_NAME",       "VARCHAR2", 100, 0),
    ("DESCRIPTION",      "VARCHAR2", 500, 0),
    ("EMAIL",            "VARCHAR2", 200, 0),
    # Long string columns
    ("REVIEW_NOTES",     "CLOB",     0,   0),
    # Numeric metrics
    ("AMOUNT",           "NUMBER",   0,   18),
    ("BALANCE",          "NUMBER",   0,   18),
    ("RISK_SCORE",       "NUMBER",   0,   5),
])
def test_is_enum_negative(name, dtype, length, precision):
    assert is_likely_enum_column(name, dtype, length, precision) is False
```

- [ ] **Step 3.2: Run tests to verify abbreviations + numeric flags fail**

Run: `python -m pytest tests/test_column_value_cache.py -v`

Expected: FAIL on the new abbreviation cases (`STS_CD`, `RSK_LVL`, `IS_ACTIVE`, etc.).

- [ ] **Step 3.3: Widen the heuristic in `knowledge_graph/column_value_cache.py`**

Replace lines 27–55 (the `_ENUM_WORDS` block and `is_likely_enum_column` function) with:

```python
# Maximum distinct values to consider a column "enum-like"
MAX_DISTINCT_VALUES = 30

# Whole-word enum names (case-insensitive). Anchored: matches NAME, NAME_*,
# *_NAME, but not arbitrary substrings.
_ENUM_WORDS = {
    "STATUS", "TYPE", "FLAG", "CODE", "CATEGORY", "LEVEL", "TIER",
    "CLASS", "STATE", "REASON", "KIND", "MODE", "PRIORITY", "GENDER",
    "STAGE", "PHASE", "RATING", "INDICATOR", "ACTIVE", "ENABLED",
    "RISK", "CURRENCY", "COUNTRY", "GRADE", "BUCKET", "SEGMENT",
    "ROLE", "METHOD", "CHANNEL", "SOURCE", "SCOPE", "RELATIONSHIP",
}

# Short suffix abbreviations common in KYC/financial schemas (Oracle uppercase).
# Matched as the trailing token after an underscore, e.g. ACCT_TYP, RSK_LVL.
_ENUM_ABBREV_SUFFIXES = {
    "CD", "TYP", "FLG", "STS", "CAT", "LVL", "RSK", "RSN",
    "IND", "PRI", "GRP", "TY", "CTGY", "SEG",
}

# Boolean-flag prefixes — usually NUMBER(1) or CHAR(1).
_FLAG_PREFIXES = ("IS_", "HAS_", "CAN_", "ALLOW_", "ENABLE_")


def is_likely_enum_column(
    column_name: str,
    data_type: str = "",
    data_length: int = 0,
    data_precision: int = 0,
) -> bool:
    """
    Return True if this column is likely to hold a small fixed set of values.

    Layered checks (any match → True):
      1. Whole-word enum name (STATUS, RISK_LEVEL, ACCOUNT_STATUS, …)
      2. Abbreviation suffix (_CD, _TYP, _LVL, _FLG, _STS, …)
      3. Boolean-flag name prefix (IS_, HAS_, CAN_, …)
      4. Short string types (CHAR ≤ 5, VARCHAR2 ≤ 15)
      5. Tiny numeric (NUMBER with precision 1..3) — flag-like
    """
    upper = column_name.upper()
    dtype = (data_type or "").upper()

    for word in _ENUM_WORDS:
        if upper == word or upper.endswith(f"_{word}") or upper.startswith(f"{word}_"):
            return True

    if "_" in upper:
        suffix = upper.rsplit("_", 1)[-1]
        if suffix in _ENUM_ABBREV_SUFFIXES:
            return True

    for prefix in _FLAG_PREFIXES:
        if upper.startswith(prefix):
            return True

    if dtype == "CHAR" and 0 < data_length <= 5:
        return True
    if dtype == "VARCHAR2" and 0 < data_length <= 15:
        return True

    if dtype == "NUMBER" and 0 < data_precision <= 3:
        return True

    return False
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_column_value_cache.py -v`

Expected: PASS on all parametrised cases.

- [ ] **Step 3.5: Run full test suite to confirm no regressions**

Run: `python -m pytest tests/ -q --ignore=tests/test_e2e.py`

Expected: All non-E2E tests still pass.

- [ ] **Step 3.6: Commit**

```bash
git add knowledge_graph/column_value_cache.py tests/test_column_value_cache.py
git commit -m "feat(value_cache): widen is_likely_enum_column heuristic

Adds three new rule families (abbreviation suffixes like _CD/_LVL/_TYP,
flag prefixes IS_/HAS_/CAN_, tiny NUMBER(1..3) columns) to catch the
column-naming patterns common in real KYC schemas where the prior
English-only word list missed most enum columns. Adds optional
data_precision parameter; existing callers using positional args still
work."
```

---

## Task 4: Heuristic marker pass on graph

**Files:**
- Create: `knowledge_graph/value_cache_builder.py`
- Modify: `tests/test_value_cache_builder.py` (extend existing file from Task 1)

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_value_cache_builder.py`:

```python
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
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `python -m pytest tests/test_value_cache_builder.py::test_mark_filter_candidates_heuristic_flags_kyc_columns -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4.3: Implement `knowledge_graph/value_cache_builder.py` (heuristic only)**

```python
"""
Value cache builder
====================
Three-step pipeline that runs after the graph is built:

  1. mark_filter_candidates_heuristic(graph)
       Cheap pass — flags Column nodes whose name/type matches enum patterns.
  2. nominate_filter_candidates_llm(graph, llm)        # added in Task 5
       LLM pass over remaining columns to catch domain-specific names the
       heuristic missed.
  3. probe_filter_candidates(graph, oracle_config)     # added in Task 6
       For every flagged column, run SELECT DISTINCT … FETCH FIRST 31 ROWS
       in parallel, populate ValueCache.

Each function is idempotent — re-running on an already-flagged graph does
not double-flag or re-probe.
"""
from __future__ import annotations

import logging
from typing import Optional

from knowledge_graph.column_value_cache import (
    _ENUM_ABBREV_SUFFIXES,
    _ENUM_WORDS,
    _FLAG_PREFIXES,
)
from knowledge_graph.graph_store import KnowledgeGraph

logger = logging.getLogger(__name__)


def mark_filter_candidates_heuristic(graph: KnowledgeGraph) -> int:
    """
    Walk every Column node and flag those that look like enum/filter columns.

    Sets two properties on each matched Column node:
      ``is_filter_candidate=True``
      ``filter_reason="heuristic:<rule>"``  rule ∈ {name_word, abbrev,
                                              flag_prefix, short_string,
                                              short_numeric}

    Idempotent: calling twice produces the same flag set and count.

    Returns
    -------
    int
        Number of columns flagged in this pass.
    """
    flagged = 0
    for col in graph.get_all_nodes("Column"):
        fqn = col.get("fqn", "")
        name = (col.get("name") or "").upper()
        dtype = (col.get("data_type") or "").upper()
        length = col.get("data_length") or 0
        precision = col.get("data_precision") or 0

        rule = _classify_column(name, dtype, length, precision)
        if rule is None:
            continue
        graph.merge_node("Column", fqn, {
            "is_filter_candidate": True,
            "filter_reason": f"heuristic:{rule}",
        })
        flagged += 1

    logger.info("Heuristic filter-candidate pass: flagged %d columns", flagged)
    return flagged


def _classify_column(name: str, dtype: str, length: int, precision: int) -> Optional[str]:
    """Return the rule name that flagged the column, or None."""
    for word in _ENUM_WORDS:
        if name == word or name.endswith(f"_{word}") or name.startswith(f"{word}_"):
            return "name_word"

    if "_" in name:
        suffix = name.rsplit("_", 1)[-1]
        if suffix in _ENUM_ABBREV_SUFFIXES:
            return "abbrev"

    for prefix in _FLAG_PREFIXES:
        if name.startswith(prefix):
            return "flag_prefix"

    if dtype == "CHAR" and 0 < length <= 5:
        return "short_string"
    if dtype == "VARCHAR2" and 0 < length <= 15:
        return "short_string"

    if dtype == "NUMBER" and 0 < precision <= 3:
        return "short_numeric"

    return None
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_value_cache_builder.py -v`

Expected: PASS for all heuristic + config tests.

- [ ] **Step 4.5: Commit**

```bash
git add knowledge_graph/value_cache_builder.py tests/test_value_cache_builder.py
git commit -m "feat(value_cache): heuristic marker pass over graph columns

mark_filter_candidates_heuristic() flags Column nodes with
is_filter_candidate=True and filter_reason='heuristic:<rule>'.
Idempotent. ~70-80% expected coverage on real KYC schemas."
```

---

## Task 5: LLM nomination pass

**Files:**
- Modify: `knowledge_graph/llm_enhancer.py` (add new top-level function)
- Test: `tests/test_llm_nominator.py`

- [ ] **Step 5.1: Write the failing test**

Create `tests/test_llm_nominator.py`:

```python
"""Tests for nominate_filter_candidates_llm — LLM pass over heuristic-missed columns."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from knowledge_graph.llm_enhancer import nominate_filter_candidates_llm
from knowledge_graph.value_cache_builder import mark_filter_candidates_heuristic


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


def _fake_llm(response_content: str):
    fake = MagicMock()
    fake.invoke = MagicMock(return_value=_FakeLLMResponse(response_content))
    return fake


def test_nominate_skips_already_flagged_columns(kyc_graph):
    mark_filter_candidates_heuristic(kyc_graph)
    seen_columns_in_prompts = []

    def capture_invoke(messages):
        for m in messages:
            seen_columns_in_prompts.append(getattr(m, "content", str(m)))
        return _FakeLLMResponse(json.dumps({"candidates": []}))

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(side_effect=capture_invoke)

    nominate_filter_candidates_llm(kyc_graph, fake_llm, batch_size=50)

    flagged_fqns = {
        col["fqn"]
        for col in kyc_graph.get_all_nodes("Column")
        if col.get("filter_reason", "").startswith("heuristic:")
    }
    full_text = "\n".join(seen_columns_in_prompts)
    for fqn in flagged_fqns:
        assert fqn not in full_text, f"Heuristic-flagged {fqn} sent to LLM"


def test_nominate_flags_llm_accepted_columns(kyc_graph):
    # Clear all flags first
    for col in kyc_graph.get_all_nodes("Column"):
        if col.get("is_filter_candidate"):
            kyc_graph.merge_node("Column", col["fqn"], {
                "is_filter_candidate": False,
                "filter_reason": None,
            })

    fake_llm = _fake_llm(json.dumps({
        "candidates": [
            {"col_fqn": "KYC.EMPLOYEES.DEPARTMENT",
             "is_filter_candidate": True,
             "confidence": "HIGH",
             "reason": "department list is small and bounded"},
        ]
    }))

    n = nominate_filter_candidates_llm(kyc_graph, fake_llm, batch_size=50)
    assert n >= 1
    node = kyc_graph.get_node("Column", "KYC.EMPLOYEES.DEPARTMENT")
    assert node.get("is_filter_candidate") is True
    assert node.get("filter_reason", "").startswith("llm:")


def test_nominate_handles_llm_error_gracefully(kyc_graph):
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(side_effect=RuntimeError("LLM down"))
    n = nominate_filter_candidates_llm(kyc_graph, fake_llm, batch_size=50)
    assert n == 0


def test_nominate_returns_zero_when_llm_is_none(kyc_graph):
    n = nominate_filter_candidates_llm(kyc_graph, None, batch_size=50)
    assert n == 0
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm_nominator.py -v`

Expected: FAIL — `ImportError: cannot import name 'nominate_filter_candidates_llm'`.

- [ ] **Step 5.3: Add `nominate_filter_candidates_llm` to `knowledge_graph/llm_enhancer.py`**

Append at the end of the file:

```python
# ---------------------------------------------------------------------------
# Filter-candidate nomination (used by Phase 1 / Layer 1)
# ---------------------------------------------------------------------------

_NOMINATION_SYSTEM_PROMPT = """You are a database schema analyst.
For each column you are shown, decide whether it is likely to be used as a
filter (WHERE col = 'value' or col IN (...)) AND has a small bounded set of
distinct values (typically <= 30 — status flags, codes, types, categories,
risk levels, currencies, country codes, etc.).

Do NOT flag:
- Free-text columns (names, descriptions, notes)
- Identifiers (IDs, account numbers, keys)
- Continuous numeric metrics (amounts, balances, scores, percentages)
- Date/time columns
- Long string columns

Output JSON ONLY, no prose, exactly:
{
  "candidates": [
    {"col_fqn": "SCHEMA.TABLE.COL",
     "is_filter_candidate": true,
     "confidence": "HIGH" | "MEDIUM" | "LOW",
     "reason": "short reason"}
  ]
}
Only include columns you flag as TRUE. Skip columns you reject."""


def nominate_filter_candidates_llm(graph, llm, batch_size: int = 50) -> int:
    """
    Ask the LLM to nominate filter-candidate columns the heuristic missed.

    Walks every Column node where ``is_filter_candidate`` is not already True,
    sends them in batches of *batch_size* to the LLM, and flags accepted ones
    with ``filter_reason="llm:<reason>"``.

    Returns
    -------
    int
        Number of new columns flagged by the LLM (excluding heuristic flags).
    """
    if llm is None:
        logger.info("LLM unavailable — skipping filter-candidate nomination.")
        return 0

    pending = []
    for col in graph.get_all_nodes("Column"):
        if col.get("is_filter_candidate"):
            continue   # already flagged by heuristic
        pending.append({
            "col_fqn": col.get("fqn", ""),
            "name": col.get("name", ""),
            "data_type": col.get("data_type", ""),
            "data_length": col.get("data_length"),
            "data_precision": col.get("data_precision"),
            "comments": col.get("comments", ""),
        })

    if not pending:
        return 0

    logger.info(
        "LLM filter-candidate nomination: %d columns in %d batches",
        len(pending), (len(pending) + batch_size - 1) // batch_size,
    )

    accepted = 0
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start:batch_start + batch_size]
        try:
            accepted += _nominate_one_batch(graph, llm, batch)
        except Exception as exc:
            logger.warning(
                "LLM nomination batch %d failed: %s — skipping",
                batch_start // batch_size, exc,
            )
    logger.info("LLM filter-candidate nomination: flagged %d new columns", accepted)
    return accepted


def _nominate_one_batch(graph, llm, batch) -> int:
    """Send one batch to the LLM and apply the results."""
    from langchain_core.messages import HumanMessage, SystemMessage

    user_lines = ["Columns to evaluate:"]
    for c in batch:
        length_part = f"({c['data_length']})" if c.get("data_length") else ""
        precision_part = f" precision={c['data_precision']}" if c.get("data_precision") else ""
        comment_part = f" -- {c['comments']}" if c.get("comments") else ""
        user_lines.append(
            f"- {c['col_fqn']} | {c['data_type']}{length_part}{precision_part}{comment_part}"
        )
    response = llm.invoke([
        SystemMessage(content=_NOMINATION_SYSTEM_PROMPT),
        HumanMessage(content="\n".join(user_lines)),
    ])
    content = getattr(response, "content", str(response))

    try:
        parsed = _extract_json_object(content)
        candidates = parsed.get("candidates", []) if parsed else []
    except Exception as exc:
        logger.warning("Failed to parse LLM nomination response: %s", exc)
        return 0

    flagged_in_batch = 0
    for cand in candidates:
        if not cand.get("is_filter_candidate"):
            continue
        fqn = cand.get("col_fqn", "")
        if not fqn or graph.get_node("Column", fqn) is None:
            continue
        reason = (cand.get("reason") or cand.get("confidence") or "nominated")[:80]
        graph.merge_node("Column", fqn, {
            "is_filter_candidate": True,
            "filter_reason": f"llm:{reason}",
        })
        flagged_in_batch += 1
    return flagged_in_batch


def _extract_json_object(text: str):
    """Extract the first {...} JSON object from text. Handles markdown fences."""
    import json
    import re
    cleaned = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE).replace("```", "")
    start = cleaned.find("{")
    if start == -1:
        return None
    depth, end = 0, -1
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    return json.loads(cleaned[start:end + 1])
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_llm_nominator.py -v`

Expected: PASS for all four tests.

- [ ] **Step 5.5: Run full suite (no regressions)**

Run: `python -m pytest tests/ -q --ignore=tests/test_e2e.py`

Expected: All non-E2E tests pass.

- [ ] **Step 5.6: Commit**

```bash
git add knowledge_graph/llm_enhancer.py tests/test_llm_nominator.py
git commit -m "feat(value_cache): add LLM nomination pass for filter candidates

Calls the LLM with batches of 50 columns the heuristic did not flag.
Accepted columns get is_filter_candidate=true, filter_reason='llm:...'.
Each batch failure is logged but does not abort the whole run."
```

---

## Task 6: DISTINCT probe pass

**Files:**
- Modify: `knowledge_graph/value_cache_builder.py` (add `probe_filter_candidates`)
- Modify: `tests/test_value_cache_builder.py` (extend)

- [ ] **Step 6.1: Write the failing test**

Append to `tests/test_value_cache_builder.py`:

```python
from unittest.mock import patch

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
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `python -m pytest tests/test_value_cache_builder.py::test_probe_filter_candidates_populates_cache -v`

Expected: FAIL — `ImportError: cannot import name 'probe_filter_candidates'`.

- [ ] **Step 6.3: Add `probe_filter_candidates` to `knowledge_graph/value_cache_builder.py`**

Append at the bottom of the file:

```python
# ---------------------------------------------------------------------------
# DISTINCT probe (parallel) — produces ValueCache
# ---------------------------------------------------------------------------

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from knowledge_graph.value_cache import ValueCache, ValueCacheEntry

try:
    import oracledb     # type: ignore
except Exception:        # pragma: no cover
    oracledb = None       # type: ignore


def probe_filter_candidates(
    graph: KnowledgeGraph,
    config,
    max_workers: int = 8,
) -> ValueCache:
    """
    For every Column flagged ``is_filter_candidate=True``, run
    ``SELECT DISTINCT col FROM schema.table FETCH FIRST max+1 ROWS ONLY``
    in parallel and collect into a ValueCache.

    Parameters
    ----------
    graph
        The populated knowledge graph.
    config
        Object exposing an ``oracle`` attribute (.dsn, .user, .password) and
        optionally ``value_cache`` (.max_values, .probe_timeout_ms).
    max_workers
        Concurrent DISTINCT queries (default 8).

    Returns
    -------
    ValueCache
        Always returns an instance, even on partial failure.
    """
    cache = ValueCache()
    vc_cfg = getattr(config, "value_cache", None)
    max_values = getattr(vc_cfg, "max_values", 30) if vc_cfg else 30
    timeout_ms = getattr(vc_cfg, "probe_timeout_ms", 5000) if vc_cfg else 5000

    targets = _collect_targets(graph)
    if not targets:
        logger.info("No filter-candidate columns to probe.")
        return cache
    if oracledb is None:
        logger.warning("oracledb not installed — cannot probe values.")
        return cache

    logger.info(
        "DISTINCT probe: %d columns, %d workers, max=%d, timeout=%dms",
        len(targets), max_workers, max_values, timeout_ms,
    )
    started = time.monotonic()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_target = {
            pool.submit(_probe_one, schema, table, col, config, max_values, timeout_ms):
                (schema, table, col)
            for (schema, table, col) in targets
        }
        for future in as_completed(future_to_target):
            schema, table, col = future_to_target[future]
            try:
                entry = future.result()
            except Exception as exc:
                entry = ValueCacheEntry(values=[], error=str(exc))
            cache.set(schema, table, col, entry)

    elapsed = time.monotonic() - started
    stats = cache.stats()
    logger.info(
        "DISTINCT probe complete in %.1fs: %d ok, %d too_many, %d errors",
        elapsed, stats["ok"], stats["too_many"], stats["errors"],
    )
    return cache


def _collect_targets(graph: KnowledgeGraph) -> List[Tuple[str, str, str]]:
    out = []
    for col in graph.get_all_nodes("Column"):
        if not col.get("is_filter_candidate"):
            continue
        schema = (col.get("schema") or "").upper()
        table = (col.get("table_name") or "").upper()
        name = (col.get("name") or "").upper()
        if not (schema and table and name):
            continue
        out.append((schema, table, name))
    return out


def _probe_one(
    schema: str,
    table: str,
    column: str,
    config,
    max_values: int,
    timeout_ms: int,
) -> ValueCacheEntry:
    cfg = getattr(config, "oracle", config)
    col_q = f'"{column}"'
    tbl_q = f'"{schema}"."{table}"'
    sql = (
        f"SELECT DISTINCT {col_q} FROM {tbl_q} "
        f"WHERE {col_q} IS NOT NULL "
        f"ORDER BY 1 "
        f"FETCH FIRST {max_values + 1} ROWS ONLY"
    )
    try:
        conn = oracledb.connect(user=cfg.user, password=cfg.password, dsn=cfg.dsn)
        try:
            conn.callTimeout = timeout_ms
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        return ValueCacheEntry(values=[], error=str(exc))

    if len(rows) > max_values:
        return ValueCacheEntry(values=[], too_many=True)
    values = [str(r[0]) for r in rows if r[0] is not None]
    return ValueCacheEntry(values=values)
```

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_value_cache_builder.py -v`

Expected: PASS for all probe tests + earlier tests in the file.

- [ ] **Step 6.5: Commit**

```bash
git add knowledge_graph/value_cache_builder.py tests/test_value_cache_builder.py
git commit -m "feat(value_cache): parallel DISTINCT probe over flagged columns

probe_filter_candidates() runs SELECT DISTINCT … FETCH FIRST max+1 ROWS
across max_workers threads; populates a ValueCache. Per-probe timeout,
too_many cap, and per-column error capture so a single bad probe never
fails the whole pass."
```

---

## Task 7: Wire value cache into init_graph + persistence

This is the integration step. Three sub-tasks: each commits independently.

### 7a — Make `initialize_graph()` build & return the ValueCache

- [ ] **Step 7a.1: Write the failing test**

Append to `tests/test_value_cache_builder.py`:

```python
def test_initialize_graph_returns_tuple_with_value_cache(monkeypatch):
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
```

- [ ] **Step 7a.2: Run test to verify it fails**

Run: `python -m pytest tests/test_value_cache_builder.py::test_initialize_graph_returns_tuple_with_value_cache -v`

Expected: FAIL — `initialize_graph` returns a 2-tuple.

- [ ] **Step 7a.3: Modify `initialize_graph()` to return a 3-tuple**

In `knowledge_graph/init_graph.py`:

1. Add import near other imports: `from knowledge_graph.value_cache import ValueCache`
2. Update return-type annotation in the signature (line 98): change `-> Tuple[KnowledgeGraph, Dict[str, Any]]` to `-> Tuple[KnowledgeGraph, Dict[str, Any], "ValueCache"]`. Update the docstring's "Returns" block to document the third element: `value_cache : ValueCache — distinct values for filter-candidate columns. Empty when value-caching is disabled or Oracle is unreachable.`
3. Change the early-exit return at line 134 (Oracle health check fail): `return KnowledgeGraph(), report, ValueCache()`
4. Change the early-exit return at line 147 (extraction fail): `return KnowledgeGraph(), report, ValueCache()`
5. Change the early-exit return at line 176 (build fail): `return graph, report, ValueCache()`
6. Insert a new step before the final `return graph, report` (around line 207). Replace lines 207–228 with:

```python
    # ------------------------------------------------------------------
    # Step 6: Build the column-value cache (Layer 1 / Phase 1)
    # ------------------------------------------------------------------
    value_cache = ValueCache()
    vc_cfg = getattr(config, "value_cache", None)
    if vc_cfg is None or vc_cfg.enabled:
        try:
            from knowledge_graph.value_cache_builder import (
                mark_filter_candidates_heuristic,
                probe_filter_candidates,
            )
            from knowledge_graph.llm_enhancer import nominate_filter_candidates_llm

            n_heur = mark_filter_candidates_heuristic(graph)
            logger.info("Heuristic flagged %d filter-candidate columns", n_heur)

            if vc_cfg is None or vc_cfg.llm_nominate:
                try:
                    from agent.llm import get_llm
                    from app_config import AppConfig
                    llm = get_llm(AppConfig())
                    n_llm = nominate_filter_candidates_llm(
                        graph, llm,
                        batch_size=getattr(vc_cfg, "llm_batch_size", 50),
                    )
                    logger.info("LLM nominated %d additional filter-candidate columns", n_llm)
                except Exception as exc:
                    logger.warning("LLM nomination skipped: %s", exc)

            value_cache = probe_filter_candidates(
                graph, config,
                max_workers=getattr(vc_cfg, "probe_workers", 8),
            )
        except Exception as exc:
            logger.warning("Value cache build failed (graph still usable): %s", exc)

    total_elapsed = time.monotonic() - start_time
    report["elapsed_seconds"] = round(total_elapsed, 1)
    report["value_cache_stats"] = value_cache.stats() if value_cache else {}
    report["success"] = True

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info("=== Initialization complete in %.1fs ===", total_elapsed)
    logger.info("  Extracted: %d tables, %d columns, %d FK relationships",
                len(metadata.tables), len(metadata.columns), len(metadata.foreign_keys))
    logger.info("  Graph nodes: Schema=%d, Table=%d, Column=%d, View=%d",
                build_stats.get("schemas", 0), build_stats.get("tables", 0),
                build_stats.get("columns", 0), build_stats.get("views", 0))
    logger.info("  Graph edges: FK=%d, JOIN_PATH=%d, SIMILAR_TO=%d",
                build_stats.get("foreign_keys", 0), build_stats.get("join_paths", 0),
                build_stats.get("similar_to", 0))
    logger.info("  Business terms: %d terms, %d mappings",
                glossary_stats.get("terms", 0), glossary_stats.get("mappings", 0))
    logger.info("  Value cache: %s", value_cache.stats() if value_cache else "n/a")
    logger.info("  Validation: %s",
                "PASSED" if report["validation_passed"] else "FAILED")

    return graph, report, value_cache
```

7. Update the `__main__` block (lines 254–265):

```python
if __name__ == "__main__":
    args = _parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    _graph, _report, _value_cache = initialize_graph(refresh_only=args.refresh_only)

    if not _report["success"]:
        logger.error("Graph initialization FAILED. See logs above for details.")
        sys.exit(1)

    logger.info("Graph initialization SUCCEEDED. Value cache: %s", _value_cache.stats())
    sys.exit(0)
```

- [ ] **Step 7a.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_value_cache_builder.py::test_initialize_graph_returns_tuple_with_value_cache -v`

Expected: PASS.

- [ ] **Step 7a.5: Find and migrate every caller of `initialize_graph()`**

Run:
```bash
grep -rn "initialize_graph(" --include="*.py" .
```

For every match outside `knowledge_graph/init_graph.py` itself and outside this plan's tests, update the unpacking from `graph, report = ...` to `graph, report, value_cache = ...` (use `_` for unused). Likely files: `app.py`, `backend/main.py`, possibly some scripts under `scripts/`.

- [ ] **Step 7a.6: Run full test suite**

Run: `python -m pytest tests/ -q --ignore=tests/test_e2e.py`

Expected: All non-E2E tests pass.

- [ ] **Step 7a.7: Commit**

```bash
git add knowledge_graph/init_graph.py tests/test_value_cache_builder.py app.py backend/main.py
# Add any other files touched in 7a.5
git commit -m "feat(value_cache): build ValueCache as part of initialize_graph

initialize_graph() now returns (graph, report, value_cache). After the
graph build it runs heuristic + (optional) LLM nomination + parallel
DISTINCT probe. Each step is wrapped in try/except so a probe failure
never breaks graph initialization."
```

### 7b — Persist & re-load the ValueCache from `app.py`

- [ ] **Step 7b.1: Update `_GraphBundle`**

In `app.py` find `_GraphBundle` (around line 234). Replace:

```python
class _GraphBundle:
    """Mutable shared bundle: graph + LLM-enhancement flag + value cache."""
    __slots__ = ("graph", "llm_enhanced", "value_cache")

    def __init__(self, graph, llm_enhanced: bool = False, value_cache=None) -> None:
        self.graph = graph
        self.llm_enhanced = llm_enhanced
        self.value_cache = value_cache
```

- [ ] **Step 7b.2: Update `get_knowledge_graph()` to load/save value cache and inject runtime singleton**

Locate the function (around line 249). Replace the body — preserving the `@st.cache_resource` decorator and signature — with:

```python
@st.cache_resource
def get_knowledge_graph():
    """Build (or load from disk) the knowledge graph + value cache once per process."""
    config = _get_app_config()
    from knowledge_graph.graph_cache import (
        get_cache_path, load_graph, save_graph,
    )
    from knowledge_graph.value_cache import (
        get_value_cache_path, load_value_cache, save_value_cache,
    )
    from knowledge_graph.column_value_cache import set_loaded_value_cache

    ttl_hours = float(os.getenv("GRAPH_CACHE_TTL_HOURS", "0") or "0")
    cache_path = get_cache_path(config)
    value_cache_path = get_value_cache_path(config)

    cached = load_graph(cache_path, max_age_hours=ttl_hours)
    if cached is not None:
        graph, llm_enhanced = cached
        value_cache = load_value_cache(value_cache_path)
        if value_cache is not None:
            set_loaded_value_cache(value_cache)
        return _GraphBundle(graph, llm_enhanced, value_cache)

    # Cache miss → build from Oracle
    from knowledge_graph.init_graph import initialize_graph
    graph, _report, value_cache = initialize_graph(config.graph)

    save_graph(graph, cache_path, llm_enhanced=False)
    if value_cache is not None and len(value_cache) > 0:
        save_value_cache(value_cache, value_cache_path)
        set_loaded_value_cache(value_cache)
    return _GraphBundle(graph, llm_enhanced=False, value_cache=value_cache)
```

- [ ] **Step 7b.3: Smoke-test app boot (no automated test — manual)**

Run:
```bash
python -c "from app import get_knowledge_graph; b = get_knowledge_graph(); print(type(b).__name__, len(b.value_cache or []))"
```

(Expect a `_GraphBundle` print; cache size depends on whether Oracle is reachable.)

- [ ] **Step 7b.4: Commit**

```bash
git add app.py
git commit -m "feat(value_cache): persist+reload ValueCache alongside graph cache

_GraphBundle gains a value_cache field. get_knowledge_graph() saves the
cache to JSON next to the graph pickle on first build, loads it on
subsequent restarts, and injects it into the runtime singleton via
set_loaded_value_cache so DDL annotation hits the in-memory dict."
```

### 7c — Mirror the same wiring in the FastAPI backend

- [ ] **Step 7c.1: Locate the backend graph-load path**

Run:
```bash
grep -n "initialize_graph\|load_graph\|get_cache_path\|column_value_cache" backend/main.py
```

- [ ] **Step 7c.2: Update each callsite**

For every place that constructs/loads the graph in `backend/main.py`:

1. If it calls `initialize_graph(...)`, capture the third tuple element as `value_cache`.
2. If it calls `load_graph(...)`, also call `load_value_cache(get_value_cache_path(config))` and `set_loaded_value_cache(...)`.

Concretely: anywhere the backend builds/loads the graph at startup, add adjacent:

```python
from knowledge_graph.value_cache import load_value_cache, get_value_cache_path
from knowledge_graph.column_value_cache import set_loaded_value_cache
_vc = load_value_cache(get_value_cache_path(config))
if _vc is not None:
    set_loaded_value_cache(_vc)
```

(Adapt names to the actual variable used for `config` in that file.)

- [ ] **Step 7c.3: Commit**

```bash
git add backend/main.py
git commit -m "feat(value_cache): load ValueCache in FastAPI lifespan

Backend now loads the disk-persisted value cache during startup and
injects it into the runtime singleton, matching app.py behaviour. Both
Streamlit and FastAPI entry points now serve grounded values to the SQL
generator."
```

---

## Task 8: Route `column_value_cache.get_distinct_values` through the loaded ValueCache

**Files:**
- Modify: `knowledge_graph/column_value_cache.py`
- Test: append to `tests/test_column_value_cache.py`

- [ ] **Step 8.1: Write the failing test**

Append to `tests/test_column_value_cache.py`:

```python
from knowledge_graph.column_value_cache import (
    get_distinct_values,
    set_loaded_value_cache,
    invalidate_cache as invalidate_runtime_cache,
)
from knowledge_graph.value_cache import ValueCache, ValueCacheEntry


def test_get_distinct_values_prefers_loaded_cache():
    invalidate_runtime_cache()
    loaded = ValueCache()
    loaded.set("KYC", "ACCOUNTS", "STATUS",
               ValueCacheEntry(values=["ACTIVE", "DORMANT"]))
    set_loaded_value_cache(loaded)

    # No Oracle import required — we hit the loaded cache.
    result = get_distinct_values("KYC", "ACCOUNTS", "STATUS", config=None)
    assert result == ["ACTIVE", "DORMANT"]


def test_get_distinct_values_too_many_returns_empty():
    invalidate_runtime_cache()
    loaded = ValueCache()
    loaded.set("KYC", "ACCOUNTS", "BIG_COL",
               ValueCacheEntry(values=[], too_many=True))
    set_loaded_value_cache(loaded)

    assert get_distinct_values("KYC", "ACCOUNTS", "BIG_COL", config=None) == []


def test_get_distinct_values_error_returns_empty():
    invalidate_runtime_cache()
    loaded = ValueCache()
    loaded.set("KYC", "ACCOUNTS", "ERR_COL",
               ValueCacheEntry(values=[], error="ORA-00942"))
    set_loaded_value_cache(loaded)

    assert get_distinct_values("KYC", "ACCOUNTS", "ERR_COL", config=None) == []
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `python -m pytest tests/test_column_value_cache.py::test_get_distinct_values_prefers_loaded_cache -v`

Expected: FAIL — `set_loaded_value_cache` does not exist.

- [ ] **Step 8.3: Update `knowledge_graph/column_value_cache.py`**

Replace the module-level `_cache` declaration block (around line 36) with:

```python
# In-process lazy cache: (SCHEMA, TABLE, COLUMN) → list of string values
# (or [] = "skip — too many or fetch failed"). Used only when the disk-loaded
# cache has no entry for this column.
_cache: Dict[Tuple[str, str, str], List[str]] = {}

# Disk-loaded value cache, set once at app/process start. None means
# "no Phase 1 cache available — fall back to live Oracle probe".
_loaded_cache = None


def set_loaded_value_cache(cache) -> None:
    """Inject the disk-loaded ValueCache so lookups hit it before Oracle."""
    global _loaded_cache
    _loaded_cache = cache
```

Replace the body of `get_distinct_values` (line 58 onward) with:

```python
def get_distinct_values(
    schema: str,
    table: str,
    column: str,
    config,
    max_values: int = MAX_DISTINCT_VALUES,
) -> List[str]:
    """
    Return distinct non-null values for ``schema.table.column``.

    Lookup order:
      1. Disk-loaded ValueCache (populated by Phase 1 at graph build)
      2. Process-local in-memory cache (lazy probes within this run)
      3. Live Oracle DISTINCT probe (fallback; result cached in memory)
    """
    key = (schema.upper(), table.upper(), column.upper())

    if _loaded_cache is not None:
        entry = _loaded_cache.get(schema, table, column)
        if entry is not None:
            if entry.too_many or entry.error:
                return []
            return list(entry.values)

    if key in _cache:
        return _cache[key]

    try:
        import oracledb  # type: ignore

        cfg = getattr(config, "oracle", config)
        col_q = f'"{column.upper()}"'
        tbl_q = f'"{schema.upper()}"."{table.upper()}"'
        sql = (
            f"SELECT DISTINCT {col_q} FROM {tbl_q} "
            f"WHERE {col_q} IS NOT NULL "
            f"ORDER BY 1 "
            f"FETCH FIRST {max_values + 1} ROWS ONLY"
        )
        conn = oracledb.connect(user=cfg.user, password=cfg.password, dsn=cfg.dsn)
        conn.callTimeout = 5_000
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        conn.close()

        if len(rows) > max_values:
            _cache[key] = []
            return []

        values = [str(r[0]) for r in rows if r[0] is not None]
        _cache[key] = values
        return values
    except Exception as exc:
        logger.debug("column_value_cache: skipping %s.%s.%s — %s", schema, table, column, exc)
        _cache[key] = []
        return []
```

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `python -m pytest tests/test_column_value_cache.py -v`

Expected: PASS for all tests in the file.

- [ ] **Step 8.5: Run full test suite**

Run: `python -m pytest tests/ -q --ignore=tests/test_e2e.py`

Expected: PASS.

- [ ] **Step 8.6: Commit**

```bash
git add knowledge_graph/column_value_cache.py tests/test_column_value_cache.py
git commit -m "feat(value_cache): route get_distinct_values through disk-loaded cache

set_loaded_value_cache() injects the Phase 1 cache; lookups now check it
before falling back to in-process memo or live Oracle probe. DDL
annotation and the entity-extractor's get_column_values tool both serve
from the precomputed cache instead of hitting Oracle on every query."
```

---

## Task 9: SQL generator prompt rules 17–19

**Files:**
- Modify: `prompts/sql_generator_system.txt`
- Modify: `agent/nodes/sql_generator.py:25-71` (the `_SYSTEM_PROMPT` fallback constant)

- [ ] **Step 9.1: Update `prompts/sql_generator_system.txt`**

After existing rule 16 and before "OUTPUT FORMAT", insert:

```
17. VALUE GROUNDING — read the column annotations carefully.
    When a column has a `-- Values(N): 'A', 'B', ...` annotation in the DDL,
    that annotation is the EXACT, COMPLETE list of values stored in the
    database for that column. You MUST:
    - Use one of the listed values verbatim (case-sensitive) when filtering
      that column. Never invent, translate, or normalize the value.
      Example: if the DDL says `-- Values(3): 'A', 'I', 'P'` for STATUS,
      write `WHERE c.STATUS = 'A'` — never `'ACTIVE'`.
    - If the user's intent maps to multiple listed values, use IN (...) with
      all matching values.
    - If the user's intent does not obviously match any listed value, prefer
      to flag the ambiguity rather than guess.

18. UNANNOTATED COLUMNS — when a column has no `-- Values(...)` annotation,
    it is either high-cardinality (names, IDs, free text) or was not flagged
    as filter-relevant. Do NOT assume any specific literal. Use the user's
    quoted string verbatim, or use LIKE for partial matches, but flag this
    case in the ambiguity block.

19. CASE SENSITIVITY — Oracle string comparisons are case-sensitive by
    default. Always preserve the exact case from the `-- Values(...)`
    annotation. If the listed values are upper-case, write upper-case in the
    WHERE clause.
```

- [ ] **Step 9.2: Update the fallback `_SYSTEM_PROMPT` in `agent/nodes/sql_generator.py`**

Update the `_SYSTEM_PROMPT` constant (lines 25–71) to include the same rules 17–19 verbatim. Insert before the `OUTPUT FORMAT` block.

- [ ] **Step 9.3: Sanity check — run existing SQL-generator tests**

Run: `python -m pytest tests/test_sql_generator_ambiguity.py tests/test_sql_generator_refinement.py -v`

Expected: PASS — these tests should still pass with the new rules appended.

- [ ] **Step 9.4: Commit**

```bash
git add prompts/sql_generator_system.txt agent/nodes/sql_generator.py
git commit -m "feat(sql_generator): require verbatim use of DDL Values(...) annotations

Adds rules 17-19: when a column has a '-- Values(N): ...' annotation,
the LLM MUST pick a listed value verbatim (case-sensitive) rather than
inventing a literal. Closes the last gap in Phase 1 — without this rule
the LLM ignored the annotations that context_builder already inserts."
```

---

## Task 10: End-to-end smoke test (gated behind ORACLE_DSN)

**Files:**
- Create: `tests/test_e2e_value_grounding.py`

- [ ] **Step 10.1: Add the e2e test**

```python
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
    from knowledge_graph.column_value_cache import set_loaded_value_cache, make_value_getter
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
```

- [ ] **Step 10.2: Run the smoke test**

```bash
ORACLE_DSN=localhost:1521/FREEPDB1 ORACLE_USER=kyc ORACLE_PASSWORD=KycPassword1 ORACLE_SCHEMA=KYC \
  python -m pytest tests/test_e2e_value_grounding.py -v
```

Expected: PASS (or SKIPPED if env vars not set).

- [ ] **Step 10.3: Commit**

```bash
git add tests/test_e2e_value_grounding.py
git commit -m "test(e2e): smoke test for Phase 1 value grounding

Verifies end-to-end that initialize_graph() populates a ValueCache from
live Oracle and that the DDL emitted to the SQL generator carries
'-- Values(...)' annotations for at least one column. Skipped when
ORACLE_DSN is not set."
```

---

## Self-Review Checklist

- [ ] **Spec coverage** — design Sections 2 (Layer 1) and 3.1 (prompt rules 17–19) are both in this plan. Sections 3.2 (retry-hint feed-in), 3.3 (UI annotations), Section 4 (Layer 3 validator), Section 5.2 (UI panel) are explicitly **out of scope** — they get their own plans (P2/P3).
- [ ] **Placeholder scan** — there are no "TBD" / "implement later" / "add appropriate handling" tokens in this document.
- [ ] **Type consistency** — `ValueCache.get/set` signature `(schema, table, column)`, `ValueCacheEntry(values, too_many, error, probed_at)`, `mark_filter_candidates_heuristic(graph) -> int`, `nominate_filter_candidates_llm(graph, llm, batch_size=50) -> int`, `probe_filter_candidates(graph, config, max_workers=8) -> ValueCache`, `initialize_graph(...) -> (graph, report, value_cache)` are all consistent across tasks.
- [ ] **Backward compatibility** — `is_likely_enum_column` accepts an optional `data_precision` parameter so existing callers (traversal.py:340, entity_extractor.py:319) keep working unchanged.
- [ ] **Caller migration** — Task 7a.5 explicitly searches for and migrates every call site of `initialize_graph()` since its return tuple grew.
- [ ] **Idempotency** — `mark_filter_candidates_heuristic` and `nominate_filter_candidates_llm` are both safe to re-run.
- [ ] **Failure isolation** — every step that calls Oracle or the LLM is wrapped in try/except so a single bad column never blocks graph initialization.
- [ ] **Persistence** — value cache survives a backend restart via `values_<hash>.json` next to `graph_<hash>.pkl`. Same hash key, so they invalidate together.
- [ ] **Security** — value cache is JSON not pickle; deserialising a tampered file cannot execute code.

---

## Total scope estimate

- 10 tasks, ~18 commits
- ~700 lines of new production code, ~600 lines of test code
- Ready to ship behind `VALUE_CACHE_ENABLED` env var (defaults to true; `false` reverts behaviour without code changes).
