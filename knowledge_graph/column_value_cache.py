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

# Column name substrings (case-insensitive) that suggest a small value set
_ENUM_WORDS = {
    "STATUS", "TYPE", "FLAG", "CODE", "CATEGORY", "LEVEL", "TIER",
    "CLASS", "STATE", "REASON", "KIND", "MODE", "PRIORITY", "GENDER",
    "STAGE", "PHASE", "RATING", "INDICATOR", "ACTIVE", "ENABLED",
    "RISK", "CURRENCY", "COUNTRY",
}

# In-process cache: (SCHEMA, TABLE, COLUMN) → list of string values (or [] = skip)
_cache: Dict[Tuple[str, str, str], List[str]] = {}


def is_likely_enum_column(
    column_name: str,
    data_type: str = "",
    data_length: int = 0,
) -> bool:
    """Return True if this column is likely to hold a small fixed set of values."""
    upper = column_name.upper()
    # Match whole-word occurrence: STATUS, ACCOUNT_STATUS, STATUS_CODE, etc.
    for word in _ENUM_WORDS:
        if upper == word or upper.endswith(f"_{word}") or upper.startswith(f"{word}_"):
            return True
    # Short fixed-char types are almost always Y/N flags or codes
    if data_type == "CHAR" and 0 < data_length <= 5:
        return True
    if data_type == "VARCHAR2" and 0 < data_length <= 15:
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
