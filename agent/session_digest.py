"""
SessionDigest builder
=====================
Pure function that converts pipeline state + acceptance metadata into a
structured digest used by the session analyzer (LLM) and persisted in
KnowledgeEntry.metadata.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

_MAX_TOOL_CALLS = 30
_MAX_RESULT_SUMMARY_CHARS = 200


def _summarize_op(op: Dict[str, Any]) -> Dict[str, Any]:
    sample = op.get("result_sample") or []
    summary = f"count={op.get('result_count', 0)}; sample={sample}"
    return {
        "tool": op.get("op", ""),
        "args": op.get("params", {}) or {},
        "result_summary": summary[:_MAX_RESULT_SUMMARY_CHARS],
    }


def _extract_tool_calls(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for step in trace or []:
        for op in step.get("graph_ops", []) or []:
            calls.append(_summarize_op(op))
            if len(calls) >= _MAX_TOOL_CALLS:
                return calls
    return calls


def _extract_schema_tables(schema_context: str) -> List[str]:
    import re
    tables = []
    for line in (schema_context or "").splitlines():
        m = re.match(r"--\s*TABLE:\s*([\w\.]+)", line.strip(), re.IGNORECASE)
        if m:
            tables.append(m.group(1))
    return tables


def build_session_digest(
    state: Dict[str, Any],
    accepted: List[Dict[str, Any]],
    rejected: List[Dict[str, Any]],
    executed_id: Optional[str],
) -> Dict[str, Any]:
    """Build a structured digest of one query session.

    Parameters
    ----------
    state : dict
        Final pipeline state (after acceptance).
    accepted : list[dict]
        Candidates the user marked as valid.
    rejected : list[dict]
        Candidates the user did NOT mark valid (may include rejection_reason).
    executed_id : str | None
        ID of the candidate the user chose to execute (None if none).
    """
    candidates: List[Dict[str, Any]] = []
    for c in accepted or []:
        candidates.append({
            "id": c.get("id", ""),
            "interpretation": c.get("interpretation", ""),
            "sql": c.get("sql", ""),
            "explanation": c.get("explanation", ""),
            "accepted": True,
            "executed": (executed_id is not None and c.get("id") == executed_id),
        })
    for c in rejected or []:
        candidates.append({
            "id": c.get("id", ""),
            "interpretation": c.get("interpretation", ""),
            "sql": c.get("sql", ""),
            "explanation": c.get("explanation", ""),
            "accepted": False,
            "executed": False,
            "rejection_reason": c.get("rejection_reason", ""),
        })

    exec_result = state.get("execution_result") or {}
    result_shape: Dict[str, Any] = {}
    if exec_result.get("columns") or exec_result.get("total_rows") is not None:
        result_shape = {
            "columns": exec_result.get("columns", []),
            "row_count": exec_result.get("total_rows", 0),
        }

    return {
        "session_id": str(uuid.uuid4()),
        "original_query": state.get("user_input", ""),
        "enriched_query": state.get("enriched_query") or "",
        "intent": state.get("intent", "DATA_QUERY"),
        "entities": state.get("entities", {}) or {},
        "clarifications": state.get("clarifications_resolved", []) or [],
        "tool_calls": _extract_tool_calls(state.get("_trace", [])),
        "schema_context_tables": _extract_schema_tables(state.get("schema_context", "")),
        "candidates": candidates,
        "validation_retries": int(state.get("retry_count", 0) or 0),
        "result_shape": result_shape,
        "created_at": time.time(),
    }
