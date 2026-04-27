"""
Session Lookup Node
====================
Runs after retrieve_schema, before check_clarification.

If a high-similarity prior `query_session` entry exists in the KYC knowledge
store AND all referenced tables still exist in the live graph, this node
short-circuits the clarification flow:
  - state["sql_candidates"] is seeded from the saved entry
  - state["has_candidates"] = True
  - state["session_match_entry_id"] is set

Pipeline routing then sends control to present_sql, skipping clarification.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from agent.trace import TraceStep

logger = logging.getLogger(__name__)


def make_session_lookup(knowledge_store, graph) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Build the session_lookup node.

    When `knowledge_store` is None or `graph` is None, the node is a passthrough.
    """
    def session_lookup(state: Dict[str, Any]) -> Dict[str, Any]:
        _trace = list(state.get("_trace", []))
        trace = TraceStep("session_lookup", "session_lookup")

        if knowledge_store is None or graph is None:
            trace.output_summary = {"action": "skip", "reason": "disabled"}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        intent = state.get("intent", "DATA_QUERY")
        history = state.get("conversation_history", []) or []
        if intent == "RESULT_FOLLOWUP" or len(history) > 0:
            trace.output_summary = {"action": "skip",
                                    "reason": "followup_or_mid_thread", "intent": intent,
                                    "history_len": len(history)}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        query = state.get("user_input") or state.get("enriched_query", "")
        try:
            match = knowledge_store.find_session_match(query, graph)
        except Exception as exc:
            logger.warning("session_lookup failed: %s", exc)
            trace.error = str(exc)
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        if match is None:
            trace.output_summary = {"action": "miss", "query_preview": query[:80]}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        accepted = (match.metadata or {}).get("accepted_candidates", []) or []
        if not accepted:
            trace.output_summary = {"action": "skip", "reason": "no_accepted_candidates"}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        candidates = []
        for i, c in enumerate(accepted):
            candidates.append({
                "id": f"sm{i+1:02d}",
                "interpretation": c.get("interpretation", "Reused interpretation"),
                "sql": c.get("sql", ""),
                "explanation": c.get("explanation", ""),
            })

        trace.output_summary = {
            "action": "match", "matched_entry_id": match.id,
            "candidate_count": len(candidates),
            "matched_query": (match.metadata or {}).get("original_query", "")[:80],
        }
        _trace.append(trace.finish().to_dict())
        return {
            **state,
            "sql_candidates": candidates,
            "has_candidates": True,
            "session_match_entry_id": match.id,
            "step": "session_matched",
            "_trace": _trace,
        }

    return session_lookup
