"""FastAPI dependency injection for KnowledgeQL backend."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Request

from agent.signal_log import SignalLog


def get_config(request: Request):
    """Return the application AppConfig singleton."""
    return request.app.state.config


def get_graph(request: Request):
    """Return the KnowledgeGraph singleton built at startup."""
    return request.app.state.graph


def get_pipeline(request: Request):
    """Return the compiled LangGraph pipeline singleton."""
    return request.app.state.pipeline


def get_llm(request: Request):
    """Return the LLM client (may be None if no credentials)."""
    return request.app.state.llm


def get_knowledge_store(request: Request):
    """Return the KYCKnowledgeStore singleton."""
    return getattr(request.app.state, "knowledge_store", None)


_signal_log_singleton: Optional[SignalLog] = None


def get_signal_log() -> SignalLog:
    """Return the SignalLog singleton, lazily created under KNOWLEDGE_STORE_PATH/signals."""
    global _signal_log_singleton
    if _signal_log_singleton is None:
        base = os.environ.get("KNOWLEDGE_STORE_PATH", "/data/knowledge_store")
        _signal_log_singleton = SignalLog(persist_dir=os.path.join(base, "signals"))
    return _signal_log_singleton
