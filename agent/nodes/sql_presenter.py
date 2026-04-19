"""
SQL Presenter Node
==================
Packages the optimized SQL for user review instead of auto-executing it.
Produces a ``formatted_response`` with ``type: "sql_preview"`` so the
frontend can show a confirmation card with a "Run Query" button.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict

from agent.state import AgentState
from agent.trace import TraceStep

logger = logging.getLogger(__name__)


def make_sql_presenter() -> Callable[[AgentState], AgentState]:
    """Return a LangGraph node that presents SQL for user confirmation."""

    def present_sql(state: AgentState) -> AgentState:
        sql = state.get("optimized_sql", "") or state.get("generated_sql", "")
        explanation = state.get("sql_explanation", "")
        validation_passed = state.get("validation_passed", False)
        validation_errors = state.get("validation_errors", [])
        _trace = list(state.get("_trace", []))
        trace = TraceStep("present_sql", "presenting")

        preview = {
            "type": "sql_preview",
            "sql": sql,
            "explanation": explanation,
            "validation_passed": validation_passed,
            "validation_errors": validation_errors,
            "schema_context_tables": [],
        }

        # Extract table names from schema_context for reference
        schema_ctx = state.get("schema_context", "")
        tables = []
        for line in schema_ctx.splitlines():
            stripped = line.strip()
            if stripped.startswith("-- TABLE:"):
                tbl = stripped.replace("-- TABLE:", "").strip()
                if tbl:
                    tables.append(tbl)
        preview["schema_context_tables"] = tables

        trace.output_summary = {
            "sql_length": len(sql),
            "validation_passed": validation_passed,
            "tables": len(tables),
        }
        _trace.append(trace.finish().to_dict())

        return {
            **state,
            "formatted_response": json.dumps(preview),
            "step": "sql_presented",
            "_trace": _trace,
        }

    return present_sql
