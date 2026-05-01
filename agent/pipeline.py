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
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# No-LLM fallback nodes (used when no API key is provided)
# ---------------------------------------------------------------------------

def _default_intent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback intent node: always DATA_QUERY."""
    return {**state, "intent": "DATA_QUERY", "step": "intent_classified"}


def _default_enrich(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback enricher (no LLM): passes user_input through to enriched_query."""
    return {**state, "enriched_query": state.get("user_input", ""), "step": "query_enriched"}


def _default_clarify(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback clarification node (no LLM): skip clarification, proceed to SQL."""
    return {
        **state,
        "need_clarification": False,
        "clarification_question": "",
        "clarification_options": [],
        "clarification_context": "",
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
    enrich_fn = None
    clarify_fn = None
    if llm:
        try:
            from agent.nodes import intent_classifier, entity_extractor, sql_generator
            from agent.nodes.query_enricher import make_query_enricher
            from agent.nodes.clarification_agent import make_clarification_agent
            intent_fn = intent_classifier.make_intent_classifier(llm)
            entity_fn = entity_extractor.make_entity_extractor(llm, graph=graph, config=config)
            gen_fn = sql_generator.make_sql_generator(llm)
            enrich_fn = make_query_enricher(llm)
            clarify_fn = make_clarification_agent(llm)
        except Exception as exc:
            logger.warning("LLM node setup failed: %s", exc)
            intent_fn = entity_fn = gen_fn = enrich_fn = clarify_fn = None

    # Build graph-aware no-LLM fallback nodes.
    # These close over `graph` so they search actual table names rather than
    # relying on a hardcoded KYC fixture.
    #
    # Pre-compute the schema summary once — graph is immutable after build.
    from agent.nodes.entity_extractor import _fallback_extract, _build_schema_summary
    _, _fallback_table_names, _ = _build_schema_summary(graph)

    def _graph_default_entities(state: Dict[str, Any]) -> Dict[str, Any]:
        """Graph-aware entity fallback: keyword-match against real table names."""
        entities = _fallback_extract(state.get("user_input", ""), _fallback_table_names)
        return {**state, "entities": entities, "step": "entities_extracted"}

    def _graph_fallback_sql(state: Dict[str, Any]) -> Dict[str, Any]:
        """Graph-aware SQL fallback: extracts FQN from schema_context DDL."""
        from agent.nodes.sql_generator import _extract_fqn_from_context
        entities = state.get("entities", {})
        schema_context = state.get("schema_context", "")
        tables = entities.get("tables", [])
        hint_name = (tables[0] if tables else "").upper()
        conditions = entities.get("conditions", [])
        aggregations = entities.get("aggregations", [])

        fqn = _extract_fqn_from_context(schema_context, hint_name) or hint_name or "UNKNOWN_TABLE"

        table_alias = "t"
        if "COUNT" in aggregations:
            sql = f"SELECT COUNT(*) AS TOTAL_COUNT FROM {fqn} {table_alias}"
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            explanation = f"Counts records in {fqn}"
        else:
            sql = f"SELECT {table_alias}.* FROM {fqn} {table_alias}"
            if conditions:
                sql += " WHERE " + " AND ".join(
                    f"{table_alias}.{c}" if "." not in c else c for c in conditions
                )
            sql += " FETCH FIRST 100 ROWS ONLY"
            explanation = f"Retrieves records from {fqn}"
            if conditions:
                explanation += f" matching: {', '.join(conditions)}"

        return {
            **state,
            "generated_sql":    sql,
            "sql_explanation":  explanation,
            "validation_passed": True,
            "step": "sql_generated",
        }

    enrich_node  = (enrich_fn if (enrich_fn and getattr(config, "query_enricher_enabled", True)) else _default_enrich)
    clarify_node = clarify_fn if clarify_fn else _default_clarify
    intent_node  = intent_fn    if intent_fn    else _default_intent
    entity_node  = entity_fn    if entity_fn    else _graph_default_entities
    schema_node = context_builder.make_context_builder(graph, config=config)
    gen_node    = gen_fn       if gen_fn       else _graph_fallback_sql
    # Resolve loaded ValueCache once (Phase 2 — Layer 3 literal grounding).
    # The cache is set as a process-wide singleton by app.py / backend.main
    # at startup; we read it from there so the validator hits the same data
    # the entity extractor and DDL annotator already use.
    _value_cache = None
    _vc_cfg = getattr(getattr(config, "graph", None), "value_cache", None)
    if _vc_cfg is None or getattr(_vc_cfg, "validator_enabled", True):
        try:
            from knowledge_graph.column_value_cache import _loaded_cache as _shared_vc
            _value_cache = _shared_vc
        except Exception:
            _value_cache = None
    _fuzzy_threshold = getattr(_vc_cfg, "fuzzy_threshold", 0.85) if _vc_cfg else 0.85
    valid_node  = sql_validator.make_sql_validator(
        graph=graph,
        value_cache=_value_cache,
        fuzzy_threshold=_fuzzy_threshold,
    )
    opt_node    = query_optimizer.make_query_optimizer()
    exec_node   = query_executor.make_query_executor(config)
    format_node = result_formatter.make_result_formatter()

    # SQL presenter: packages SQL for user review (confirm-before-execute)
    from agent.nodes.sql_presenter import make_sql_presenter
    present_node = make_sql_presenter()

    # KYC Business Agent: auto-answers clarification questions from knowledge base
    kyc_agent_node = None
    _knowledge_store = getattr(config, "_knowledge_store", None)
    if _knowledge_store:
        from agent.nodes.kyc_business_agent import make_kyc_business_agent
        kyc_agent_node = make_kyc_business_agent(llm=llm, knowledge_store=_knowledge_store)

    # Session lookup: short-circuits clarification when a prior query_session entry matches
    session_lookup_node = None
    if _knowledge_store is not None:
        from agent.nodes.session_lookup import make_session_lookup
        if str(getattr(config, "session_learning_enabled", True)).lower() != "false":
            session_lookup_node = make_session_lookup(_knowledge_store, graph)

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
            ("enrich_query",        enrich_node),
            ("classify_intent",     intent_node),
            ("extract_entities",    entity_node),
            ("retrieve_schema",     schema_node),
            ("check_clarification", clarify_node),  # pass-through in sequential mode
            ("retry_loop", None),   # special marker handled above
            ("optimize_sql",        opt_node),
            ("present_sql",         present_node),
            ("execute_query",       exec_node),
            ("format_result",       format_node),
        ]

        if session_lookup_node:
            pipeline_nodes.insert(
                next(i for i, (n, _) in enumerate(pipeline_nodes) if n == "check_clarification"),
                ("session_lookup", session_lookup_node),
            )

        logger.info("Sequential fallback pipeline ready (llm=%s)", "yes" if llm else "no")
        return _SequentialPipeline(pipeline_nodes)

    # ------------------------------------------------------------------ #
    # Full LangGraph pipeline
    # ------------------------------------------------------------------ #
    from langgraph.graph import END, StateGraph
    from agent.state import AgentState

    workflow = StateGraph(AgentState)
    workflow.add_node("enrich_query",        enrich_node)
    workflow.add_node("classify_intent",     intent_node)
    workflow.add_node("extract_entities",    entity_node)
    workflow.add_node("retrieve_schema",     schema_node)
    workflow.add_node("check_clarification", clarify_node)
    if kyc_agent_node:
        workflow.add_node("kyc_business_agent", kyc_agent_node)
    if session_lookup_node:
        workflow.add_node("session_lookup", session_lookup_node)
    workflow.add_node("generate_sql",        gen_node)
    workflow.add_node("validate_sql",        valid_node)
    workflow.add_node("optimize_sql",        opt_node)
    workflow.add_node("present_sql",         present_node)
    workflow.add_node("execute_query",       exec_node)
    workflow.add_node("format_result",       format_node)

    workflow.set_entry_point("enrich_query")
    workflow.add_edge("enrich_query",     "classify_intent")
    workflow.add_edge("classify_intent",  "extract_entities")
    workflow.add_edge("extract_entities", "retrieve_schema")
    if session_lookup_node:
        workflow.add_edge("retrieve_schema", "session_lookup")
        workflow.add_conditional_edges(
            "session_lookup",
            lambda s: "skip_to_present" if s.get("has_candidates") else "clarify",
            {"skip_to_present": "present_sql", "clarify": "check_clarification"},
        )
    else:
        workflow.add_edge("retrieve_schema", "check_clarification")

    # After clarification check:
    # - needs_clarification=true AND kyc_agent available → try kyc_business_agent
    # - needs_clarification=true AND no kyc_agent → END (user sees clarification)
    # - needs_clarification=false OR RESULT_FOLLOWUP → generate_sql
    if kyc_agent_node:
        workflow.add_conditional_edges(
            "check_clarification",
            lambda s: "kyc_agent" if (
                s.get("need_clarification") and s.get("intent") != "RESULT_FOLLOWUP"
            ) else "generate_sql",
            {"kyc_agent": "kyc_business_agent", "generate_sql": "generate_sql"},
        )
        # After KYC agent: auto-answered → generate_sql, else → END (user sees clarification)
        workflow.add_conditional_edges(
            "kyc_business_agent",
            lambda s: "generate_sql" if s.get("kyc_auto_answered") else "end",
            {"generate_sql": "generate_sql", "end": END},
        )
    else:
        workflow.add_conditional_edges(
            "check_clarification",
            lambda s: "clarify" if (
                s.get("need_clarification") and s.get("intent") != "RESULT_FOLLOWUP"
            ) else "generate_sql",
            {"clarify": END, "generate_sql": "generate_sql"},
        )

    # After generate_sql: if multiple candidates detected → END (user picks), else → validate
    workflow.add_conditional_edges(
        "generate_sql",
        lambda s: "end" if s.get("has_candidates") else "validate",
        {"end": END, "validate": "validate_sql"},
    )

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

    # After optimization: present SQL for review or auto-execute
    workflow.add_conditional_edges(
        "optimize_sql",
        lambda s: "present" if s.get("skip_execution", True) else "execute",
        {"present": "present_sql", "execute": "execute_query"},
    )
    workflow.add_edge("present_sql", END)
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
        "skip_execution": True,
        "previous_sql_context": {},
        "_trace": [],
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
