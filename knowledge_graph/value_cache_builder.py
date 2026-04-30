"""
Value cache builder
====================
Three-step pipeline that runs after the graph is built:

  1. mark_filter_candidates_heuristic(graph)
       Cheap pass — flags Column nodes whose name/type matches enum patterns.
  2. nominate_filter_candidates_llm(graph, llm)
       LLM pass over remaining columns to catch domain-specific names the
       heuristic missed.
  3. probe_filter_candidates(graph, oracle_config)
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
