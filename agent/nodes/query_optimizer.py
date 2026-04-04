"""
Query Optimizer Node
=====================
Applies rule-based transformations to the validated Oracle SQL before execution.

Optimization rules applied in order:
  1. Strip trailing semicolons (Oracle JDBC/oracledb driver handles this)
  2. Inject FETCH FIRST N ROWS ONLY if no row limit is present
  3. Note available indexes from schema_context as SQL comments
  4. Copy final SQL to state["optimized_sql"]

No semantic rewriting is performed — only safe structural additions.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, List

from agent.state import AgentState
from agent.trace import TraceStep

logger = logging.getLogger(__name__)

# Default row cap when no limit is present in the generated SQL
_DEFAULT_ROW_LIMIT = 10000

# Patterns that indicate a row limit is already present
_LIMIT_PATTERNS = [
    re.compile(r"\bFETCH\s+FIRST\b", re.IGNORECASE),
    re.compile(r"\bROWNUM\s*[<=]", re.IGNORECASE),
    re.compile(r"\bROW_NUMBER\s*\(\s*\)\s+OVER\b", re.IGNORECASE),
]


def make_query_optimizer() -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that applies rule-based SQL optimizations.

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def optimize_sql(state: AgentState) -> AgentState:
        sql = state.get("generated_sql", "").strip()
        schema_context = state.get("schema_context", "")
        applied_rules: List[str] = []
        _trace = list(state.get("_trace", []))
        trace = TraceStep("optimize_sql", "optimizing")

        # ------------------------------------------------------------------ #
        # Rule 1: Strip trailing semicolons
        # ------------------------------------------------------------------ #
        if sql.endswith(";"):
            sql = sql.rstrip(";").rstrip()
            applied_rules.append("Removed trailing semicolon")

        # ------------------------------------------------------------------ #
        # Rule 2: Inject FETCH FIRST if no row limit present
        # ------------------------------------------------------------------ #
        has_limit = any(pat.search(sql) for pat in _LIMIT_PATTERNS)
        if not has_limit:
            sql = f"{sql}\nFETCH FIRST {_DEFAULT_ROW_LIMIT} ROWS ONLY"
            applied_rules.append(f"Added FETCH FIRST {_DEFAULT_ROW_LIMIT} ROWS ONLY")

        # ------------------------------------------------------------------ #
        # Rule 3: Inject index hint comments
        # ------------------------------------------------------------------ #
        index_hints = _extract_index_hints(schema_context, sql)
        if index_hints:
            hint_comment = "-- Available indexes: " + ", ".join(index_hints)
            sql = hint_comment + "\n" + sql
            applied_rules.append(f"Noted {len(index_hints)} available index(es) in comment")

        # ------------------------------------------------------------------ #
        # Rule 4: Add optimizer hint for large table queries
        # ------------------------------------------------------------------ #
        # If the query touches the TRANSACTIONS table (large: 5M rows), suggest index hint
        if "KYC.TRANSACTIONS" in sql.upper() or "TRANSACTIONS T" in sql.upper():
            if "/*+" not in sql:
                # Inject after SELECT keyword
                sql = re.sub(
                    r"\bSELECT\b",
                    "SELECT /*+ INDEX(T IDX_TXN_DATE) */",
                    sql,
                    count=1,
                    flags=re.IGNORECASE,
                )
                applied_rules.append(
                    "Added optimizer hint for TRANSACTIONS.IDX_TXN_DATE"
                )

        if applied_rules:
            logger.info("Query optimizer applied: %s", applied_rules)
            logger.debug("Query optimizer applied: %s", applied_rules)
        else:
            logger.debug("Query optimizer: no rules applied")

        trace.output_summary = {"applied_rules": applied_rules, "sql_preview": sql[:200]}
        _trace.append(trace.finish().to_dict())

        return {
            **state,
            "optimized_sql": sql,
            "step": "sql_optimized",
            "_trace": _trace,
        }

    return optimize_sql


def _extract_index_hints(schema_context: str, sql: str) -> List[str]:
    """
    Scan schema_context for INDEX definitions that match tables in the SQL.
    Returns a list of index names likely to be useful for this query.
    """
    if not schema_context:
        return []

    # Find all index names from DDL comments: "-- INDEX IDX_NAME ON TABLE(COL)"
    index_pattern = re.compile(
        r"--\s+(?:UNIQUE\s+)?INDEX\s+(\S+)\s+ON\s+(\S+)\(([^)]+)\)",
        re.IGNORECASE,
    )
    sql_upper = sql.upper()
    hints = []

    for match in index_pattern.finditer(schema_context):
        idx_name = match.group(1)
        table_ref = match.group(2)  # e.g. KYC.TRANSACTIONS
        col_list = match.group(3)  # e.g. TRANSACTION_DATE

        # Check if the table appears in the SQL
        table_short = table_ref.split(".")[-1].upper()
        if table_short in sql_upper:
            # Check if the indexed column appears in WHERE or ORDER BY
            for col in col_list.split(","):
                col_stripped = col.strip().upper()
                if col_stripped in sql_upper:
                    hints.append(f"{idx_name}({col_stripped})")
                    break

    return hints[:5]  # cap at 5 hints to keep comments terse
