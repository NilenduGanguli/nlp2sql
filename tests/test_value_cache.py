"""Tests for the disk-persistent JSON value cache."""
from __future__ import annotations

import json
import os
import time

import pytest

from knowledge_graph.value_cache import (
    ValueCache,
    ValueCacheEntry,
    get_value_cache_path,
    load_value_cache,
    save_value_cache,
)


def test_value_cache_entry_defaults():
    e = ValueCacheEntry(values=["A", "B"])
    assert e.values == ["A", "B"]
    assert e.too_many is False
    assert e.error is None
    assert e.probed_at > 0


def test_value_cache_set_and_get():
    cache = ValueCache()
    cache.set("KYC", "ACCOUNTS", "STATUS", ValueCacheEntry(values=["ACTIVE", "DORMANT"]))
    entry = cache.get("KYC", "ACCOUNTS", "STATUS")
    assert entry is not None
    assert entry.values == ["ACTIVE", "DORMANT"]


def test_value_cache_get_missing_returns_none():
    cache = ValueCache()
    assert cache.get("KYC", "NOPE", "X") is None


def test_value_cache_keys_are_uppercased():
    cache = ValueCache()
    cache.set("kyc", "accounts", "status", ValueCacheEntry(values=["A"]))
    assert cache.get("KYC", "ACCOUNTS", "STATUS") is not None
    assert cache.get("kYc", "AccountS", "stATus") is not None


def test_value_cache_round_trip(tmp_path):
    cache = ValueCache()
    cache.set("KYC", "ACCOUNTS", "STATUS", ValueCacheEntry(values=["ACTIVE", "CLOSED"]))
    cache.set("KYC", "ACCOUNTS", "BIG_COL", ValueCacheEntry(values=[], too_many=True))
    cache.set("KYC", "ACCOUNTS", "ERR_COL", ValueCacheEntry(values=[], error="ORA-12541"))

    path = tmp_path / "values_test.json"
    assert save_value_cache(cache, str(path)) is True
    assert os.path.exists(path)

    # Verify on-disk format is human-readable JSON
    with open(path) as fh:
        raw = json.load(fh)
    assert raw["version"] == "1"
    assert "entries" in raw

    loaded = load_value_cache(str(path))
    assert loaded is not None
    assert loaded.get("KYC", "ACCOUNTS", "STATUS").values == ["ACTIVE", "CLOSED"]
    assert loaded.get("KYC", "ACCOUNTS", "BIG_COL").too_many is True
    assert loaded.get("KYC", "ACCOUNTS", "ERR_COL").error == "ORA-12541"


def test_load_value_cache_missing_returns_none(tmp_path):
    assert load_value_cache(str(tmp_path / "does_not_exist.json")) is None


def test_load_value_cache_corrupt_returns_none(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("not valid json{{{")
    assert load_value_cache(str(p)) is None


def test_get_value_cache_path_uses_graph_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAPH_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("ORACLE_DSN", "host:1521/X")
    monkeypatch.setenv("ORACLE_USER", "u")
    monkeypatch.setenv("ORACLE_TARGET_SCHEMAS", "KYC")
    p = get_value_cache_path()
    assert p.startswith(str(tmp_path))
    assert p.endswith(".json")
    assert "values_" in os.path.basename(p)
