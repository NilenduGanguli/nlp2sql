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
from backend.models import CacheInfoResponse, ConfigResponse, ConfigUpdateRequest, RebuildResponse
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
