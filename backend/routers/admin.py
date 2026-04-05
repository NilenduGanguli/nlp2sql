"""
Admin endpoints — graph cache management and rebuild control.
"""
from __future__ import annotations

import asyncio
import logging
import os

import anyio
from fastapi import APIRouter, Depends, Request

from backend.deps import get_config, get_graph
from backend.models import (
    CacheInfoResponse, ConfigResponse, ConfigUpdateRequest,
    KnowledgeFileResponse, RebuildResponse,
)
from knowledge_graph.graph_cache import (
    cache_info, get_cache_path, invalidate_cache, load_graph, save_graph,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/cache-info", response_model=CacheInfoResponse)
async def get_cache_info(config=Depends(get_config)):
    """Return metadata about the current graph cache file."""
    cache_path = get_cache_path(config.graph)
    info = cache_info(cache_path)
    if info is None:
        return CacheInfoResponse(path=cache_path, exists=False)
    return CacheInfoResponse(
        path=cache_path,
        exists=True,
        created_at=info.get("created_at"),
        age_hours=info.get("age_hours"),
        llm_enhanced=info.get("llm_enhanced"),
        size_mb=info.get("size_mb"),
        version=info.get("format_version"),
    )


@router.post("/rebuild", response_model=RebuildResponse)
async def rebuild_graph(request: Request, config=Depends(get_config)):
    """
    Invalidate the disk cache and rebuild the knowledge graph from Oracle.
    The rebuild runs in the background; poll GET /api/health for completion.
    The frontend should call queryClient.invalidateQueries(['schema', 'graph']) after
    receiving a rebuild-complete signal (when health.graph_loaded switches back to True).
    """
    cache_path = get_cache_path(config.graph)
    invalidate_cache(cache_path)

    app = request.app

    async def _rebuild_bg():
        logger.info("Graph rebuild triggered by admin endpoint")
        app.state.oracle_connected = False   # signal rebuilding to health check

        try:
            from knowledge_graph.init_graph import initialize_graph
            from agent.pipeline import build_pipeline

            # Build graph in thread (blocking Oracle I/O)
            graph, report = await anyio.to_thread.run_sync(
                lambda: initialize_graph(config.graph)
            )
            app.state.graph = graph
            app.state.graph_llm_enhanced = False
            app.state.oracle_connected = graph.count_nodes("Table") > 0

            # Save to cache
            await anyio.to_thread.run_sync(
                lambda: save_graph(graph, cache_path, llm_enhanced=False)
            )

            # Rebuild pipeline
            llm = getattr(app.state, "llm", None)
            pipeline = await anyio.to_thread.run_sync(
                lambda: build_pipeline(graph, config, llm)
            )
            app.state.pipeline = pipeline
            logger.info("Graph rebuild complete: %d tables", graph.count_nodes("Table"))

            # Schedule LLM enhancement again if LLM present
            if llm:
                asyncio.create_task(_enhance_bg(app, config, cache_path, llm))

        except Exception as exc:
            logger.error("Graph rebuild failed: %s", exc, exc_info=True)
            app.state.oracle_connected = False

    async def _enhance_bg(app, config, cache_path, llm):
        try:
            from knowledge_graph.llm_enhancer import enhance_graph_with_llm
            await anyio.to_thread.run_sync(
                lambda: enhance_graph_with_llm(app.state.graph, llm)
            )
            app.state.graph_llm_enhanced = True
            await anyio.to_thread.run_sync(
                lambda: save_graph(app.state.graph, cache_path, llm_enhanced=True)
            )
        except Exception as exc:
            logger.warning("Post-rebuild LLM enhancement failed: %s", exc)

    asyncio.create_task(_rebuild_bg())
    return RebuildResponse(
        status="started",
        message="Graph rebuild started in background. Poll GET /api/health to track progress.",
    )


@router.post("/rebuild-pipeline", response_model=RebuildResponse)
async def rebuild_pipeline_only(request: Request, config=Depends(get_config)):
    """
    Rebuild the LLM pipeline without touching the graph or Oracle.
    Picks up any prompt file edits saved to disk since the last build.
    """
    app = request.app

    async def _rebuild():
        try:
            from agent.pipeline import build_pipeline
            llm = getattr(app.state, "llm", None)
            pipeline = await anyio.to_thread.run_sync(
                lambda: build_pipeline(app.state.graph, config, llm)
            )
            app.state.pipeline = pipeline
            logger.info("Pipeline rebuilt (prompts reloaded)")
        except Exception as exc:
            logger.error("Pipeline-only rebuild failed: %s", exc, exc_info=True)

    asyncio.create_task(_rebuild())
    return RebuildResponse(
        status="started",
        message="Pipeline rebuild started. New prompts will take effect within seconds.",
    )


@router.get("/config", response_model=ConfigResponse)
async def get_llm_config(config=Depends(get_config)):
    """Return the current LLM configuration (API key masked)."""
    return ConfigResponse(
        llm_provider=config.llm_provider,
        llm_model=config.llm_model,
        has_api_key=bool(config.llm_api_key),
        vertex_project=getattr(config, "vertex_project", ""),
        vertex_location=getattr(config, "vertex_location", "us-central1"),
    )


@router.post("/config", response_model=RebuildResponse)
async def update_llm_config(
    body: ConfigUpdateRequest,
    request: Request,
    config=Depends(get_config),
):
    """Update LLM provider/model/key and rebuild the pipeline."""
    app = request.app

    # Mutate the singleton config in-place
    config.llm_provider = body.llm_provider
    config.llm_model = body.llm_model
    if body.llm_api_key:
        config.llm_api_key = body.llm_api_key

    async def _rebuild_pipeline():
        try:
            from agent.llm import get_llm as _get_llm
            from agent.pipeline import build_pipeline

            llm = await anyio.to_thread.run_sync(lambda: _get_llm(config))
            app.state.llm = llm
            pipeline = await anyio.to_thread.run_sync(
                lambda: build_pipeline(app.state.graph, config, llm)
            )
            app.state.pipeline = pipeline
            logger.info(
                "Pipeline rebuilt with provider=%s model=%s",
                body.llm_provider,
                body.llm_model,
            )
        except Exception as exc:
            logger.error("Pipeline rebuild after config update failed: %s", exc, exc_info=True)

    asyncio.create_task(_rebuild_pipeline())
    return RebuildResponse(
        status="started",
        message=f"LLM updated to {body.llm_provider}/{body.llm_model}. Pipeline rebuilding.",
    )


def _knowledge_file_path() -> str:
    return os.getenv("KYC_KNOWLEDGE_FILE", "kyc_business_knowledge.txt")


@router.get("/agent-config")
async def get_agent_config():
    """
    Return the static agent/pipeline configuration for display in the Prompt Studio.
    Includes pipeline DAG structure, entity extractor tool specs, and tuneable constants.
    """
    from agent.nodes.entity_extractor import MAX_TOOL_CALLS, _ORACLE_MAX_ROWS

    pipeline_nodes = [
        {"node": "enrich_query",        "label": "Query Enricher",           "prompt": "query_enricher_system",       "type": "llm",   "description": "Expands domain-sparse queries with KYC business knowledge"},
        {"node": "classify_intent",     "label": "Intent Classifier",        "prompt": "intent_classifier_system",    "type": "llm",   "description": "Classifies query as DATA_QUERY / SCHEMA_EXPLORE / QUERY_EXPLAIN / QUERY_REFINE / RESULT_FOLLOWUP (uses conversation history for context)"},
        {"node": "extract_entities",    "label": "Entity Extractor",         "prompt": "entity_extractor_system",     "type": "agent", "description": f"Agentic ReAct loop; up to {MAX_TOOL_CALLS} tool calls; resolves tables, columns, conditions"},
        {"node": "retrieve_schema",     "label": "Schema Retrieval",         "prompt": None,                          "type": "graph", "description": "Builds DDL context from entity FQNs + join-path hints"},
        {"node": "check_clarification", "label": "Clarification Check",      "prompt": "clarification_agent_system",  "type": "llm",   "description": "Checks if query is still ambiguous; emits clarification event if so"},
        {"node": "generate_sql",        "label": "SQL Generator",            "prompt": "sql_generator_system",        "type": "llm",   "description": "Generates Oracle SQL from DDL context + enriched query"},
        {"node": "validate_sql",        "label": "SQL Validator",            "prompt": None,                          "type": "rule",  "description": "Rule-based: sqlglot parse, blocked keywords, Cartesian product guard, column existence check (graph-powered)"},
        {"node": "optimize_sql",        "label": "SQL Optimizer",            "prompt": None,                          "type": "rule",  "description": "Injects FETCH FIRST row limit, strips trailing semi-colon, adds index hints"},
        {"node": "execute_query",       "label": "Query Executor",           "prompt": None,                          "type": "oracle","description": "Runs final SQL against live Oracle; returns columns + rows"},
        {"node": "format_result",       "label": "Result Formatter",         "prompt": None,                          "type": "rule",  "description": "Serialises result + trace for SSE transport"},
    ]

    pipeline_edges = [
        {"from": "enrich_query",        "to": "classify_intent",     "condition": "always"},
        {"from": "classify_intent",     "to": "extract_entities",    "condition": "always"},
        {"from": "extract_entities",    "to": "retrieve_schema",     "condition": "always"},
        {"from": "retrieve_schema",     "to": "check_clarification", "condition": "always"},
        {"from": "check_clarification", "to": "END",                 "condition": "need_clarification=True AND no conversation history"},
        {"from": "check_clarification", "to": "generate_sql",        "condition": "need_clarification=False OR conversation history present"},
        {"from": "generate_sql",        "to": "validate_sql",        "condition": "always"},
        {"from": "validate_sql",        "to": "generate_sql",        "condition": "validation_passed=False AND retry_count < 3"},
        {"from": "validate_sql",        "to": "optimize_sql",        "condition": "validation_passed=True OR retry_count >= 3"},
        {"from": "optimize_sql",        "to": "execute_query",       "condition": "always"},
        {"from": "execute_query",       "to": "format_result",       "condition": "always"},
        {"from": "format_result",       "to": "END",                 "condition": "always"},
    ]

    entity_extractor_tools = [
        {"name": "search_schema",         "color": "#38bdf8", "description": "Fuzzy/keyword search across table names and column names"},
        {"name": "get_table_detail",      "color": "#a78bfa", "description": "Full column list with data types, PKs, FK references for one table"},
        {"name": "find_join_path",        "color": "#fb923c", "description": "FK-based join columns between two specific tables"},
        {"name": "resolve_business_term", "color": "#34d399", "description": "Map business/domain language (e.g. 'KYC check') to schema objects"},
        {"name": "list_related_tables",   "color": "#60a5fa", "description": "List all FK-reachable tables from a seed table"},
        {"name": "query_oracle",          "color": "#f472b6", "description": f"Execute a read-only SELECT against the live Oracle DB (max {_ORACLE_MAX_ROWS} rows). Use to inspect actual data values, check filter conditions, or query data dictionary views."},
        {"name": "submit_entities",       "color": "#4ade80", "description": "Finalise entity extraction — tables, columns, conditions, confirmed FQNs"},
    ]

    return {
        "pipeline_nodes": pipeline_nodes,
        "pipeline_edges": pipeline_edges,
        "entity_extractor": {
            "max_tool_calls": MAX_TOOL_CALLS,
            "oracle_max_rows": _ORACLE_MAX_ROWS,
            "tools": entity_extractor_tools,
            "protocol": "JSON {thought, action, args} — works with all LLM providers",
            "fallback": "keyword matching when LLM or agentic loop unavailable",
        },
    }


@router.get("/knowledge-file", response_model=KnowledgeFileResponse)
async def get_knowledge_file(config=Depends(get_config)):
    """Return the current business knowledge file content."""
    path = _knowledge_file_path()
    try:
        content = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        content = ""
    return KnowledgeFileResponse(
        content=content,
        path=path,
        size_bytes=len(content.encode("utf-8")),
        enricher_enabled=getattr(config, "query_enricher_enabled", True),
    )


@router.post("/knowledge-file/regenerate", response_model=RebuildResponse)
async def regenerate_knowledge_file(request: Request, config=Depends(get_config)):
    """Regenerate the business knowledge file from the graph (requires LLM)."""
    from fastapi import HTTPException

    app = request.app
    llm = getattr(app.state, "llm", None)
    if not llm:
        raise HTTPException(status_code=503, detail="LLM not configured — cannot regenerate knowledge file")

    async def _regen():
        path = _knowledge_file_path()
        try:
            from knowledge_graph.knowledge_generator import generate_knowledge_file
            from agent.nodes.query_enricher import _load_knowledge
            from agent.pipeline import build_pipeline

            ok = await anyio.to_thread.run_sync(
                lambda: generate_knowledge_file(app.state.graph, llm, path)
            )
            if ok:
                _load_knowledge.cache_clear()
                app.state.pipeline = await anyio.to_thread.run_sync(
                    lambda: build_pipeline(app.state.graph, config, llm)
                )
                logger.info("Knowledge file regenerated and pipeline rebuilt")
        except Exception as exc:
            logger.error("Knowledge file regeneration failed: %s", exc, exc_info=True)

    asyncio.create_task(_regen())
    return RebuildResponse(
        status="started",
        message="Knowledge file regeneration started. Refresh in ~30s.",
    )
