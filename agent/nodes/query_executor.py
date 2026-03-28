"""
Query Executor Node
====================
Executes the optimized SQL against a live Oracle database via oracledb.

Result format:
  {
    "columns": [...],
    "rows": [[...], ...],
    "total_rows": N,
    "execution_time_ms": N,
    "source": "oracle" | "none" | "error"
  }
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List

from agent.state import AgentState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Live Oracle executor
# ---------------------------------------------------------------------------

def _oracle_execute(sql: str, config) -> Dict[str, Any]:
    """Execute SQL against a real Oracle database via oracledb."""
    try:
        import oracledb
    except ImportError:
        raise ImportError(
            "python-oracledb is required for live Oracle execution. "
            "Install it with: pip install python-oracledb"
        )

    start_ms = time.time()
    if config.oracle.thick_mode and oracledb.is_thin_mode():
        try:
            oracledb.init_oracle_client()
            logger.info("oracledb thick mode enabled")
        except Exception as exc:
            logger.warning("Cannot enable thick mode; falling back to thin mode. %s", exc)

    conn = oracledb.connect(
        user=config.oracle.user,
        password=config.oracle.password,
        dsn=config.oracle.dsn,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        col_names = [d[0] for d in cursor.description]
        max_rows = getattr(config, "max_result_rows", None) or 10000
        raw_rows = cursor.fetchmany(max_rows)
        rows = [list(r) for r in raw_rows]
        cursor.close()
    finally:
        conn.close()

    elapsed = int((time.time() - start_ms) * 1000)
    return {
        "columns": col_names,
        "rows": rows,
        "total_rows": len(rows),
        "execution_time_ms": elapsed,
        "source": "oracle",
    }


# ---------------------------------------------------------------------------
# Node factory
# ---------------------------------------------------------------------------

def make_query_executor(config) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that executes the optimized SQL.

    Parameters
    ----------
    config : AppConfig
        Application configuration (oracle credentials, row limits).

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def execute_query(state: AgentState) -> AgentState:
        sql = state.get("optimized_sql", "") or state.get("generated_sql", "")

        if not sql:
            return {
                **state,
                "execution_result": {
                    "columns": [],
                    "rows": [],
                    "total_rows": 0,
                    "execution_time_ms": 0,
                    "source": "none",
                },
                "error": "No SQL to execute.",
                "step": "query_executed",
            }

        try:
            result = _oracle_execute(sql, config)
            logger.info(
                "Oracle execution: %d rows in %dms",
                result["total_rows"],
                result["execution_time_ms"],
            )
        except Exception as exc:
            logger.error("Oracle execution failed: %s", exc)
            return {
                **state,
                "execution_result": {
                    "columns": [],
                    "rows": [],
                    "total_rows": 0,
                    "execution_time_ms": 0,
                    "source": "error",
                    "error": str(exc),
                },
                "error": str(exc),
                "step": "query_executed",
            }

        return {
            **state,
            "execution_result": result,
            "step": "query_executed",
        }

    return execute_query
