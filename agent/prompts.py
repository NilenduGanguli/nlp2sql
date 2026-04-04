"""
Prompt File Loader
==================
All LLM prompts are stored as text files in the project-level prompts/ directory.
This module loads them, returning inline defaults if the file is not found.
No caching — re-reads on every pipeline init so changes take effect on next query.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


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
    """Write content to prompts/{name}.txt, creating the directory if needed."""
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _PROMPTS_DIR / f"{name}.txt"
    path.write_text(content + "\n", encoding="utf-8")
    logger.info("Saved prompt '%s' (%d chars)", name, len(content))


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
