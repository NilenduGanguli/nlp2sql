"""Health and status endpoints."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request

from backend.models import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Overall system health: graph, LLM, Oracle, knowledge file."""
    app = request.app
    state = app.state

    graph = getattr(state, "graph", None)
    config = getattr(state, "config", None)
    llm = getattr(state, "llm", None)

    graph_loaded = graph is not None
    graph_tables = graph.count_nodes("Table") if graph_loaded else 0
    graph_columns = graph.count_nodes("Column") if graph_loaded else 0
    llm_enhanced = getattr(state, "graph_llm_enhanced", False)

    # LLM readiness
    llm_ready = False
    if config:
        provider = getattr(config, "llm_provider", "").lower()
        api_key = getattr(config, "llm_api_key", "")
        llm_ready = bool(api_key) or provider == "vertex"

    # Oracle connectivity — quick check (cached flag set during startup)
    oracle_connected = getattr(state, "oracle_connected", False)

    # Knowledge file
    knowledge_file = os.getenv("KYC_KNOWLEDGE_FILE", "kyc_business_knowledge.txt")
    try:
        knowledge_file_ready = os.path.isfile(knowledge_file) and os.path.getsize(knowledge_file) > 0
    except OSError:
        knowledge_file_ready = False

    overall = "ok" if (graph_loaded and oracle_connected) else "degraded"

    return HealthResponse(
        status=overall,
        graph_loaded=graph_loaded,
        graph_tables=graph_tables,
        graph_columns=graph_columns,
        llm_ready=llm_ready,
        llm_enhanced=llm_enhanced,
        oracle_connected=oracle_connected,
        knowledge_file_ready=knowledge_file_ready,
    )
