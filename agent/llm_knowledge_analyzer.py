"""
LLM Knowledge Analyzer
=======================
Uses the LLM to transform raw business documents into rich, descriptive
knowledge entries for the KYC Business Agent.

Three public functions:

- ``analyze_business_docs(llm, docs_dir)`` — reads all .txt files from the
  knowledge directory, feeds them to the LLM in a single prompt, and returns
  rich KnowledgeEntry objects.

- ``analyze_accepted_query(llm, user_input, sql, explanation, pairs)`` —
  analyzes a user-accepted query interaction and produces reusable knowledge.

- ``get_cached_or_analyze(llm, docs_dir, cache_dir)`` — cache wrapper around
  ``analyze_business_docs``; skips LLM call when source files haven't changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.knowledge_store import KnowledgeEntry

logger = logging.getLogger(__name__)

_DOCKER_CACHE_DIR = "/data/graph_cache"
_LOCAL_CACHE_DIR = os.path.expanduser("~/.cache/knowledgeql")
_CACHE_FILENAME = "llm_knowledge_cache.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    """Load the knowledge analyzer system prompt."""
    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "prompts",
        "knowledge_analyzer_system.txt",
    )
    try:
        with open(prompt_path, "r") as f:
            return f.read().strip()
    except Exception:
        return (
            "You are a KYC domain expert. Analyze the provided business documents "
            "and produce a JSON array of knowledge entries with title, content, "
            "and category fields."
        )


def _read_all_docs(docs_dir: str) -> List[Tuple[str, str]]:
    """Read all .txt files from docs_dir. Returns [(filename, content)]."""
    if not os.path.isdir(docs_dir):
        return []
    results = []
    for fname in sorted(os.listdir(docs_dir)):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(docs_dir, fname)
        try:
            with open(fpath, "r") as f:
                content = f.read().strip()
            if content:
                results.append((fname, content))
        except Exception as exc:
            logger.warning("Failed to read %s: %s", fpath, exc)
    return results


def _hash_docs(docs: List[Tuple[str, str]]) -> str:
    """SHA256 hash of all doc filenames + content."""
    h = hashlib.sha256()
    for fname, content in docs:
        h.update(fname.encode())
        h.update(content.encode())
    return h.hexdigest()


def _parse_llm_json(raw: str) -> Any:
    """Parse JSON from LLM response, tolerating common formatting issues."""
    try:
        from knowledge_graph.llm_enhancer import _parse_json_robust
        return _parse_json_robust(raw)
    except ImportError:
        pass
    # Inline fallback
    raw = re.sub(r"<thinking>[\s\S]*?</thinking>", "", raw, flags=re.IGNORECASE)
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    candidate = fence_match.group(1).strip() if fence_match else raw
    # Find outermost array or object
    arr_match = re.search(r"\[[\s\S]*\]", candidate)
    if arr_match:
        cleaned = re.sub(r",\s*([\]}])", r"\1", arr_match.group())
        return json.loads(cleaned)
    obj_match = re.search(r"\{[\s\S]*\}", candidate)
    if obj_match:
        cleaned = re.sub(r",\s*([\]}])", r"\1", obj_match.group())
        return json.loads(cleaned)
    raise ValueError("No JSON found in LLM response")


def _get_cache_dir() -> str:
    """Resolve cache directory (same logic as knowledge_store.py)."""
    env_path = os.getenv("GRAPH_CACHE_PATH")
    if env_path:
        return env_path
    if os.path.isdir(_DOCKER_CACHE_DIR):
        return _DOCKER_CACHE_DIR
    return _LOCAL_CACHE_DIR


# ---------------------------------------------------------------------------
# 1. Business Document Analysis
# ---------------------------------------------------------------------------

def analyze_business_docs(llm, docs_dir: str) -> List[KnowledgeEntry]:
    """Feed all business docs to the LLM and produce rich knowledge entries.

    All .txt files are sent in a single prompt so the LLM can cross-reference
    between table definitions, value sets, and hierarchy trees.

    Parameters
    ----------
    llm : BaseChatModel
        LangChain chat model.
    docs_dir : str
        Path to the knowledge documents directory.

    Returns
    -------
    list[KnowledgeEntry]
        Rich knowledge entries with source="llm_analysis".
        Empty list on failure.
    """
    docs = _read_all_docs(docs_dir)
    if not docs:
        logger.warning("No documents found in %s for LLM analysis", docs_dir)
        return []

    system_prompt = _load_system_prompt()

    # Build user message with all documents
    doc_sections = []
    for fname, content in docs:
        doc_sections.append(f"=== FILE: {fname} ===\n{content}\n=== END: {fname} ===")
    user_message = (
        "Analyze the following business knowledge documents and produce "
        "comprehensive knowledge entries.\n\n"
        + "\n\n".join(doc_sections)
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        logger.error("LLM document analysis call failed: %s", exc)
        return []

    # Parse response
    try:
        parsed = _parse_llm_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse LLM knowledge response: %s", exc)
        logger.debug("Raw LLM response: %s", raw[:500])
        return []

    # Handle both array and {"entries": [...]} formats
    items = parsed if isinstance(parsed, list) else parsed.get("entries", [])

    entries: List[KnowledgeEntry] = []
    valid_categories = {
        "table_purpose", "value_set_guide", "join_strategy",
        "query_pattern", "business_rule",
    }

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", f"Entry {i+1}")).strip()
        content = str(item.get("content", "")).strip()
        category = str(item.get("category", "business_rule")).strip()

        if not content:
            continue
        if category not in valid_categories:
            category = "business_rule"

        # Prefix content with title for better searchability
        full_content = f"{title}\n{content}" if title else content

        entry_id = hashlib.md5(f"llm_analysis:{title}:{i}".encode()).hexdigest()[:16]
        entries.append(KnowledgeEntry(
            id=entry_id,
            source="llm_analysis",
            content=full_content,
            category=category,
            metadata={"title": title, "generated_at": time.time()},
        ))

    logger.info("LLM document analysis produced %d knowledge entries", len(entries))
    return entries


# ---------------------------------------------------------------------------
# 2. Accepted Query Analysis
# ---------------------------------------------------------------------------

def analyze_accepted_query(
    llm,
    user_input: str,
    sql: str,
    explanation: str,
    clarification_pairs: List[Tuple[str, str]],
) -> List[KnowledgeEntry]:
    """Analyze a user-accepted query and extract reusable knowledge.

    Parameters
    ----------
    llm : BaseChatModel
        LangChain chat model.
    user_input : str
        The user's original natural language question.
    sql : str
        The generated SQL that was accepted.
    explanation : str
        The SQL explanation.
    clarification_pairs : list of (question, answer) tuples
        Clarifications that were resolved during the interaction.

    Returns
    -------
    list[KnowledgeEntry]
        1-3 reusable knowledge entries with source="llm_query_analysis".
    """
    if not user_input or not sql:
        return []

    system_prompt = (
        "You are a KYC database expert. Analyze the following successful "
        "natural-language-to-SQL interaction and extract reusable knowledge "
        "that would help answer similar questions in the future.\n\n"
        "Produce a JSON array of 1-3 knowledge entries. Each entry should capture "
        "a reusable insight — a query pattern, a business rule discovered, or "
        "a clarification resolution that applies broadly.\n\n"
        "Each entry: {\"title\": \"...\", \"content\": \"...\", \"category\": "
        "\"query_pattern\" | \"business_rule\" | \"value_set_guide\"}\n\n"
        "Focus on:\n"
        "- What the user was trying to find and how the SQL achieved it\n"
        "- If clarifications were needed, what general rule resolves them\n"
        "- What tables/joins/filters are the reusable pattern\n"
        "- Write the content so a future agent can answer similar questions "
        "WITHOUT needing clarification\n\n"
        "Return ONLY the JSON array."
    )

    # Build the interaction summary
    parts = [f"User question: {user_input}"]
    if clarification_pairs:
        pairs_text = "\n".join(
            f"  Q: {q}\n  A: {a}" for q, a in clarification_pairs
        )
        parts.append(f"Clarifications resolved:\n{pairs_text}")
    parts.append(f"Generated SQL:\n{sql}")
    if explanation:
        parts.append(f"Explanation: {explanation}")

    user_message = "\n\n".join(parts)

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        logger.warning("LLM query analysis call failed: %s", exc)
        return []

    try:
        parsed = _parse_llm_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Failed to parse LLM query analysis: %s", exc)
        return []

    items = parsed if isinstance(parsed, list) else parsed.get("entries", [])

    entries: List[KnowledgeEntry] = []
    for i, item in enumerate(items[:3]):  # cap at 3
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        content = str(item.get("content", "")).strip()
        category = str(item.get("category", "query_pattern")).strip()
        if not content:
            continue

        full_content = f"{title}\n{content}" if title else content
        entries.append(KnowledgeEntry(
            id=str(uuid.uuid4())[:16],
            source="llm_query_analysis",
            content=full_content,
            category=category,
            metadata={
                "title": title,
                "user_input": user_input,
                "sql": sql,
                "generated_at": time.time(),
            },
        ))

    logger.info("LLM query analysis produced %d knowledge entries", len(entries))
    return entries


def _load_session_analyzer_prompt() -> str:
    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "prompts",
        "session_analyzer_system.txt",
    )
    try:
        with open(prompt_path, "r") as f:
            return f.read().strip()
    except Exception:
        return (
            "You are a KYC analyst. Produce a JSON object {title, content} that "
            "comprehensively documents the provided query session for future reuse."
        )


def analyze_accepted_session(llm, digest: Dict[str, Any]) -> Optional[KnowledgeEntry]:
    """Produce ONE comprehensive KnowledgeEntry from a SessionDigest.

    Returns None on missing input or LLM/parse failure (caller falls back to
    narrow per-clarification recording).
    """
    if not digest:
        return None
    accepted = [c for c in digest.get("candidates", []) if c.get("accepted")]
    if not accepted:
        return None

    system_prompt = _load_session_analyzer_prompt()
    user_message = "Session digest (JSON):\n" + json.dumps(digest, indent=2, default=str)

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        logger.warning("Session analyzer LLM call failed: %s", exc)
        return None

    try:
        parsed = _parse_llm_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Session analyzer parse failed: %s", exc)
        return None

    if not isinstance(parsed, dict):
        return None
    title = str(parsed.get("title", "")).strip()
    content = str(parsed.get("content", "")).strip()
    if not content:
        return None
    if title:
        full_content = f"{title}\n{content}"
    else:
        full_content = content

    rejected = [c for c in digest.get("candidates", []) if not c.get("accepted")]
    metadata = {
        "session_id": digest.get("session_id", ""),
        "title": title,
        "original_query": digest.get("original_query", ""),
        "enriched_query": digest.get("enriched_query", ""),
        "accepted_candidates": [
            {"interpretation": c.get("interpretation", ""), "sql": c.get("sql", ""),
             "explanation": c.get("explanation", "")}
            for c in accepted
        ],
        "rejected_candidates": [
            {"interpretation": c.get("interpretation", ""), "sql": c.get("sql", ""),
             "rejection_reason": c.get("rejection_reason", "")}
            for c in rejected
        ],
        "clarifications": digest.get("clarifications", []),
        "tables_used": digest.get("schema_context_tables", []),
        "tool_calls_summary": digest.get("tool_calls", []),
        "result_shape": digest.get("result_shape", {}),
        "created_at": digest.get("created_at", time.time()),
    }
    eid = hashlib.sha1(
        f"query_session:{metadata['original_query']}:{metadata['created_at']}".encode()
    ).hexdigest()[:16]
    entry = KnowledgeEntry(
        id=eid,
        source="query_session",
        category="query_session",
        content=full_content,
        metadata=metadata,
    )
    logger.info("Session analyzer produced entry %s for: %s", eid, metadata["original_query"][:60])
    return entry


# ---------------------------------------------------------------------------
# 3. Cached Document Analysis
# ---------------------------------------------------------------------------

def get_cached_or_analyze(
    llm,
    docs_dir: str,
    cache_dir: Optional[str] = None,
) -> List[KnowledgeEntry]:
    """Return LLM-analyzed entries, using a disk cache when source files
    haven't changed.

    Parameters
    ----------
    llm : BaseChatModel
        LangChain chat model.
    docs_dir : str
        Path to the knowledge documents directory.
    cache_dir : str | None
        Where to store the cache file. Defaults to graph cache path.

    Returns
    -------
    list[KnowledgeEntry]
        Rich knowledge entries (from cache or fresh LLM analysis).
    """
    if cache_dir is None:
        cache_dir = _get_cache_dir()
    cache_path = os.path.join(cache_dir, _CACHE_FILENAME)

    docs = _read_all_docs(docs_dir)
    if not docs:
        return []

    current_hash = _hash_docs(docs)

    # Try loading cache
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            if cached.get("hash") == current_hash and cached.get("entries"):
                entries = [
                    KnowledgeEntry(**e) for e in cached["entries"]
                ]
                logger.info(
                    "Loaded %d LLM knowledge entries from cache (hash=%s…)",
                    len(entries), current_hash[:8],
                )
                return entries
            else:
                logger.info("LLM knowledge cache stale (hash mismatch), re-analyzing")
        except Exception as exc:
            logger.warning("Failed to read LLM knowledge cache: %s", exc)

    # Cache miss — run LLM analysis
    entries = analyze_business_docs(llm, docs_dir)
    if not entries:
        return []

    # Write cache atomically
    try:
        os.makedirs(cache_dir, exist_ok=True)
        cache_data = {
            "hash": current_hash,
            "created_at": time.time(),
            "entries": [
                {
                    "id": e.id,
                    "source": e.source,
                    "content": e.content,
                    "category": e.category,
                    "metadata": e.metadata,
                }
                for e in entries
            ],
        }
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(cache_data, f, indent=2, default=str)
        os.replace(tmp_path, cache_path)
        logger.info("Cached %d LLM knowledge entries (hash=%s…)", len(entries), current_hash[:8])
    except Exception as exc:
        logger.warning("Failed to write LLM knowledge cache: %s", exc)

    return entries
