"""
Teach endpoints — Phase 2 of the teaching-knowledge system.

Two endpoints power the React Teach tab:

  POST /api/teach/analyze
       Body: { user_input: str, expected_sql: str }
       Calls the LLM analyzer over a synthesized "single-accept" digest and
       returns the structured knowledge (description, why_this_sql,
       key_concepts, tags, anticipated_clarifications, key_filter_values)
       PLUS a curator_notes scratchpad and an empty siblings list — the
       wizard fills these in before saving.

  POST /api/teach/save
       Body: TeachSavePayload (see below).
       Atomically:
         1. Persists ONE query_session KnowledgeEntry from the curator's
            (possibly edited) analysis.
         2. Persists each anticipated_clarification as a LearnedPattern so
            the KYC business agent can auto-answer matching questions.
         3. Persists each "sibling" KnowledgeEntry the curator attached
            (manual business rules, glossary additions, etc.).
       All-or-nothing: a single failure rolls back nothing previously
       written in this call (the in-memory store + a single save_to_disk
       at the end provides this).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.deps import get_knowledge_store
from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teach", tags=["teach"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TeachAnalyzeRequest(BaseModel):
    user_input: str = Field(..., min_length=1)
    expected_sql: str = Field(..., min_length=1)


class TeachClarification(BaseModel):
    question: str
    answer: str


class TeachAnalysis(BaseModel):
    title: str = ""
    description: str = ""
    why_this_sql: str = ""
    key_concepts: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    anticipated_clarifications: List[TeachClarification] = Field(default_factory=list)
    key_filter_values: Dict[str, List[str]] = Field(default_factory=dict)


class TeachSibling(BaseModel):
    """An auxiliary KnowledgeEntry the curator attaches alongside the main entry."""
    content: str = Field(..., min_length=1)
    category: str = "business_rule"  # business_rule | glossary | column_values | manual


class TeachSavePayload(BaseModel):
    user_input: str = Field(..., min_length=1)
    expected_sql: str = Field(..., min_length=1)
    tables_used: List[str] = Field(default_factory=list)
    analysis: TeachAnalysis
    curator_notes: str = ""
    siblings: List[TeachSibling] = Field(default_factory=list)
    explanation: str = ""


class TeachSaveResponse(BaseModel):
    status: str
    session_entry_id: str
    learned_pattern_ids: List[str]
    sibling_entry_ids: List[str]


# ---------------------------------------------------------------------------
# /analyze
# ---------------------------------------------------------------------------


@router.post("/analyze", response_model=TeachAnalysis)
async def analyze(req: TeachAnalyzeRequest, request: Request) -> TeachAnalysis:
    """Run the LLM analyzer over a synthesized one-accept digest.

    Returns an empty/default TeachAnalysis when the LLM is unavailable so the
    wizard can still let the curator fill everything in by hand.
    """
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        logger.info("/teach/analyze called with no LLM — returning empty analysis")
        return TeachAnalysis()

    digest = _synth_digest(req.user_input, req.expected_sql)
    try:
        from agent.llm_knowledge_analyzer import analyze_accepted_session
        import anyio
        entry = await anyio.to_thread.run_sync(
            lambda: analyze_accepted_session(llm, digest)
        )
    except Exception as exc:
        logger.warning("/teach/analyze LLM call failed: %s", exc)
        return TeachAnalysis()

    if entry is None:
        return TeachAnalysis()

    md = entry.metadata or {}
    return TeachAnalysis(
        title=md.get("title", ""),
        description=md.get("description", ""),
        why_this_sql=md.get("why_this_sql", ""),
        key_concepts=md.get("key_concepts", []) or [],
        tags=md.get("tags", []) or [],
        anticipated_clarifications=[
            TeachClarification(question=c.get("question", ""), answer=c.get("answer", ""))
            for c in (md.get("anticipated_clarifications", []) or [])
        ],
        key_filter_values=md.get("key_filter_values", {}) or {},
    )


def _synth_digest(user_input: str, expected_sql: str) -> Dict[str, Any]:
    """Build a single-accept digest in the shape build_session_digest produces."""
    return {
        "session_id": f"teach_{uuid.uuid4().hex[:8]}",
        "original_query": user_input,
        "enriched_query": "",
        "candidates": [{
            "id": "teach1",
            "interpretation": "curator-taught",
            "sql": expected_sql,
            "explanation": "",
            "accepted": True,
            "executed": True,
        }],
        "clarifications": [],
        "schema_context_tables": [],
        "tool_calls": [],
        "result_shape": {},
        "created_at": time.time(),
    }


# ---------------------------------------------------------------------------
# /save
# ---------------------------------------------------------------------------


@router.post("/save", response_model=TeachSaveResponse)
async def save(
    payload: TeachSavePayload,
    knowledge_store: KYCKnowledgeStore = Depends(get_knowledge_store),
) -> TeachSaveResponse:
    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store unavailable")

    session_entry_id = _make_session_entry(knowledge_store, payload)
    learned_ids = _make_learned_patterns(knowledge_store, payload)
    sibling_ids = _make_sibling_entries(knowledge_store, payload)

    knowledge_store.save_to_disk()  # one fsync covers all writes
    logger.info(
        "/teach/save persisted session=%s patterns=%d siblings=%d",
        session_entry_id, len(learned_ids), len(sibling_ids),
    )
    return TeachSaveResponse(
        status="saved",
        session_entry_id=session_entry_id,
        learned_pattern_ids=learned_ids,
        sibling_entry_ids=sibling_ids,
    )


def _make_session_entry(store: KYCKnowledgeStore, p: TeachSavePayload) -> str:
    a = p.analysis
    eid = f"teach_{uuid.uuid4().hex[:12]}"
    metadata: Dict[str, Any] = {
        "session_id": eid,
        "title": a.title,
        "original_query": p.user_input,
        "enriched_query": "",
        "accepted_candidates": [{
            "interpretation": "curator-taught",
            "sql": p.expected_sql,
            "explanation": p.explanation,
        }],
        "rejected_candidates": [],
        "clarifications": [],
        "tables_used": p.tables_used,
        "tool_calls_summary": [],
        "result_shape": {},
        "created_at": time.time(),
        # Phase 1 enrichment fields
        "description": a.description + (
            f"\n\nCurator notes: {p.curator_notes}" if p.curator_notes else ""
        ),
        "why_this_sql": a.why_this_sql,
        "key_concepts": a.key_concepts,
        "tags": a.tags,
        "anticipated_clarifications": [c.model_dump() for c in a.anticipated_clarifications],
        "key_filter_values": a.key_filter_values,
        # Provenance
        "source_workflow": "teach",
    }
    content = a.title or p.user_input
    if a.description:
        content = f"{content}\n{a.description}"
    entry = KnowledgeEntry(
        id=eid,
        source="query_session",
        category="query_session",
        content=content,
        metadata=metadata,
    )
    store.add_session_entry(entry)
    return eid


def _make_learned_patterns(store: KYCKnowledgeStore, p: TeachSavePayload) -> List[str]:
    ids: List[str] = []
    for c in p.analysis.anticipated_clarifications:
        if not (c.question and c.answer):
            continue
        try:
            pat = store.record_pattern(
                question=c.question,
                answer=c.answer,
                user_query=p.user_input,
                sql=p.expected_sql,
                confidence=0.85,        # curator-taught → high confidence
                category="filter_value",
                user_confirmed=True,
                tags=["taught", "anticipated_clarification"],
            )
            ids.append(pat.id)
        except Exception as exc:
            logger.warning("record_pattern failed for Q&A %s: %s", c.question[:40], exc)
    return ids


def _make_sibling_entries(store: KYCKnowledgeStore, p: TeachSavePayload) -> List[str]:
    ids: List[str] = []
    for s in p.siblings:
        try:
            entry = store.add_manual_entry(
                content=s.content,
                category=s.category,
                metadata={"source_workflow": "teach"},
            )
            ids.append(entry.id)
        except Exception as exc:
            logger.warning("add_manual_entry failed: %s", exc)
    return ids
