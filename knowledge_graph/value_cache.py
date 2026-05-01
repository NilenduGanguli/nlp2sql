"""
Disk-persistent value cache (JSON)
===================================
Stores the distinct values of every "filter-likely" column so the SQL
generator can ground WHERE clauses in real DB values instead of guessing.

Layout:
  - One JSON file per (DSN, user, schemas, format-version) tuple
  - Atomic write via ``.tmp`` + ``os.replace``
  - Lives next to the graph cache file in GRAPH_CACHE_PATH

JSON is the chosen format because the cache contents originate from the
database — JSON deserialisation cannot execute code, so a tampered file
cannot pwn the process. The file is also human-readable, which makes
debugging and ops trivial. The data shape (list[str] + small primitives)
is trivially serialisable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, Tuple

from knowledge_graph.graph_cache import _compute_schema_hash, get_cache_dir

logger = logging.getLogger(__name__)

_VALUE_CACHE_FORMAT_VERSION = "1"


@dataclass
class ValueCacheEntry:
    """Cached probe result for one (schema, table, column) triple."""

    values: list = field(default_factory=list)
    too_many: bool = False              # column had > max_values distinct values
    error: Optional[str] = None         # set when the probe failed
    probed_at: float = field(default_factory=time.time)


class ValueCache:
    """In-memory map keyed by (SCHEMA, TABLE, COLUMN) — case-insensitive on input."""

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str, str], ValueCacheEntry] = {}

    def get(self, schema: str, table: str, column: str) -> Optional[ValueCacheEntry]:
        return self._entries.get(self._key(schema, table, column))

    def set(self, schema: str, table: str, column: str, entry: ValueCacheEntry) -> None:
        self._entries[self._key(schema, table, column)] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def items(self):
        return self._entries.items()

    def stats(self) -> Dict[str, int]:
        ok = sum(1 for e in self._entries.values() if e.values and not e.error)
        too_many = sum(1 for e in self._entries.values() if e.too_many)
        errors = sum(1 for e in self._entries.values() if e.error)
        return {"total": len(self), "ok": ok, "too_many": too_many, "errors": errors}

    @staticmethod
    def _key(schema: str, table: str, column: str) -> Tuple[str, str, str]:
        return (schema.upper(), table.upper(), column.upper())


def get_value_cache_path(config=None) -> str:
    """Return the disk path of the value cache for *config*."""
    cache_dir = get_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    schema_hash = _compute_schema_hash(config)
    return os.path.join(cache_dir, f"values_{schema_hash}.json")


def save_value_cache(cache: ValueCache, path: str) -> bool:
    """Serialise *cache* to *path* atomically as JSON. Returns True on success."""
    try:
        entries_serialised = {
            "|".join(key): asdict(entry)
            for key, entry in cache._entries.items()
        }
        payload = {
            "version": _VALUE_CACHE_FORMAT_VERSION,
            "created_at": time.time(),
            "entries": entries_serialised,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        size_mb = os.path.getsize(path) / 1_000_000
        logger.info(
            "Value cache saved: %s (%.2f MB, %d entries)",
            path, size_mb, len(cache),
        )
        return True
    except Exception as exc:
        logger.warning("Failed to save value cache to %s: %s", path, exc)
        try:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        except OSError:
            pass
        return False


def load_value_cache(path: str) -> Optional[ValueCache]:
    """Load a previously saved value cache from JSON. None if missing/corrupt/wrong-version."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:
        logger.warning("Value cache at %s is unreadable: %s", path, exc)
        return None
    if payload.get("version") != _VALUE_CACHE_FORMAT_VERSION:
        logger.info(
            "Value cache version mismatch (%r vs %r) — discarding.",
            payload.get("version"), _VALUE_CACHE_FORMAT_VERSION,
        )
        return None
    cache = ValueCache()
    for key_str, entry_dict in payload.get("entries", {}).items():
        parts = key_str.split("|")
        if len(parts) != 3:
            continue
        schema, table, column = parts
        cache.set(schema, table, column, ValueCacheEntry(
            values=entry_dict.get("values", []),
            too_many=bool(entry_dict.get("too_many", False)),
            error=entry_dict.get("error"),
            probed_at=float(entry_dict.get("probed_at", time.time())),
        ))
    age_h = (time.time() - payload.get("created_at", time.time())) / 3600
    logger.info(
        "Value cache loaded from %s (age=%.1fh, %d entries)",
        path, age_h, len(cache),
    )
    return cache


def invalidate_value_cache(path: str) -> bool:
    """Remove the value cache file. Returns True if it was removed."""
    try:
        os.remove(path)
        logger.info("Value cache invalidated: %s", path)
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.warning("Could not remove value cache %s: %s", path, exc)
        return False
