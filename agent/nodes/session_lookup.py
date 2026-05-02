"""
Session Lookup Node
====================
Runs after retrieve_schema, before check_clarification.

Three-way routing on the top-1 ranked accepted-query entry's score:

  >= SHORT_CIRCUIT_THRESHOLD (0.75)
      Short-circuit the pipeline:
        state["sql_candidates"]         seeded with the saved SQL
        state["has_candidates"]         True
        state["session_match_entry_id"] set
      Pipeline routing then sends control to present_sql,
      skipping clarification and SQL generation.

  >= RAG_INJECT_MIN (0.30) and < SHORT_CIRCUIT_THRESHOLD
      Inject up to top_k=3 entries as RAG examples:
        state["accepted_examples"]      list of {score, description,
                                                  why_this_sql, sql,
                                                  key_concepts, tags}
      Pipeline continues normally; the SQL generator's prompt rule 20
      tells it to prefer these examples' tables/joins/filters/values.

  < RAG_INJECT_MIN
      No-op.

Verified-pattern matches (from KYCKnowledgeStore.find_verified_pattern)
take precedence over both paths and behave like the short-circuit case.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from agent.trace import TraceStep

logger = logging.getLogger(__name__)

SHORT_CIRCUIT_THRESHOLD = 0.75
RAG_INJECT_MIN = 0.30


def make_session_lookup(knowledge_store, graph) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Build the session_lookup node.

    When ``knowledge_store`` is None or ``graph`` is None, the node is a passthrough.
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
            trace.output_summary = {
                "action": "skip", "reason": "followup_or_mid_thread",
                "intent": intent, "history_len": len(history),
            }
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        query = state.get("user_input") or state.get("enriched_query", "")

        # 1. Verified-pattern short-circuit (existing — unchanged)
        try:
            vp = knowledge_store.find_verified_pattern(query, graph)
        except Exception as exc:
            logger.warning("verified-pattern lookup failed: %s", exc)
            vp = None
        if vp is not None:
            candidate = {
                "id": "vp01",
                "interpretation": "verified pattern",
                "sql": vp.exemplar_sql,
                "explanation": f"Verified pattern (score={vp.score:.1f}, accepts={vp.accept_count})",
                "is_verified": True,
                "pattern_id": vp.pattern_id,
            }
            trace.output_summary = {
                "action": "match", "match_kind": "verified_pattern",
                "matched_pattern_id": vp.pattern_id, "candidate_count": 1,
                "matched_query": (vp.exemplar_query or "")[:80],
            }
            _trace.append(trace.finish().to_dict())
            return {
                **state,
                "sql_candidates": [candidate],
                "has_candidates": True,
                "session_match_entry_id": vp.pattern_id,
                "step": "session_matched",
                "_trace": _trace,
            }

        # 2. Graded session-entry retrieval (Phase 1 RAG)
        try:
            ranked = knowledge_store.rank_accepted_entries(query, top_k=3, graph=graph)
        except Exception as exc:
            logger.warning("rank_accepted_entries failed: %s", exc)
            trace.error = str(exc)
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        if not ranked:
            trace.output_summary = {"action": "miss", "query_preview": query[:80]}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        top_entry, top_score = ranked[0]

        # 2a. Short-circuit at high similarity
        if top_score >= SHORT_CIRCUIT_THRESHOLD:
            accepted = (top_entry.metadata or {}).get("accepted_candidates", []) or []
            candidates = [{
                "id": f"sm{i+1:02d}",
                "interpretation": c.get("interpretation", "Reused interpretation"),
                "sql": c.get("sql", ""),
                "explanation": c.get("explanation", ""),
            } for i, c in enumerate(accepted) if c.get("sql")]
            if candidates:
                trace.output_summary = {
                    "action": "match", "match_kind": "query_session",
                    "matched_entry_id": top_entry.id,
                    "candidate_count": len(candidates), "score": round(top_score, 2),
                }
                _trace.append(trace.finish().to_dict())
                return {
                    **state,
                    "sql_candidates": candidates,
                    "has_candidates": True,
                    "session_match_entry_id": top_entry.id,
                    "step": "session_matched",
                    "_trace": _trace,
                }
            # entry has no SQL — fall through to RAG path

        # 2b. RAG injection at moderate similarity
        examples = []
        for entry, score in ranked:
            if score < RAG_INJECT_MIN or score >= SHORT_CIRCUIT_THRESHOLD:
                continue
            md = entry.metadata or {}
            accepted = md.get("accepted_candidates", []) or []
            sql = accepted[0]["sql"] if accepted else ""
            examples.append({
                "score": round(score, 2),
                "description": md.get("description", "") or md.get("original_query", ""),
                "why_this_sql": md.get("why_this_sql", ""),
                "sql": sql,
                "key_concepts": md.get("key_concepts", []) or [],
                "tags": md.get("tags", []) or [],
            })

        if examples:
            trace.output_summary = {
                "action": "rag_inject", "example_count": len(examples),
                "top_score": round(top_score, 2),
            }
            _trace.append(trace.finish().to_dict())
            return {**state, "accepted_examples": examples, "_trace": _trace}

        trace.output_summary = {"action": "below_threshold", "top_score": round(top_score, 2)}
        _trace.append(trace.finish().to_dict())
        return {**state, "_trace": _trace}

    return session_lookup
