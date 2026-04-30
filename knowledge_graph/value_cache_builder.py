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
