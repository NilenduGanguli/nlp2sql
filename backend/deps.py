"""FastAPI dependency injection for KnowledgeQL backend."""
from __future__ import annotations

from fastapi import Request


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
