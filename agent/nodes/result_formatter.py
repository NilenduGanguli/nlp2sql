"""
Result Formatter Node
======================
Formats the pipeline's execution result into a structured JSON response
suitable for display in the Streamlit chat UI.

The formatted response dict contains:
  type              – "query_result" | "error" | "schema_info"
  summary           – Human-readable one-line summary
  sql               – The executed SQL statement
  explanation       – Business-language explanation of the query
  columns           – Column name list
  rows              – First 100 rows for display
  total_rows        – Total row count from execution
  execution_time_ms – Query execution time in milliseconds
  data_source       – "mock" | "oracle"
  schema_context_tables – Table names extracted from schema_context

The full dict is JSON-serialized and stored in state["formatted_response"].
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List

from agent.state import AgentState

logger = logging.getLogger(__name__)

_MAX_DISPLAY_ROWS = 100


def make_result_formatter() -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that formats the query result.

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def format_result(state: AgentState) -> AgentState:
        error = state.get("error")
        execution_result = state.get("execution_result", {})
        optimized_sql = state.get("optimized_sql", "") or state.get("generated_sql", "")
        sql_explanation = state.get("sql_explanation", "")
        schema_context = state.get("schema_context", "")
        validation_errors = state.get("validation_errors", [])

        # ------------------------------------------------------------------ #
        # Error response
        # ------------------------------------------------------------------ #
        if error and not execution_result.get("rows"):
            response = {
                "type": "error",
                "summary": f"An error occurred: {error}",
                "sql": optimized_sql,
                "explanation": sql_explanation,
                "columns": [],
                "rows": [],
                "total_rows": 0,
                "execution_time_ms": 0,
                "data_source": "none",
                "schema_context_tables": _extract_table_names(schema_context),
                "validation_errors": validation_errors,
            }
            return {
                **state,
                "formatted_response": _safe_json(response),
                "step": "done",
            }

        # ------------------------------------------------------------------ #
        # Normal query result response
        # ------------------------------------------------------------------ #
        columns: List[str] = execution_result.get("columns", [])
        rows: List[List[Any]] = execution_result.get("rows", [])
        total_rows: int = execution_result.get("total_rows", len(rows))
        execution_time_ms: int = execution_result.get("execution_time_ms", 0)
        data_source: str = execution_result.get("source", "mock")

        # Build summary line
        row_word = "row" if total_rows == 1 else "rows"
        time_sec = execution_time_ms / 1000.0
        if data_source == "mock":
            summary = (
                f"Found {total_rows:,} {row_word} (demo data) "
                f"in {time_sec:.2f}s."
            )
        else:
            summary = (
                f"Found {total_rows:,} {row_word} from Oracle "
                f"in {time_sec:.2f}s."
            )

        # Serialize rows (truncate to display limit)
        serializable_rows = _serialize_rows(rows[:_MAX_DISPLAY_ROWS])

        response = {
            "type": "query_result",
            "summary": summary,
            "sql": optimized_sql,
            "explanation": sql_explanation,
            "columns": columns,
            "rows": serializable_rows,
            "total_rows": total_rows,
            "execution_time_ms": execution_time_ms,
            "data_source": data_source,
            "schema_context_tables": _extract_table_names(schema_context),
            "validation_errors": validation_errors,
        }

        logger.info(
            "Result formatted: type=%s, rows=%d, source=%s",
            response["type"],
            total_rows,
            data_source,
        )

        return {
            **state,
            "formatted_response": _safe_json(response),
            "step": "done",
        }

    return format_result


def _extract_table_names(schema_context: str) -> List[str]:
    """Extract table names mentioned in the DDL schema context string."""
    if not schema_context:
        return []
    # Match "-- TABLE: SCHEMA.NAME" lines
    pattern = re.compile(r"--\s+TABLE:\s+[\w.]+\.(\w+)", re.IGNORECASE)
    found = []
    for m in pattern.finditer(schema_context):
        name = m.group(1).upper()
        if name not in found:
            found.append(name)
    return found


def _serialize_rows(rows: List[List[Any]]) -> List[List[Any]]:
    """Convert row values to JSON-safe types."""
    result = []
    for row in rows:
        serialized = []
        for val in row:
            if val is None:
                serialized.append(None)
            elif isinstance(val, (int, float, bool, str)):
                serialized.append(val)
            else:
                # Handle dates, decimals, Oracle LOBs, etc.
                try:
                    serialized.append(str(val))
                except Exception:
                    serialized.append(repr(val))
        result.append(serialized)
    return result


def _safe_json(obj: Dict[str, Any]) -> str:
    """Serialize to JSON, handling non-serializable values gracefully."""
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception as exc:
        logger.error("JSON serialization failed: %s", exc)
        return json.dumps(
            {
                "type": "error",
                "summary": f"Serialization error: {exc}",
                "sql": "",
                "explanation": "",
                "columns": [],
                "rows": [],
                "total_rows": 0,
                "execution_time_ms": 0,
                "data_source": "none",
                "schema_context_tables": [],
                "validation_errors": [],
            }
        )
