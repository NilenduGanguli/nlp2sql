"""
Knowledge Graph Disk Cache
===========================
Serializes/deserializes KnowledgeGraph to/from disk using pickle so that an
expensive Oracle extraction + graph build is not repeated on every process
start (e.g. container restart).

Cache file format
-----------------
  pickle dict {
    "version":       str   — serialization format version (internal, not user-facing)
    "cache_version": str   — value of GRAPH_CACHE_VERSION env var at save time
    "created_at":    float — Unix timestamp of when the cache was written
    "schema_hash":   str   — identifies the Oracle source (DSN + user + schemas)
    "graph":         KnowledgeGraph
    "llm_enhanced":  bool  — True when LLM enhancement pass has already run
  }

Configuration (env vars)
------------------------
  GRAPH_CACHE_VERSION  — user-controlled version tag (default "1").
                         Bump this to force a full rebuild without deleting the
                         cache file — a different version produces a different
                         cache filename (hash changes → automatic cache miss).
  GRAPH_CACHE_PATH     — directory for cache files (default: auto-detected)
  GRAPH_CACHE_TTL_HOURS— max age in hours before the cache is considered stale
                         (0 or unset = no TTL, cache lives until config / version changes)

Default paths
-------------
  Docker container  : /data/graph_cache   (mount a named Docker volume here)
  Local dev         : ~/.cache/knowledgeql
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Bump this string to immediately invalidate all existing cache files.
_CACHE_FORMAT_VERSION = "2"

# Default locations tried in order
_DOCKER_CACHE_DIR = "/data/graph_cache"
_LOCAL_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "knowledgeql")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_cache_dir() -> str:
    """
    Return the directory where cache files are stored.

    Precedence:
      1. ``GRAPH_CACHE_PATH`` env var
      2. ``/data/graph_cache``  (if ``/data`` already exists — Docker volume)
      3. ``~/.cache/knowledgeql`` (local dev fallback)
    """
    env_path = os.getenv("GRAPH_CACHE_PATH", "").strip()
    if env_path:
        return env_path
    if os.path.isdir("/data"):
        return _DOCKER_CACHE_DIR
    return _LOCAL_CACHE_DIR


def get_cache_path(config=None) -> str:
    """
    Return the full path of the cache file for *config*.

    Different Oracle environments (different DSN/user/schema) produce different
    file names so they never collide.
    """
    cache_dir = get_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    schema_hash = _compute_schema_hash(config)
    return os.path.join(cache_dir, f"graph_{schema_hash}.pkl")


def save_graph(graph, path: str, llm_enhanced: bool = False) -> bool:
    """
    Pickle *graph* to *path*.

    Uses an atomic write (write to ``.tmp`` then ``os.replace``) so a
    partial write never leaves a corrupt cache file.

    Returns ``True`` on success, ``False`` on error.
    """
    try:
        payload: Dict[str, Any] = {
            "version":       _CACHE_FORMAT_VERSION,
            "cache_version": os.getenv("GRAPH_CACHE_VERSION", "1"),
            "created_at":    time.time(),
            "schema_hash":   _compute_schema_hash(None),  # informational
            "graph":         graph,
            "llm_enhanced":  llm_enhanced,
        }
        tmp_path = path + ".tmp"
        with open(tmp_path, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, path)
        size_mb = os.path.getsize(path) / 1_000_000
        logger.info("Graph cache saved: %s (%.1f MB, llm_enhanced=%s)",
                    path, size_mb, llm_enhanced)
        return True
    except Exception as exc:
        logger.warning("Failed to save graph cache to %s: %s", path, exc)
        # Clean up partial tmp file if it exists
        try:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        except OSError:
            pass
        return False


def load_graph(path: str, max_age_hours: float = 0.0) -> Optional[Tuple[Any, bool]]:
    """
    Load a previously saved graph from *path*.

    Returns ``(graph, llm_enhanced)`` on success, or ``None`` when:
      - the file does not exist
      - the format version does not match
      - the file is older than *max_age_hours* (when > 0)
      - the file is corrupt (any pickle error)
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
    except Exception as exc:
        logger.warning("Graph cache at %s is unreadable — rebuilding. (%s)", path, exc)
        return None

    if payload.get("version") != _CACHE_FORMAT_VERSION:
        logger.info(
            "Graph cache version mismatch (%r vs expected %r) — rebuilding.",
            payload.get("version"), _CACHE_FORMAT_VERSION,
        )
        return None

    if max_age_hours > 0:
        age_hours = (time.time() - payload.get("created_at", 0)) / 3600
        if age_hours > max_age_hours:
            logger.info(
                "Graph cache is %.1fh old (TTL=%.1fh) — rebuilding.", age_hours, max_age_hours
            )
            return None

    graph = payload.get("graph")
    llm_enhanced: bool = payload.get("llm_enhanced", False)
    age_h = (time.time() - payload.get("created_at", time.time())) / 3600
    logger.info(
        "Graph cache loaded from %s (age=%.1fh, llm_enhanced=%s)",
        path, age_h, llm_enhanced,
    )
    return graph, llm_enhanced


def invalidate_cache(path: str) -> bool:
    """
    Delete the cache file at *path*.

    Returns ``True`` if the file was removed, ``False`` if it did not exist
    or could not be deleted.
    """
    try:
        os.remove(path)
        logger.info("Graph cache invalidated: %s", path)
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.warning("Could not remove graph cache %s: %s", path, exc)
        return False


def cache_info(path: str) -> Optional[Dict[str, Any]]:
    """
    Return metadata about an existing cache file without loading the full graph.

    Returns a dict with ``created_at``, ``age_hours``, ``llm_enhanced``,
    ``size_mb``, or ``None`` if the file does not exist or cannot be read.
    """
    if not os.path.exists(path):
        return None
    try:
        # Only unpickle the outer dict, not the embedded graph object.
        # We use pickle at HIGHEST_PROTOCOL so offset scanning isn't practical;
        # instead just load it fully. The graph is large but this is a UI call.
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
        age_hours = (time.time() - payload.get("created_at", 0)) / 3600
        return {
            "created_at":    payload.get("created_at"),
            "age_hours":     round(age_hours, 1),
            "llm_enhanced":  payload.get("llm_enhanced", False),
            "version":       payload.get("version"),
            "cache_version": payload.get("cache_version", "1"),
            "size_mb":       round(os.path.getsize(path) / 1_000_000, 1),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_schema_hash(config=None) -> str:
    """
    Compute a short, stable hash that identifies the Oracle schema being cached.

    Components:
      - ORACLE_DSN + ORACLE_USER + TARGET_SCHEMAS  — different environments → different files
      - _CACHE_FORMAT_VERSION                       — internal serialization version (code bump)
      - GRAPH_CACHE_VERSION env var                 — user-controlled; bump to force a full rebuild

    A change in any component produces a different filename, making the old
    cache file invisible (automatic cache miss → rebuild).
    """
    user_version = os.getenv("GRAPH_CACHE_VERSION", "1")

    if config is None:
        # Read directly from env as fallback
        dsn     = os.getenv("ORACLE_DSN", "")
        user    = os.getenv("ORACLE_USER", "")
        schemas = os.getenv("ORACLE_TARGET_SCHEMAS", "")
    else:
        # Accept AppConfig (has .oracle) or GraphConfig (has .oracle) or OracleConfig
        oracle_cfg = (
            getattr(config, "oracle", None)
            or config  # config IS an OracleConfig
        )
        dsn     = getattr(oracle_cfg, "dsn", "")  or os.getenv("ORACLE_DSN", "")
        user    = getattr(oracle_cfg, "user", "") or os.getenv("ORACLE_USER", "")
        raw_schemas = getattr(oracle_cfg, "target_schemas", None)
        if isinstance(raw_schemas, list):
            schemas = ",".join(sorted(raw_schemas))
        else:
            schemas = os.getenv("ORACLE_TARGET_SCHEMAS", "")

    key = f"{dsn}|{user}|{schemas}|{_CACHE_FORMAT_VERSION}|{user_version}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]
