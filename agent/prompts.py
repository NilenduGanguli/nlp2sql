"""
Prompt File Loader + Versioned Persistence
==========================================
All LLM prompts are stored as text files in the project-level prompts/ directory.

Versioning & persistence design:
  - When PROMPTS_PERSIST_PATH env var is set, every save is ALSO written to that
    path (typically a Docker named-volume so it survives container rebuilds).
  - A timestamped history file is kept under {persist}/history/{name}/{ts}.txt
    so any past version can be restored.
  - At startup, load_persisted_prompts() copies persisted files back into the
    container's prompts/ dir if they are newer — so user edits survive rebuilds.

Env vars:
  PROMPTS_PERSIST_PATH  : absolute path to persist dir (optional).
                          Defaults to $GRAPH_CACHE_PATH/prompts if GRAPH_CACHE_PATH
                          is set, otherwise persistence is disabled.
"""
from __future__ import annotations

import datetime
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Resolve persistence directory from env
def _resolve_persist_dir() -> Optional[Path]:
    explicit = os.environ.get("PROMPTS_PERSIST_PATH", "").strip()
    if explicit:
        return Path(explicit)
    cache_path = os.environ.get("GRAPH_CACHE_PATH", "").strip()
    if cache_path:
        return Path(cache_path) / "prompts"
    return None

_PERSIST_DIR: Optional[Path] = _resolve_persist_dir()


def load_prompt(name: str, default: str = "") -> str:
    """Load content from prompts/{name}.txt. Returns `default` if the file is missing."""
    path = _PROMPTS_DIR / f"{name}.txt"
    try:
        content = path.read_text(encoding="utf-8").strip()
        logger.debug("Loaded prompt '%s' (%d chars)", name, len(content))
        return content
    except FileNotFoundError:
        if default:
            logger.debug("Prompt file '%s' not found — using inline default", name)
        return default
    except Exception as exc:
        logger.warning("Cannot read prompt '%s': %s — using inline default", name, exc)
        return default


def save_prompt(name: str, content: str) -> None:
    """
    Write content to prompts/{name}.txt.

    If PROMPTS_PERSIST_PATH is configured, also:
      1. Writes to {persist}/{name}.txt (survives container rebuild)
      2. Creates a timestamped version at {persist}/history/{name}/{ts}.txt
    """
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _PROMPTS_DIR / f"{name}.txt"
    path.write_text(content + "\n", encoding="utf-8")
    logger.info("Saved prompt '%s' (%d chars)", name, len(content))

    if _PERSIST_DIR is not None:
        try:
            _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
            # Overwrite current persisted copy
            persist_path = _PERSIST_DIR / f"{name}.txt"
            persist_path.write_text(content + "\n", encoding="utf-8")

            # Save versioned backup
            history_dir = _PERSIST_DIR / "history" / name
            history_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            version_path = history_dir / f"{ts}.txt"
            version_path.write_text(content + "\n", encoding="utf-8")
            logger.info("Persisted prompt '%s' version %s", name, ts)
        except Exception as exc:
            logger.warning("Could not persist prompt '%s': %s", name, exc)


def list_prompts() -> list[dict]:
    """Return all prompt files as [{name, content}]."""
    if not _PROMPTS_DIR.is_dir():
        return []
    result = []
    for path in sorted(_PROMPTS_DIR.glob("*.txt")):
        try:
            result.append({"name": path.stem, "content": path.read_text(encoding="utf-8").strip()})
        except Exception:
            pass
    return result


def list_prompt_versions(name: str) -> list[dict]:
    """
    Return version history for a prompt, newest first.
    Returns [{version_id, saved_at}] — up to 30 versions.
    Returns [] when persistence is disabled or no history exists.
    """
    if _PERSIST_DIR is None:
        return []
    history_dir = _PERSIST_DIR / "history" / name
    if not history_dir.is_dir():
        return []
    versions: list[dict] = []
    for p in sorted(history_dir.glob("*.txt"), reverse=True)[:30]:
        ts_str = p.stem
        try:
            dt = datetime.datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ")
            saved_at = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except ValueError:
            saved_at = ts_str
        # Peek at first 80 chars so UI can show a preview
        try:
            preview = p.read_text(encoding="utf-8")[:80].replace("\n", " ").strip()
        except Exception:
            preview = ""
        versions.append({"version_id": p.stem, "saved_at": saved_at, "preview": preview})
    return versions


def get_prompt_version(name: str, version_id: str) -> Optional[str]:
    """Load a specific historical version of a prompt. Returns None if not found."""
    if _PERSIST_DIR is None:
        return None
    # Sanitise version_id — must be alphanumeric+T+Z (timestamp format)
    import re
    if not re.match(r"^[A-Za-z0-9]+$", version_id):
        return None
    path = _PERSIST_DIR / "history" / name / f"{version_id}.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def load_persisted_prompts() -> int:
    """
    At container startup: copy persisted prompt files back into the container's
    prompts/ dir if the persisted copy is newer than the bundled file.

    This ensures user edits made via the Prompt Studio UI survive container rebuilds.
    Returns the number of prompts restored.
    """
    if _PERSIST_DIR is None or not _PERSIST_DIR.is_dir():
        return 0
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for persist_path in sorted(_PERSIST_DIR.glob("*.txt")):
        bundled_path = _PROMPTS_DIR / persist_path.name
        try:
            persist_mtime = persist_path.stat().st_mtime
            bundled_mtime = bundled_path.stat().st_mtime if bundled_path.exists() else 0.0
            if persist_mtime > bundled_mtime:
                shutil.copy2(str(persist_path), str(bundled_path))
                count += 1
                logger.info("Restored persisted prompt: %s", persist_path.name)
        except Exception as exc:
            logger.warning("Could not restore persisted prompt %s: %s", persist_path.name, exc)
    if count:
        logger.info("Restored %d persisted prompt(s) from %s", count, _PERSIST_DIR)
    return count
