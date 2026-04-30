"""Tests for value_cache_builder module — heuristic marking, LLM nomination, DISTINCT probe."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from knowledge_graph.config import GraphConfig, ValueCacheConfig


def test_value_cache_config_defaults_match_design():
    cfg = ValueCacheConfig()
    assert cfg.enabled is True
    assert cfg.max_values == 30
    assert cfg.probe_workers == 8
    assert cfg.probe_timeout_ms == 5000
    assert cfg.llm_nominate is True
    assert cfg.llm_batch_size == 50


def test_value_cache_config_reads_env(monkeypatch):
    monkeypatch.setenv("VALUE_CACHE_ENABLED", "false")
    monkeypatch.setenv("VALUE_CACHE_MAX_VALUES", "50")
    monkeypatch.setenv("VALUE_CACHE_PROBE_WORKERS", "16")
    cfg = ValueCacheConfig()
    assert cfg.enabled is False
    assert cfg.max_values == 50
    assert cfg.probe_workers == 16


def test_graph_config_composes_value_cache_config():
    gcfg = GraphConfig()
    assert isinstance(gcfg.value_cache, ValueCacheConfig)
