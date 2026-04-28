"""
KnowledgeQL FastAPI Backend
============================
Entry point:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000

Lifespan sequence:
  1. Load AppConfig from environment
  2. Load KnowledgeGraph from disk cache — or build from Oracle
  3. Build LangGraph pipeline
  4. Schedule background tasks: LLM enhancement, knowledge file generation
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app_config import AppConfig
from knowledge_graph.graph_cache import (
    get_cache_path, invalidate_cache, load_graph, save_graph,
)
from knowledge_graph.init_graph import initialize_graph

from backend.routers import admin, health, query, schema, sql, graph as graph_router
from backend.routers import prompts as prompts_router
from backend.routers import kyc_agent as kyc_agent_router
from backend.routers import signals as signals_router

_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("backend.main")


# ---------------------------------------------------------------------------
# Graph bundle (mirrors _GraphBundle from app.py)
# ---------------------------------------------------------------------------

class _GraphBundle:
    __slots__ = ("graph", "llm_enhanced")

    def __init__(self, graph, llm_enhanced: bool = False):
        self.graph = graph
        self.llm_enhanced = llm_enhanced


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

def _load_or_build_graph(config: AppConfig) -> _GraphBundle:
    """Load graph from cache or build from Oracle. Always returns a bundle."""
    cache_path = get_cache_path(config.graph)
    max_age = float(os.getenv("GRAPH_CACHE_TTL_HOURS", "0")) or None

    # Try cache first
    cached = load_graph(cache_path, max_age_hours=max_age)
    if cached is not None:
        graph, llm_enhanced = cached
        logger.info("Graph loaded from cache (%s, llm_enhanced=%s)", cache_path, llm_enhanced)
        return _GraphBundle(graph, llm_enhanced)

    # Build from Oracle
    logger.info("Cache miss — building graph from Oracle…")
    graph, report = initialize_graph(config.graph)
    if report.get("success"):
        save_graph(graph, cache_path, llm_enhanced=False)
        logger.info("Graph built and cached (%d tables)", graph.count_nodes("Table"))
    else:
        logger.warning("Graph build incomplete — check Oracle connectivity")
    return _GraphBundle(graph, False)


async def _background_tasks(app: FastAPI) -> None:
    """
    Post-startup background coroutine:
      1. LLM-enhance the graph (if not already done and LLM creds present)
      2. Generate knowledge file (if empty and LLM creds present)
      3. Rebuild pipeline with fresh knowledge file
      4. LLM-analyze business docs for rich knowledge entries
    """
    state = app.state
    config: AppConfig = state.config
    llm = state.llm

    # ---- LLM graph enhancement ----------------------------------------
    if not state.graph_llm_enhanced and llm is not None:
        logger.info("Starting LLM graph enhancement in background…")
        try:
            from knowledge_graph.llm_enhancer import enhance_graph_with_llm
            await anyio.to_thread.run_sync(
                lambda: enhance_graph_with_llm(state.graph, llm)
            )
            state.graph_llm_enhanced = True
            # Re-save enriched graph
            cache_path = get_cache_path(config.graph)
            await anyio.to_thread.run_sync(
                lambda: save_graph(state.graph, cache_path, llm_enhanced=True)
            )
            logger.info("LLM graph enhancement complete")
        except Exception as exc:
            logger.warning("LLM enhancement failed (graph still usable): %s", exc)

    # ---- Knowledge file generation ------------------------------------
    knowledge_file = os.getenv("KYC_KNOWLEDGE_FILE", "kyc_business_knowledge.txt")
    file_empty = not (os.path.isfile(knowledge_file) and os.path.getsize(knowledge_file) > 0)

    if file_empty and llm is not None:
        logger.info("Knowledge file is empty — generating via LLM…")
        try:
            from knowledge_graph.knowledge_generator import generate_knowledge_file
            ok = await anyio.to_thread.run_sync(
                lambda: generate_knowledge_file(state.graph, llm, knowledge_file)
            )
            if ok:
                logger.info("Knowledge file generated: %s", knowledge_file)
                # Clear the LRU cache so the next build reads the new content
                from agent.nodes.query_enricher import _load_knowledge
                _load_knowledge.cache_clear()
                # Rebuild pipeline so enricher reads fresh file
                from agent.pipeline import build_pipeline
                state.pipeline = await anyio.to_thread.run_sync(
                    lambda: build_pipeline(state.graph, config, llm)
                )
                logger.info("Pipeline rebuilt with fresh knowledge file")
        except Exception as exc:
            logger.warning("Knowledge file generation failed: %s", exc)

    # ---- LLM business document analysis ----------------------------------
    if llm is not None:
        docs_dir = getattr(state, "docs_dir", "kyc_business_knowledge_agentic")
        knowledge_store = getattr(state, "knowledge_store", None)
        if knowledge_store:
            logger.info("Starting LLM business document analysis in background…")
            try:
                from agent.llm_knowledge_analyzer import get_cached_or_analyze
                cache_dir = os.getenv("GRAPH_CACHE_PATH", "")
                llm_entries = await anyio.to_thread.run_sync(
                    lambda: get_cached_or_analyze(llm, docs_dir, cache_dir or None)
                )
                if llm_entries:
                    knowledge_store.replace_entries_by_source("llm_analysis", llm_entries)
                    logger.info(
                        "LLM document analysis complete: %d rich entries added",
                        len(llm_entries),
                    )
            except Exception as exc:
                logger.warning("LLM document analysis failed (regex entries still active): %s", exc)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build graph + pipeline at startup; schedule background enhancement."""
    config = AppConfig()
    app.state.config = config
    app.state.oracle_connected = False
    app.state.graph_llm_enhanced = False

    # Restore any user-edited prompts from the persistent volume before pipeline build
    try:
        from agent.prompts import load_persisted_prompts
        restored = load_persisted_prompts()
        if restored:
            logger.info("Restored %d persisted prompt(s) from volume", restored)
    except Exception as exc:
        logger.warning("Could not restore persisted prompts: %s", exc)

    # LLM client
    llm = None
    provider = getattr(config, "llm_provider", "").lower()
    has_creds = bool(getattr(config, "llm_api_key", "")) or provider == "vertex"
    if has_creds:
        try:
            from agent.llm import get_llm
            llm = get_llm(config)
            logger.info("LLM client ready: provider=%s model=%s", config.llm_provider, config.llm_model)
        except Exception as exc:
            logger.warning("LLM client failed — running without LLM: %s", exc)
    app.state.llm = llm

    # Load / build knowledge graph (blocking Oracle I/O — run in thread)
    logger.info("Loading knowledge graph…")
    bundle: _GraphBundle = await anyio.to_thread.run_sync(
        lambda: _load_or_build_graph(config)
    )
    app.state.graph = bundle.graph
    app.state.graph_llm_enhanced = bundle.llm_enhanced
    app.state.oracle_connected = bundle.graph.count_nodes("Table") > 0

    # Initialize KYC Knowledge Store
    logger.info("Initializing KYC knowledge store…")
    from agent.knowledge_store import KYCKnowledgeStore
    from agent.business_doc_loader import load_all_business_knowledge
    knowledge_store = KYCKnowledgeStore()
    docs_dir = os.getenv("KYC_DOCS_DIR", "kyc_business_knowledge_agentic")
    biz_entries = await anyio.to_thread.run_sync(
        lambda: load_all_business_knowledge(docs_dir)
    )
    knowledge_store.add_static_entries(biz_entries)
    app.state.knowledge_store = knowledge_store
    app.state.docs_dir = docs_dir  # for background LLM analysis
    config._knowledge_store = knowledge_store  # type: ignore[attr-defined]
    logger.info("Knowledge store ready: %d static entries, %d learned patterns",
                len(knowledge_store.static_entries), len(knowledge_store.learned_patterns))

    # Build pipeline
    logger.info("Building NL-to-SQL pipeline…")
    from agent.pipeline import build_pipeline
    pipeline = await anyio.to_thread.run_sync(
        lambda: build_pipeline(bundle.graph, config, llm)
    )
    app.state.pipeline = pipeline
    logger.info("Pipeline ready — server accepting requests")

    # Background: LLM enhancement + knowledge file (non-blocking)
    bg_task = asyncio.create_task(_background_tasks(app))

    yield  # ---- server is running ----

    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="KnowledgeQL API",
    description="NLP-to-SQL backend for Oracle databases",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten in production with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(sql.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(graph_router.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(prompts_router.router, prefix="/api")
app.include_router(kyc_agent_router.router, prefix="/api")
app.include_router(signals_router.router, prefix="/api")

# ---------------------------------------------------------------------------
# Static file serving — React SPA (must come AFTER all API routers)
# ---------------------------------------------------------------------------

_DIST = Path(__file__).resolve().parent.parent / "dist"

if _DIST.is_dir():
    # Mount /assets with long-cache headers for hashed JS/CSS bundles
    _assets = _DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """Serve existing files verbatim; everything else → index.html (SPA routing)."""
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
