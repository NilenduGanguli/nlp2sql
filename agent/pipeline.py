"""
LangGraph Agent Pipeline
=========================
Assembles and compiles the full NLP-to-SQL LangGraph workflow.

Pipeline flow:
  classify_intent → extract_entities → retrieve_schema → generate_sql
      → validate_sql ──[pass]──→ optimize_sql → execute_query → format_result → END
                      └──[fail, retry < 3]──→ generate_sql
                      └──[fail, retry >= 3]──→ optimize_sql (force)

Exported functions:
  build_pipeline(graph, config, llm=None) → compiled LangGraph app
  run_query(pipeline, user_input, conversation_history) → result dict
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# No-LLM fallback nodes (used when no API key is provided)
# ---------------------------------------------------------------------------

def _default_intent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback intent node: always DATA_QUERY."""
    return {**state, "intent": "DATA_QUERY", "step": "intent_classified"}


def _default_entities(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback entity extractor: simple keyword matching against KYC table names."""
    text = state.get("user_input", "").upper()
    kyc_tables = [
        "CUSTOMERS", "ACCOUNTS", "TRANSACTIONS", "KYC_REVIEWS",
        "RISK_ASSESSMENTS", "BENEFICIAL_OWNERS", "EMPLOYEES", "PEP_STATUS",
    ]
    found = [t for t in kyc_tables if t in text or t.rstrip("S") in text]

    # Detect aggregations
    aggregations = []
    if any(kw in text for kw in ("HOW MANY", "COUNT", "NUMBER OF", "TOTAL")):
        aggregations.append("COUNT")
    if any(kw in text for kw in ("SUM", "TOTAL AMOUNT")):
        aggregations.append("SUM")

    # Detect time ranges
    time_range = None
    for phrase in ("LAST MONTH", "LAST QUARTER", "LAST YEAR", "THIS YEAR", "THIS MONTH"):
        if phrase in text:
            time_range = phrase.lower()
            break

    # Detect conditions
    conditions = []
    if "HIGH RISK" in text or "HIGH-RISK" in text:
        conditions.append("RISK_RATING = 'HIGH'")
    if "VERY HIGH" in text:
        conditions.append("RISK_RATING = 'VERY_HIGH'")
    if "PEP" in text:
        conditions.append("IS_PEP = 'Y'")
    if "FLAGGED" in text:
        conditions.append("IS_FLAGGED = 'Y'")

    return {
        **state,
        "entities": {
            "tables": found or ["CUSTOMERS"],
            "columns": [],
            "conditions": conditions,
            "time_range": time_range,
            "aggregations": aggregations,
            "sort_by": None,
            "limit": None,
        },
        "step": "entities_extracted",
    }


def _no_llm_sql_generator(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback SQL generator: generates a simple SELECT * for the primary table."""
    entities = state.get("entities", {})
    tables = entities.get("tables", ["CUSTOMERS"])
    table = tables[0] if tables else "CUSTOMERS"

    conditions = entities.get("conditions", [])
    time_range = entities.get("time_range")
    aggregations = entities.get("aggregations", [])

    # Build a minimal but reasonable Oracle SQL
    if aggregations and "COUNT" in aggregations:
        sql = f"SELECT COUNT(*) AS TOTAL_COUNT FROM KYC.{table}"
        if conditions:
            sql += f" WHERE {' AND '.join(conditions)}"
        explanation = f"Counts the total number of records in {table}"
    else:
        select_cols = "*"
        sql = f"SELECT {select_cols} FROM KYC.{table} c"
        where_clauses = list(conditions)

        if time_range and table == "TRANSACTIONS":
            where_clauses.append(
                "c.TRANSACTION_DATE >= TRUNC(SYSDATE, 'MM') - INTERVAL '1' MONTH"
            )
        elif time_range and table == "KYC_REVIEWS":
            where_clauses.append(
                "c.REVIEW_DATE >= ADD_MONTHS(TRUNC(SYSDATE), -12)"
            )

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        # Always add a sort on primary key if possible
        pk_map = {
            "CUSTOMERS": "c.CUSTOMER_ID",
            "ACCOUNTS": "c.ACCOUNT_ID",
            "TRANSACTIONS": "c.TRANSACTION_DATE DESC",
            "KYC_REVIEWS": "c.REVIEW_DATE DESC",
            "RISK_ASSESSMENTS": "c.ASSESSED_DATE DESC",
            "BENEFICIAL_OWNERS": "c.OWNER_ID",
            "EMPLOYEES": "c.EMPLOYEE_ID",
            "PEP_STATUS": "c.PEP_ID",
        }
        order_col = pk_map.get(table, "1")
        sql += f" ORDER BY {order_col}"
        explanation = f"Retrieves records from {table}"
        if conditions:
            explanation += f" matching: {', '.join(conditions)}"

    return {
        **state,
        "generated_sql": sql,
        "sql_explanation": explanation,
        "validation_passed": True,  # trust the fallback
        "step": "sql_generated",
    }


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------

def build_pipeline(graph, config, llm=None):
    """
    Build and compile the NLP-to-SQL pipeline.

    Uses LangGraph when available; falls back to a simple sequential pipeline
    when langgraph is not installed (graceful degradation).

    Parameters
    ----------
    graph : KnowledgeGraph
        The populated in-memory knowledge graph for schema retrieval.
    config : AppConfig
        Application configuration (LLM provider, demo mode, etc.).
    llm : BaseChatModel | None
        Optional pre-built LangChain chat model. If None and config.llm_api_key
        is set, a new LLM client is created from config.

    Returns
    -------
    Pipeline object with a `.invoke(state)` method.
    """
    _LANGGRAPH_AVAILABLE = False
    try:
        from langgraph.graph import END, START, StateGraph  # noqa: F401
        _LANGGRAPH_AVAILABLE = True
    except ImportError:
        logger.warning(
            "langgraph not installed — using sequential fallback pipeline. "
            "Install with: pip install langgraph"
        )

    from agent.nodes import context_builder, query_executor, query_optimizer, result_formatter, sql_validator

    # Resolve LLM if needed.
    # Vertex AI uses no API key (authenticates via service account / ADC), so we
    # check the provider explicitly instead of relying on llm_api_key being set.
    _provider = getattr(config, "llm_provider", "").lower()
    _has_credentials = bool(getattr(config, "llm_api_key", "")) or (_provider == "vertex")
    if llm is None and _has_credentials:
        try:
            from agent.llm import get_llm
            llm = get_llm(config)
            logger.info("LLM client created: provider=%s", config.llm_provider)
        except Exception as exc:
            logger.warning("Could not create LLM client: %s — using no-LLM fallbacks", exc)
            llm = None

    # Resolve node functions
    intent_fn = None
    entity_fn = None
    gen_fn = None
    if llm:
        try:
            from agent.nodes import intent_classifier, entity_extractor, sql_generator
            intent_fn = intent_classifier.make_intent_classifier(llm)
            entity_fn = entity_extractor.make_entity_extractor(llm)
            gen_fn = sql_generator.make_sql_generator(llm)
        except Exception as exc:
            logger.warning("LLM node setup failed: %s", exc)
            intent_fn = entity_fn = gen_fn = None

    intent_node   = intent_fn   if intent_fn   else _default_intent
    entity_node   = entity_fn   if entity_fn   else _default_entities
    schema_node   = context_builder.make_context_builder(graph)
    gen_node      = gen_fn      if gen_fn      else _no_llm_sql_generator
    valid_node    = sql_validator.make_sql_validator()
    opt_node      = query_optimizer.make_query_optimizer()
    exec_node     = query_executor.make_query_executor(config)
    format_node   = result_formatter.make_result_formatter()

    if not _LANGGRAPH_AVAILABLE:
        # ------------------------------------------------------------------ #
        # Fallback: simple sequential pipeline without LangGraph
        # ------------------------------------------------------------------ #
        class _SequentialPipeline:
            """Simple sequential pipeline that runs each node in order."""

            def __init__(self, nodes, max_retries: int = 3):
                self._nodes = nodes
                self._max_retries = max_retries

            def invoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
                state = dict(state)
                for name, node_fn in self._nodes:
                    if name == "retry_loop":
                        # SQL generation → validation retry loop
                        for attempt in range(self._max_retries + 1):
                            state = _update(state, gen_node(state))
                            state = _update(state, valid_node(state))
                            if state.get("validation_passed"):
                                break
                            if attempt >= self._max_retries:
                                break  # give up, proceed with invalid SQL
                            state["retry_count"] = state.get("retry_count", 0) + 1
                    else:
                        try:
                            state = _update(state, node_fn(state))
                        except Exception as exc:
                            logger.error("Node %s failed: %s", name, exc)
                            state["error"] = str(exc)
                return state

        def _update(state: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
            merged = dict(state)
            merged.update(patch)
            return merged

        pipeline_nodes = [
            ("classify_intent", intent_node),
            ("extract_entities", entity_node),
            ("retrieve_schema", schema_node),
            ("retry_loop", None),   # special marker handled above
            ("optimize_sql", opt_node),
            ("execute_query", exec_node),
            ("format_result", format_node),
        ]

        logger.info("Sequential fallback pipeline ready (llm=%s)", "yes" if llm else "no")
        return _SequentialPipeline(pipeline_nodes)

    # ------------------------------------------------------------------ #
    # Full LangGraph pipeline
    # ------------------------------------------------------------------ #
    from langgraph.graph import END, StateGraph
    from agent.state import AgentState

    workflow = StateGraph(AgentState)
    workflow.add_node("classify_intent", intent_node)
    workflow.add_node("extract_entities", entity_node)
    workflow.add_node("retrieve_schema", schema_node)
    workflow.add_node("generate_sql", gen_node)
    workflow.add_node("validate_sql", valid_node)
    workflow.add_node("optimize_sql", opt_node)
    workflow.add_node("execute_query", exec_node)
    workflow.add_node("format_result", format_node)

    workflow.set_entry_point("classify_intent")
    workflow.add_edge("classify_intent", "extract_entities")
    workflow.add_edge("extract_entities", "retrieve_schema")
    workflow.add_edge("retrieve_schema", "generate_sql")
    workflow.add_edge("generate_sql", "validate_sql")

    def route_after_validation(state: Dict[str, Any]) -> str:
        if state.get("validation_passed"):
            return "optimize"
        retry_count = state.get("retry_count", 0)
        if retry_count >= 3:
            return "force_optimize"  # give up retrying; proceed anyway
        return "retry"

    workflow.add_conditional_edges(
        "validate_sql",
        route_after_validation,
        {
            "optimize": "optimize_sql",
            "retry": "generate_sql",
            "force_optimize": "optimize_sql",
        },
    )

    workflow.add_edge("optimize_sql", "execute_query")
    workflow.add_edge("execute_query", "format_result")
    workflow.add_edge("format_result", END)

    compiled = workflow.compile()
    logger.info("LangGraph pipeline compiled (llm=%s)", "yes" if llm else "no-llm fallback")
    return compiled


# ---------------------------------------------------------------------------
# High-level query runner
# ---------------------------------------------------------------------------

def run_query(
    pipeline,
    user_input: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Run a natural-language query through the compiled pipeline.

    Parameters
    ----------
    pipeline : compiled LangGraph app
        Output of build_pipeline().
    user_input : str
        The user's natural-language question.
    conversation_history : list[dict] | None
        Previous chat turns: [{"role": "user"|"assistant", "content": "..."}]

    Returns
    -------
    dict
        Formatted result dict with keys: type, summary, sql, columns, rows, etc.
    """
    from agent.state import AgentState

    initial_state: AgentState = {
        "user_input": user_input,
        "conversation_history": conversation_history or [],
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

    try:
        result = pipeline.invoke(initial_state)

        formatted = result.get("formatted_response", "")
        if formatted:
            try:
                return json.loads(formatted)
            except Exception:
                return {
                    "type": "query_result",
                    "summary": formatted,
                    "sql": result.get("optimized_sql", ""),
                    "explanation": result.get("sql_explanation", ""),
                    "columns": [],
                    "rows": [],
                    "total_rows": 0,
                    "execution_time_ms": 0,
                    "data_source": "unknown",
                    "schema_context_tables": [],
                    "validation_errors": result.get("validation_errors", []),
                }

        if result.get("error"):
            return {
                "type": "error",
                "summary": result["error"],
                "sql": result.get("optimized_sql", result.get("generated_sql", "")),
                "explanation": result.get("sql_explanation", ""),
                "columns": [],
                "rows": [],
                "total_rows": 0,
                "execution_time_ms": 0,
                "data_source": "none",
                "schema_context_tables": [],
                "validation_errors": result.get("validation_errors", []),
            }

        # Unexpected empty response
        return {
            "type": "error",
            "summary": "The pipeline completed but produced no output.",
            "sql": result.get("optimized_sql", ""),
            "explanation": "",
            "columns": [],
            "rows": [],
            "total_rows": 0,
            "execution_time_ms": 0,
            "data_source": "none",
            "schema_context_tables": [],
            "validation_errors": [],
        }

    except Exception as exc:
        logger.error("Pipeline invocation failed: %s", exc, exc_info=True)
        return {
            "type": "error",
            "summary": str(exc),
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
