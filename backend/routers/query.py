"""
NL query endpoint — streams pipeline progress via Server-Sent Events (SSE).

SSE event sequence per request:
  event: step            data: {"step": "enriching|classifying|extracting|..."}
  event: sql             data: {"sql": "<generated SQL>"}      (emitted as soon as SQL is ready)
  event: kyc_auto_answer data: {"question": "...", "auto_answer": "...", "source": "..."}
  event: sql_candidates  data: {"candidates": [...]}
  event: session_match   data: {"matched_entry_id": "...", "candidates": [...], "original_query": "..."}
  event: sql_ready       data: {"sql": "...", "explanation": "...", ...}  (confirm-before-execute)
  event: result          data: {<full result dict>}
  event: clarification   data: {"question": "...", "options": [...], "context": "..."}
  event: error           data: {"message": "<error>"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from backend.deps import get_config, get_knowledge_store, get_llm, get_pipeline
from backend.models import (
    ExecuteCandidateRequest,
    ExecuteConfirmedSqlRequest,
    QueryRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["query"])

# Map LangGraph node names → human-readable step labels for the UI
_NODE_TO_STEP: Dict[str, str] = {
    "enrich_query":        "enriching",
    "classify_intent":     "classifying",
    "extract_entities":    "extracting",
    "retrieve_schema":     "retrieving",
    "generate_sql":        "generating",
    "validate_sql":        "validating",
    "optimize_sql":        "optimizing",
    "present_sql":         "presenting",
    "kyc_business_agent":  "auto_clarifying",
    "session_lookup":      "checking_session_memory",
    "execute_query":       "executing",
    "format_result":       "formatting",
}

# Deterministic next-node lookup so we can emit a "step" label that
# describes what is *currently running* (not the node that just completed).
# Conditional edges are best-effort — see _next_step_label below.
_NODE_TO_NEXT: Dict[str, Optional[str]] = {
    "enrich_query":        "classify_intent",
    "classify_intent":     "extract_entities",
    "extract_entities":    "retrieve_schema",
    "retrieve_schema":     "session_lookup",
    "session_lookup":      "check_clarification",
    "check_clarification": "generate_sql",
    "generate_sql":        "validate_sql",
    "validate_sql":        "optimize_sql",
    "optimize_sql":        "present_sql",
    "kyc_business_agent":  "generate_sql",
    "present_sql":         None,
    "execute_query":       "format_result",
    "format_result":       None,
}


def _build_initial_state(user_input: str, history: list, **kwargs) -> Dict[str, Any]:
    return {
        "user_input": user_input,
        "conversation_history": history,
        "enriched_query": None,
        "intent": "DATA_QUERY",
        "entities": {},
        "schema_context": "",
        "candidate_sqls": [],
        "generated_sql": "",
        "sql_explanation": "",
        "validation_passed": False,
        "validation_errors": [],
        "optimized_sql": "",
        "execution_result": {},
        "formatted_response": "",
        "step": "start",
        "error": None,
        "retry_count": 0,
        "need_clarification": False,
        "clarification_question": "",
        "clarification_options": [],
        "clarification_context": "",
        "entity_table_fqns": [],
        "kyc_auto_answered": False,
        "kyc_auto_answer": "",
        "sql_candidates": [],
        "has_candidates": False,
        "session_match_entry_id": None,
        "skip_execution": kwargs.get("skip_execution", True),
        "previous_sql_context": kwargs.get("previous_sql_context") or {},
        "_trace": [],
    }


@router.post("/query")
async def stream_query(
    req: QueryRequest,
    pipeline=Depends(get_pipeline),
    config=Depends(get_config),
):
    """
    Run the NL-to-SQL pipeline and stream progress events via SSE.
    The sync LangGraph pipeline is executed in a thread pool so the async
    event loop is never blocked.
    """
    async def _generate():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        initial_state = _build_initial_state(
            req.user_input,
            req.conversation_history,
            skip_execution=not req.auto_execute,
            previous_sql_context=req.previous_sql_context,
        )

        def _run_pipeline():
            try:
                last_state: Dict[str, Any] = {}

                # LangGraph compiled graph exposes .stream() which yields
                # {node_name: state_after_node} after each node completes.
                if hasattr(pipeline, "stream"):
                    # Emit the FIRST step label upfront so the UI shows what's
                    # currently running, not what just finished. We pick the
                    # first real node in the pipeline.
                    first_node = (
                        "enrich_query"
                        if getattr(config, "query_enricher_enabled", True)
                        else "classify_intent"
                    )
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("step", {"step": _NODE_TO_STEP.get(first_node, first_node)}),
                    )

                    for chunk in pipeline.stream(initial_state):
                        node_name = next(iter(chunk))
                        state = chunk[node_name]
                        last_state = state

                        # check_clarification: don't emit a step badge — emit a
                        # clarification event instead when needed.
                        if node_name == "check_clarification":
                            if state.get("need_clarification"):
                                loop.call_soon_threadsafe(
                                    queue.put_nowait,
                                    (
                                        "clarification",
                                        {
                                            "question": state.get("clarification_question", ""),
                                            "options": state.get("clarification_options", []),
                                            "context": state.get("clarification_context", ""),
                                        },
                                    ),
                                )

                        # Emit the NEXT node's label so the UI describes
                        # what's currently running. Skip if there's no next
                        # (terminal node) or it's a no-op for the UI.
                        next_node = _NODE_TO_NEXT.get(node_name)
                        # If clarification is needed, retrieval flows go to
                        # check_clarification → END; don't preview "generating".
                        if next_node == "generate_sql" and state.get("need_clarification"):
                            next_node = None
                        if next_node and next_node != "check_clarification":
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("step", {"step": _NODE_TO_STEP.get(next_node, next_node)}),
                            )

                        # Emit trace step as soon as each node completes
                        _trace = state.get("_trace", [])
                        if _trace:
                            latest_trace = _trace[-1]  # the step just completed
                            loop.call_soon_threadsafe(
                                queue.put_nowait, ("trace", latest_trace)
                            )

                        # Emit SQL as soon as generator finishes
                        if node_name == "generate_sql" and state.get("generated_sql"):
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("sql", {"sql": state["generated_sql"]}),
                            )
                            # Emit multi-SQL candidates if detected
                            if state.get("has_candidates") and state.get("sql_candidates"):
                                loop.call_soon_threadsafe(
                                    queue.put_nowait,
                                    ("sql_candidates", {"candidates": state["sql_candidates"]}),
                                )

                        # Emit session_match when session_lookup short-circuits
                        if node_name == "session_lookup" and state.get("session_match_entry_id"):
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("session_match", {
                                    "matched_entry_id": state["session_match_entry_id"],
                                    "candidates": state.get("sql_candidates", []),
                                    "original_query": req.user_input,
                                }),
                            )
                            # Also emit candidates so existing UI flow renders the picker
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("sql_candidates", {"candidates": state.get("sql_candidates", [])}),
                            )

                        # Emit KYC auto-answer when business agent resolved a clarification
                        if node_name == "kyc_business_agent" and state.get("kyc_auto_answered"):
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("kyc_auto_answer", {
                                    "question": state.get("clarification_question", ""),
                                    "auto_answer": state.get("kyc_auto_answer", ""),
                                    "source": "knowledge_base",
                                }),
                            )

                        # Emit sql_ready when present_sql packages SQL for user review
                        if node_name == "present_sql":
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("sql_ready", {
                                    "sql": state.get("optimized_sql", "") or state.get("generated_sql", ""),
                                    "explanation": state.get("sql_explanation", ""),
                                    "validation_passed": state.get("validation_passed", False),
                                    "validation_errors": state.get("validation_errors", []),
                                }),
                            )

                    # Parse final formatted_response — but only if the pipeline
                    # completed normally (not stopped for clarification/presentation/candidates)
                    if (
                        not last_state.get("need_clarification")
                        and not last_state.get("has_candidates")
                    ):
                        result = _parse_formatted_response(last_state)
                        # Skip emitting "result" for sql_preview — already handled via sql_ready
                        if result is not None and result.get("type") == "sql_preview":
                            result = None
                        elif result is not None:
                            result["_trace"] = last_state.get("_trace", [])
                    else:
                        result = None
                else:
                    # _SequentialPipeline fallback (no LangGraph)
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("step", {"step": "processing"})
                    )
                    final_state = pipeline.invoke(initial_state)
                    result = _parse_formatted_response(final_state)
                    if result is not None:
                        result["_trace"] = final_state.get("_trace", [])

                if result is not None:
                    loop.call_soon_threadsafe(queue.put_nowait, ("result", result))

            except Exception as exc:
                logger.error("Pipeline error: %s", exc, exc_info=True)
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("error", {"message": str(exc)})
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        # Run pipeline in thread pool — never blocks the event loop
        loop.run_in_executor(None, _run_pipeline)

        while True:
            item = await queue.get()
            if item is None:
                break
            event, data = item
            yield {"event": event, "data": json.dumps(data, default=str)}

    return EventSourceResponse(_generate())


def _parse_formatted_response(state: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON formatted_response from pipeline state into a result dict."""
    formatted = state.get("formatted_response", "")
    if formatted:
        try:
            return json.loads(formatted)
        except (json.JSONDecodeError, TypeError):
            pass

    if state.get("error"):
        return {
            "type": "error",
            "summary": state["error"],
            "sql": state.get("optimized_sql", state.get("generated_sql", "")),
            "explanation": state.get("sql_explanation", ""),
            "columns": [], "rows": [], "total_rows": 0,
            "execution_time_ms": 0, "data_source": "none",
            "schema_context_tables": [], "validation_errors": state.get("validation_errors", []),
        }

    return {
        "type": "error",
        "summary": "The pipeline completed but produced no output.",
        "sql": state.get("optimized_sql", ""),
        "explanation": "",
        "columns": [], "rows": [], "total_rows": 0,
        "execution_time_ms": 0, "data_source": "none",
        "schema_context_tables": [], "validation_errors": [],
    }


# ---------------------------------------------------------------------------
# Execute confirmed SQL — runs only executor + formatter (no full pipeline)
# ---------------------------------------------------------------------------

@router.post("/query/execute")
async def execute_confirmed_sql(
    req: ExecuteConfirmedSqlRequest,
    config=Depends(get_config),
    knowledge_store=Depends(get_knowledge_store),
):
    """Execute a user-confirmed SQL query and stream results via SSE."""
    from agent.nodes.query_executor import make_query_executor
    from agent.nodes.result_formatter import make_result_formatter

    exec_fn = make_query_executor(config)
    format_fn = make_result_formatter()

    # Learning: user confirmed execution → bump confidence of any matching pattern
    if knowledge_store and req.user_input:
        try:
            pattern = knowledge_store.find_matching_pattern("", req.user_input)
            if pattern:
                knowledge_store.bump_confidence(pattern.id, delta=0.1)
        except Exception:
            pass  # non-critical

    async def _generate():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _run():
            try:
                state: Dict[str, Any] = {
                    "user_input": req.user_input,
                    "conversation_history": req.conversation_history,
                    "optimized_sql": req.sql,
                    "generated_sql": req.sql,
                    "sql_explanation": "",
                    "validation_passed": True,
                    "validation_errors": [],
                    "execution_result": {},
                    "formatted_response": "",
                    "error": None,
                    "_trace": [],
                }
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("step", {"step": "executing"})
                )
                state = exec_fn(state)

                # Emit trace for executor
                _trace = state.get("_trace", [])
                if _trace:
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("trace", _trace[-1])
                    )

                loop.call_soon_threadsafe(
                    queue.put_nowait, ("step", {"step": "formatting"})
                )
                state = format_fn(state)

                if _trace := state.get("_trace", []):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("trace", _trace[-1])
                    )

                result = _parse_formatted_response(state)
                if result is not None:
                    result["_trace"] = state.get("_trace", [])
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("result", result)
                    )
            except Exception as exc:
                logger.error("Execute error: %s", exc, exc_info=True)
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("error", {"message": str(exc)})
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _run)

        while True:
            item = await queue.get()
            if item is None:
                break
            event, data = item
            yield {"event": event, "data": json.dumps(data, default=str)}

    return EventSourceResponse(_generate())


# ---------------------------------------------------------------------------
# Execute a selected SQL candidate — validate → optimize → present
# ---------------------------------------------------------------------------

@router.post("/query/execute-candidate")
async def execute_candidate(
    req: ExecuteCandidateRequest,
    pipeline=Depends(get_pipeline),
    config=Depends(get_config),
):
    """Validate, optimize, and present a user-selected SQL candidate."""
    from agent.nodes.query_optimizer import make_query_optimizer
    from agent.nodes.sql_presenter import make_sql_presenter
    from agent.nodes.sql_validator import make_sql_validator

    async def _generate():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        graph = None
        try:
            graph = config._graph  # type: ignore[attr-defined]
        except AttributeError:
            pass

        valid_fn = make_sql_validator(graph=graph)
        opt_fn = make_query_optimizer()
        present_fn = make_sql_presenter()

        def _run():
            try:
                state: Dict[str, Any] = {
                    "user_input": req.user_input,
                    "conversation_history": req.conversation_history,
                    "generated_sql": req.sql,
                    "sql_explanation": req.explanation,
                    "optimized_sql": "",
                    "validation_passed": False,
                    "validation_errors": [],
                    "formatted_response": "",
                    "schema_context": "",
                    "retry_count": 0,
                    "error": None,
                    "skip_execution": True,
                    "_trace": [],
                }

                loop.call_soon_threadsafe(
                    queue.put_nowait, ("step", {"step": "validating"})
                )
                state = valid_fn(state)

                loop.call_soon_threadsafe(
                    queue.put_nowait, ("step", {"step": "optimizing"})
                )
                state = opt_fn(state)

                loop.call_soon_threadsafe(
                    queue.put_nowait, ("step", {"step": "presenting"})
                )
                state = present_fn(state)

                sql = state.get("optimized_sql", "") or state.get("generated_sql", "")
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("sql_ready", {
                        "sql": sql,
                        "explanation": state.get("sql_explanation", ""),
                        "validation_passed": state.get("validation_passed", False),
                        "validation_errors": state.get("validation_errors", []),
                    }),
                )

                # Emit traces
                for t in state.get("_trace", []):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("trace", t)
                    )

            except Exception as exc:
                logger.error("Candidate execute error: %s", exc, exc_info=True)
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("error", {"message": str(exc)})
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _run)

        while True:
            item = await queue.get()
            if item is None:
                break
            event, data = item
            yield {"event": event, "data": json.dumps(data, default=str)}

    return EventSourceResponse(_generate())


# ---------------------------------------------------------------------------
# Record clarification answer as a learned pattern
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel

class _RecordClarificationRequest(_BaseModel):
    question: str = ""
    answer: str = ""
    user_query: str = ""

@router.post("/query/record-clarification")
async def record_clarification(
    req: _RecordClarificationRequest,
    knowledge_store=Depends(get_knowledge_store),
):
    """Record a user's clarification answer as a learned pattern."""
    if not knowledge_store:
        return {"status": "skipped", "reason": "no_knowledge_store"}
    try:
        if req.question and req.answer:
            pattern = knowledge_store.record_pattern(
                question=req.question,
                answer=req.answer,
                user_query=req.user_query,
                confidence=0.5,
                category="filter_value",
            )
            return {"status": "recorded", "pattern_id": pattern.id}
    except Exception as exc:
        logger.warning("Failed to record clarification: %s", exc)
    return {"status": "skipped"}


# ---------------------------------------------------------------------------
# Accept / reject generated query — records conversation as learned knowledge
# ---------------------------------------------------------------------------

from typing import List as _List

class _ClarificationPair(_BaseModel):
    question: str
    answer: str

class _AcceptedCandidate(_BaseModel):
    id: str = ""
    sql: str
    explanation: str = ""
    interpretation: str = ""

class _RejectedCandidate(_BaseModel):
    id: str = ""
    sql: str = ""
    explanation: str = ""
    interpretation: str = ""
    rejection_reason: str = ""

class _AcceptQueryRequest(_BaseModel):
    sql: str = ""                         # legacy single-SQL field (back-compat)
    explanation: str = ""
    user_input: str = ""
    clarification_pairs: _List[_ClarificationPair] = []
    accepted: bool = True
    accepted_candidates: _List[_AcceptedCandidate] = []
    rejected_candidates: _List[_RejectedCandidate] = []
    executed_candidate_id: Optional[str] = None
    session_digest: Dict[str, Any] = {}
    mode: Optional[Literal["curator", "consumer"]] = None

@router.post("/query/accept-query")
async def accept_query(
    req: _AcceptQueryRequest,
    knowledge_store=Depends(get_knowledge_store),
    llm=Depends(get_llm),
):
    """Record a user's acceptance (or rejection) of a generated query.

    When accepted, the conversation context and clarification pairs are
    stored as learned patterns in the KYC knowledge store so the agent
    can auto-answer similar questions in the future.  If an LLM is
    available, a background task also analyzes the interaction to produce
    rich, descriptive knowledge entries.
    """
    if not knowledge_store:
        return {"status": "skipped", "reason": "no_knowledge_store"}

    if not req.accepted:
        return {"status": "rejected"}

    # Normalize: if no accepted_candidates supplied (legacy clients), synthesize one from req.sql
    accepted_list = list(req.accepted_candidates)
    if not accepted_list and req.sql:
        accepted_list = [_AcceptedCandidate(
            id="legacy", sql=req.sql, explanation=req.explanation, interpretation="primary",
        )]

    recorded_ids: _List[str] = []
    try:
        # 1. Record each clarification pair as a learned pattern
        for pair in req.clarification_pairs:
            if pair.question and pair.answer:
                p = knowledge_store.record_pattern(
                    question=pair.question,
                    answer=pair.answer,
                    user_query=req.user_input,
                    sql=req.sql,
                    confidence=0.8,
                    category="filter_value",
                    user_confirmed=True,
                    tags=["accepted_query"],
                )
                recorded_ids.append(p.id)

        # 2. Record the overall query → SQL mapping as a pattern
        if req.user_input and req.sql:
            p = knowledge_store.record_pattern(
                question=req.user_input,
                answer=req.sql,
                user_query=req.user_input,
                sql=req.sql,
                confidence=0.8,
                category="query_mapping",
                user_confirmed=True,
                tags=["accepted_query"],
            )
            recorded_ids.append(p.id)

    except Exception as exc:
        logger.warning("Failed to record accepted query: %s", exc)
        return {"status": "partial", "recorded": recorded_ids}

    # 3. Background: comprehensive session learning + narrow per-clarification fallback
    if llm is not None and req.user_input and accepted_list:
        import anyio

        _llm = llm
        _store = knowledge_store
        _user_input = req.user_input
        _digest = req.session_digest or {}
        _accepted_payload = [a.model_dump() for a in accepted_list]
        _rejected_payload = [r.model_dump() for r in req.rejected_candidates]
        _executed_id = req.executed_candidate_id
        _pairs = [(p.question, p.answer) for p in req.clarification_pairs]
        _legacy_sql = req.sql or (accepted_list[0].sql if accepted_list else "")
        _legacy_expl = req.explanation
        _mode = req.mode or "curator"

        async def _analyze_bg():
            # 3a. Comprehensive session learning (one rich entry).
            try:
                from agent.session_digest import build_session_digest
                from agent.llm_knowledge_analyzer import analyze_accepted_session
                if not _digest:
                    digest = build_session_digest(
                        {"user_input": _user_input,
                         "clarifications_resolved": [{"question": q, "answer": a} for q, a in _pairs]},
                        _accepted_payload, _rejected_payload, executed_id=_executed_id,
                    )
                else:
                    digest = _digest
                entry = await anyio.to_thread.run_sync(
                    lambda: analyze_accepted_session(_llm, digest)
                )
                if entry is not None:
                    _store.add_session_entry(entry)
                    logger.info("Session learning recorded entry %s", entry.id)
                    try:
                        from agent.pattern_aggregator import aggregate_patterns
                        from backend.deps import get_signal_log
                        sigs = get_signal_log()
                        aggregate_patterns(_store, entry, sigs, mode=_mode, manual_promotion=False)
                    except Exception as agg_exc:
                        logger.warning("pattern aggregation failed: %s", agg_exc)
                else:
                    raise ValueError("session analyzer returned None")
            except Exception as exc:
                logger.warning("Session analyzer failed (%s); falling back to narrow analyzer", exc)
                # 3b. Fallback: legacy narrow analyzer (1-3 entries).
                try:
                    from agent.llm_knowledge_analyzer import analyze_accepted_query
                    entries = await anyio.to_thread.run_sync(
                        lambda: analyze_accepted_query(
                            _llm, _user_input, _legacy_sql, _legacy_expl, _pairs,
                        )
                    )
                    for e in entries:
                        _store.add_manual_entry(e.content, e.category, e.metadata)
                except Exception as exc2:
                    logger.warning("Narrow analyzer also failed: %s", exc2)

        asyncio.create_task(_analyze_bg())

    return {"status": "accepted", "recorded": recorded_ids}
