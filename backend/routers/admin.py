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
