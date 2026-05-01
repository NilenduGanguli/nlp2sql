"""Tests for /api/admin/value-cache-info and /api/admin/rebuild-value-cache (Phase 3)."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app_config import AppConfig
from backend.main import app


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAPH_CACHE_PATH", str(tmp_path))
    app.state.config = AppConfig()
    # Ensure clean app.state for each test
    if hasattr(app.state, "graph_bundle"):
        delattr(app.state, "graph_bundle")
    return TestClient(app)


def test_value_cache_info_when_no_cache_present(tmp_path, monkeypatch):
    """No file on disk + no loaded singleton → exists=False, zero stats."""
    # Reset the loaded singleton so the test is deterministic.
    from knowledge_graph import column_value_cache as cvc
    cvc._loaded_cache = None

    client = _make_client(monkeypatch, tmp_path)
    r = client.get("/api/admin/value-cache-info")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False
    assert body["stats"] == {"total": 0, "ok": 0, "too_many": 0, "errors": 0}
    assert body["path"].endswith(".json")


def test_value_cache_info_reflects_loaded_singleton(tmp_path, monkeypatch):
    """Loaded ValueCache → stats reflect ok/too_many/error counts."""
    from knowledge_graph import column_value_cache as cvc
    from knowledge_graph.value_cache import ValueCache, ValueCacheEntry

    cache = ValueCache()
    cache.set("KYC", "ACCOUNTS", "STATUS",
              ValueCacheEntry(values=["ACTIVE", "DORMANT"]))
    cache.set("KYC", "ACCOUNTS", "BIG_COL",
              ValueCacheEntry(values=[], too_many=True))
    cache.set("KYC", "ACCOUNTS", "ERR_COL",
              ValueCacheEntry(values=[], error="ORA-00942"))
    cvc._loaded_cache = cache

    client = _make_client(monkeypatch, tmp_path)
    r = client.get("/api/admin/value-cache-info")
    assert r.status_code == 200
    stats = r.json()["stats"]
    assert stats["total"] == 3
    assert stats["ok"] == 1
    assert stats["too_many"] == 1
    assert stats["errors"] == 1


def test_rebuild_value_cache_returns_started(tmp_path, monkeypatch):
    """Endpoint returns immediately even when no graph is configured (graceful)."""
    client = _make_client(monkeypatch, tmp_path)
    # No app.state.graph → background task aborts cleanly with a warning log.
    r = client.post("/api/admin/rebuild-value-cache")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "started"
    assert "Value cache rebuild" in body["message"]
