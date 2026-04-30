"""
Column Value Cache
==================
Lazy, in-memory cache of distinct values for low-cardinality ("enum-like") columns.

Used to annotate DDL context with actual DB values so agents generate correct
WHERE clauses — bridging semantic intent ("active customers") to the real stored
value ("ACTIVE", "Y", "1", etc.).

Design
------
- First call for a (schema, table, column) triple hits Oracle once; result cached in process memory.
- Columns with more than `MAX_DISTINCT_VALUES` distinct values are skipped (not enums).
- A cached empty list means "too many values or fetch failed — skip annotation".
- `invalidate_cache()` clears the cache (useful after a graph rebuild).
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Maximum distinct values to consider a column "enum-like"
MAX_DISTINCT_VALUES = 30

# Whole-word enum names (case-insensitive). Anchored: matches NAME, NAME_*,
# *_NAME, but not arbitrary substrings.
_ENUM_WORDS = {
    "STATUS", "TYPE", "FLAG", "CODE", "CATEGORY", "LEVEL", "TIER",
    "CLASS", "STATE", "REASON", "KIND", "MODE", "PRIORITY", "GENDER",
    "STAGE", "PHASE", "RATING", "INDICATOR", "ACTIVE", "ENABLED",
    "CURRENCY", "COUNTRY", "GRADE", "BUCKET", "SEGMENT",
    "ROLE", "METHOD", "CHANNEL", "SOURCE", "SCOPE", "RELATIONSHIP",
}
# NOTE: bare "RISK" is intentionally NOT in this list — it produces too
# many false positives (RISK_SCORE is a NUMBER metric, not an enum).
# RISK_RATING / RISK_LEVEL / RISK_GRADE etc. are still flagged via the
# trailing word (RATING / LEVEL / GRADE).

# Short suffix abbreviations common in KYC/financial schemas (Oracle uppercase).
# Matched as the trailing token after an underscore, e.g. ACCT_TYP, RSK_LVL.
_ENUM_ABBREV_SUFFIXES = {
    "CD", "TYP", "FLG", "STS", "CAT", "LVL", "RSK", "RSN",
    "IND", "PRI", "GRP", "TY", "CTGY", "SEG",
}

# Boolean-flag prefixes — usually NUMBER(1) or CHAR(1).
_FLAG_PREFIXES = ("IS_", "HAS_", "CAN_", "ALLOW_", "ENABLE_")

# In-process cache: (SCHEMA, TABLE, COLUMN) → list of string values (or [] = skip)
_cache: Dict[Tuple[str, str, str], List[str]] = {}

# Disk-loaded value cache, set once at app/process start. None means
# "no Phase 1 cache available — fall back to live Oracle probe".
_loaded_cache = None


def set_loaded_value_cache(cache) -> None:
    """Inject the disk-loaded ValueCache so lookups hit it before Oracle.

    Wired in Task 8 — for now the function only stores the reference; the
    actual lookup integration into ``get_distinct_values`` happens there.
    """
    global _loaded_cache
    _loaded_cache = cache


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


def get_distinct_values(
    schema: str,
    table: str,
    column: str,
    config,
    max_values: int = MAX_DISTINCT_VALUES,
) -> List[str]:
    """
    Return the distinct non-null values for ``schema.table.column``.

    Results are cached in process memory on the first successful fetch.
    Returns an empty list when:
    - the column has more than ``max_values`` distinct values (not an enum)
    - Oracle is unreachable
    - any other error occurs
    """
    key = (schema.upper(), table.upper(), column.upper())
    if key in _cache:
        return _cache[key]

    try:
        import oracledb  # type: ignore

        cfg = getattr(config, "oracle", config)
        col_q   = f'"{column.upper()}"'
        tbl_q   = f'"{schema.upper()}"."{table.upper()}"'
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
            # Column is not an enum — too many distinct values; cache as empty to skip
            _cache[key] = []
            return []

        values = [str(r[0]) for r in rows if r[0] is not None]
        _cache[key] = values
        logger.debug(
            "column_value_cache: %s.%s.%s → %d values",
            schema, table, column, len(values),
        )
        return values

    except Exception as exc:
        logger.debug(
            "column_value_cache: skipping %s.%s.%s — %s",
            schema, table, column, exc,
        )
        _cache[key] = []  # don't retry on failures
        return []


def make_value_getter(config) -> Callable[[str, str, str], List[str]]:
    """
    Return a ``(schema, table, column) → [values]`` closure bound to *config*.

    Suitable for injection into :func:`~knowledge_graph.traversal.serialize_context_to_ddl`.
    """
    def get_values(schema: str, table: str, column: str) -> List[str]:
        return get_distinct_values(schema, table, column, config)
    return get_values


def invalidate_cache() -> None:
    """Clear the in-memory value cache (call after a graph rebuild)."""
    _cache.clear()
