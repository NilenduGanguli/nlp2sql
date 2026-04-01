"""
NL query endpoint — streams pipeline progress via Server-Sent Events (SSE).

SSE event sequence per request:
  event: step    data: {"step": "enriching|classifying|extracting|..."}
  event: sql     data: {"sql": "<generated SQL>"}      (emitted as soon as SQL is ready)
  event: result  data: {<full result dict>}
  event: error   data: {"message": "<error>"}
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from backend.deps import get_config, get_pipeline
from backend.models import QueryRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["query"])

# Map LangGraph node names → human-readable step labels for the UI
_NODE_TO_STEP: Dict[str, str] = {
    "enrich_query":    "enriching",
    "classify_intent": "classifying",
    "extract_entities": "extracting",
    "retrieve_schema": "retrieving",
    "generate_sql":    "generating",
    "validate_sql":    "validating",
    "optimize_sql":    "optimizing",
    "execute_query":   "executing",
    "format_result":   "formatting",
}


def _build_initial_state(user_input: str, history: list) -> Dict[str, Any]:
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
        initial_state = _build_initial_state(req.user_input, req.conversation_history)

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
                        else:
                            loop.call_soon_threadsafe(
                                queue.put_nowait, ("step", {"step": step})
                            )

                        # Emit SQL as soon as generator finishes
                        if node_name == "generate_sql" and state.get("generated_sql"):
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("sql", {"sql": state["generated_sql"]}),
                            )

                    # Parse final formatted_response
                    result = _parse_formatted_response(last_state)
                else:
                    # _SequentialPipeline fallback (no LangGraph)
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("step", {"step": "processing"})
                    )
                    final_state = pipeline.invoke(initial_state)
                    result = _parse_formatted_response(final_state)

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
