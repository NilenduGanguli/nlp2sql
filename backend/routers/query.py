"""
NL query endpoint — streams pipeline progress via Server-Sent Events (SSE).

SSE event sequence per request:
  event: step            data: {"step": "enriching|classifying|extracting|..."}
  event: sql             data: {"sql": "<generated SQL>"}      (emitted as soon as SQL is ready)
  event: kyc_auto_answer data: {"question": "...", "auto_answer": "...", "source": "..."}
  event: sql_candidates  data: {"candidates": [...]}
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
from typing import Any, Dict

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
    "execute_query":       "executing",
    "format_result":       "formatting",
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
                    for chunk in pipeline.stream(initial_state):
                        node_name = next(iter(chunk))
                        state = chunk[node_name]
                        last_state = state

                        step = _NODE_TO_STEP.get(node_name, node_name)
                        # Don't show "enriching" when enricher is disabled (node
                        # is a no-op pass-through, not a real LLM call)
                        if node_name == "enrich_query" and not getattr(config, "query_enricher_enabled", True):
                            pass
                        # Don't show a step badge for the clarification check node —
                        # emit a clarification event instead when needed
                        elif node_name == "check_clarification":
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
                        else:
                            loop.call_soon_threadsafe(
                                queue.put_nowait, ("step", {"step": step})
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

class _AcceptQueryRequest(_BaseModel):
    sql: str
    explanation: str = ""
    user_input: str = ""
    clarification_pairs: _List[_ClarificationPair] = []
    accepted: bool = True

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

    # 3. Background: LLM-analyze the interaction for rich knowledge entries
    if llm is not None and req.user_input and req.sql:
        import anyio

        _llm = llm
        _store = knowledge_store
        _user_input = req.user_input
        _sql = req.sql
        _explanation = req.explanation
        _pairs = [(p.question, p.answer) for p in req.clarification_pairs]

        async def _analyze_bg():
            try:
                from agent.llm_knowledge_analyzer import analyze_accepted_query
                entries = await anyio.to_thread.run_sync(
                    lambda: analyze_accepted_query(
                        _llm, _user_input, _sql, _explanation, _pairs,
                    )
                )
                for e in entries:
                    _store.add_manual_entry(e.content, e.category, e.metadata)
                if entries:
                    logger.info(
                        "LLM query analysis added %d knowledge entries for: %s",
                        len(entries), _user_input[:60],
                    )
            except Exception as exc:
                logger.warning("LLM query analysis failed: %s", exc)

        asyncio.create_task(_analyze_bg())

    return {"status": "accepted", "recorded": recorded_ids}
