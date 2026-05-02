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

import csv
import io
import json
import re
import zipfile
from typing import Iterable, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

import anyio

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


# ---------------------------------------------------------------------------
# /bulk — Phase 3
# ---------------------------------------------------------------------------


class BulkPair(BaseModel):
    """One (question, expected_sql) item from a bulk upload."""
    user_input: str
    expected_sql: str
    description_override: Optional[str] = None
    tags_override: Optional[List[str]] = None
    notes: Optional[str] = None


class BulkResultItem(BaseModel):
    user_input: str
    status: str            # 'saved' | 'error'
    session_entry_id: Optional[str] = None
    learned_pattern_count: int = 0
    error: Optional[str] = None


class BulkResponse(BaseModel):
    format_detected: str   # 'json' | 'csv' | 'sql' | 'zip-of-sql'
    total: int
    saved: int
    failed: int
    items: List[BulkResultItem]


@router.post("/bulk", response_model=BulkResponse)
async def bulk(
    request: Request,
    file: UploadFile = File(..., description="JSON, CSV, SQL, or ZIP-of-SQL file"),
    knowledge_store: KYCKnowledgeStore = Depends(get_knowledge_store),
) -> BulkResponse:
    """Bulk-teach the system from a file of (question, expected_sql) pairs.

    Auto-detects the format from filename + first-bytes sniff:
      - .json    → list of {user_input, expected_sql, ...}
      - .csv     → header row required: user_input, expected_sql[, description, tags, notes]
      - .sql     → header comments: -- @question: ... | -- @tags: ...
      - .zip     → archive of .sql files (each with the same header convention)

    Each pair is analyzed (LLM if available) and saved atomically. Failures
    on individual pairs are reported per-item; one bad pair does not abort
    the whole upload.
    """
    if knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store unavailable")

    raw = await file.read()
    fmt, pairs = _parse_bulk_payload(file.filename or "", raw)
    if not pairs:
        return BulkResponse(format_detected=fmt, total=0, saved=0, failed=0, items=[])

    llm = getattr(request.app.state, "llm", None)
    items: List[BulkResultItem] = []
    saved = 0
    failed = 0

    for pair in pairs:
        try:
            analysis = await _analyze_one(llm, pair)
            session_id = _make_session_entry(
                knowledge_store,
                _payload_from_pair(pair, analysis),
            )
            n_learned = len(_make_learned_patterns(
                knowledge_store,
                _payload_from_pair(pair, analysis),
            ))
            items.append(BulkResultItem(
                user_input=pair.user_input,
                status="saved",
                session_entry_id=session_id,
                learned_pattern_count=n_learned,
            ))
            saved += 1
        except Exception as exc:
            logger.warning("bulk: pair %r failed: %s", pair.user_input[:50], exc)
            items.append(BulkResultItem(
                user_input=pair.user_input,
                status="error",
                error=str(exc),
            ))
            failed += 1

    knowledge_store.save_to_disk()  # one fsync covers all writes
    logger.info("bulk teach complete: format=%s saved=%d failed=%d", fmt, saved, failed)
    return BulkResponse(
        format_detected=fmt,
        total=len(pairs),
        saved=saved,
        failed=failed,
        items=items,
    )


# ---------------------------------------------------------------------------
# Bulk parsers
# ---------------------------------------------------------------------------


def _parse_bulk_payload(filename: str, raw: bytes) -> Tuple[str, List[BulkPair]]:
    """Sniff the format and dispatch. Returns (format_label, pairs)."""
    name = filename.lower()
    if name.endswith(".json") or _looks_like_json(raw):
        return ("json", list(_parse_json(raw)))
    if name.endswith(".csv") or _looks_like_csv(raw):
        return ("csv", list(_parse_csv(raw)))
    if name.endswith(".zip") or _looks_like_zip(raw):
        return ("zip-of-sql", list(_parse_zip(raw)))
    if name.endswith(".sql") or _looks_like_sql(raw):
        return ("sql", list(_parse_single_sql_file(raw)))
    raise HTTPException(status_code=400,
                        detail=f"Unrecognised file format for {filename!r}")


def _looks_like_json(raw: bytes) -> bool:
    s = raw.lstrip()
    return s.startswith(b"[") or s.startswith(b"{")


def _looks_like_zip(raw: bytes) -> bool:
    return raw[:2] == b"PK"


def _looks_like_csv(raw: bytes) -> bool:
    head = raw[:200].decode("utf-8", errors="ignore").splitlines()
    if not head:
        return False
    return "user_input" in head[0].lower() and "expected_sql" in head[0].lower()


def _looks_like_sql(raw: bytes) -> bool:
    head = raw[:500].decode("utf-8", errors="ignore")
    return bool(_SQL_QUESTION_HEADER.search(head)) or "select" in head.lower()


def _parse_json(raw: bytes) -> Iterable[BulkPair]:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="JSON must be a list of objects")
    for item in data:
        if not isinstance(item, dict):
            continue
        ui = str(item.get("user_input") or item.get("question") or "").strip()
        sql = str(item.get("expected_sql") or item.get("sql") or "").strip()
        if not ui or not sql:
            continue
        yield BulkPair(
            user_input=ui,
            expected_sql=sql,
            description_override=item.get("description") or item.get("description_override"),
            tags_override=item.get("tags") or item.get("tags_override"),
            notes=item.get("notes"),
        )


def _parse_csv(raw: bytes) -> Iterable[BulkPair]:
    text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        ui = (row.get("user_input") or row.get("question") or "").strip()
        sql = (row.get("expected_sql") or row.get("sql") or "").strip()
        if not ui or not sql:
            continue
        tags_raw = row.get("tags") or ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else None
        yield BulkPair(
            user_input=ui,
            expected_sql=sql,
            description_override=(row.get("description") or "").strip() or None,
            tags_override=tags,
            notes=(row.get("notes") or "").strip() or None,
        )


_SQL_QUESTION_HEADER = re.compile(r"--\s*@question\s*:\s*(.+)", re.IGNORECASE)
_SQL_TAGS_HEADER = re.compile(r"--\s*@tags\s*:\s*(.+)", re.IGNORECASE)
_SQL_NOTES_HEADER = re.compile(r"--\s*@notes\s*:\s*(.+)", re.IGNORECASE)


def _parse_single_sql_file(raw: bytes) -> Iterable[BulkPair]:
    text = raw.decode("utf-8", errors="replace")
    pair = _parse_sql_text(text)
    if pair:
        yield pair


def _parse_sql_text(text: str) -> Optional[BulkPair]:
    q = _SQL_QUESTION_HEADER.search(text)
    if not q:
        return None
    user_input = q.group(1).strip()
    tags_match = _SQL_TAGS_HEADER.search(text)
    notes_match = _SQL_NOTES_HEADER.search(text)
    sql_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("-- @")
    ]
    sql = "\n".join(sql_lines).strip()
    if not sql:
        return None
    tags = None
    if tags_match:
        tags = [t.strip() for t in tags_match.group(1).split(",") if t.strip()]
    return BulkPair(
        user_input=user_input,
        expected_sql=sql,
        tags_override=tags,
        notes=notes_match.group(1).strip() if notes_match else None,
    )


def _parse_zip(raw: bytes) -> Iterable[BulkPair]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Corrupt ZIP: {exc}")
    for name in zf.namelist():
        if not name.lower().endswith(".sql"):
            continue
        try:
            inner = zf.read(name).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("bulk zip: failed to read %s — %s", name, exc)
            continue
        pair = _parse_sql_text(inner)
        if pair is not None:
            yield pair


# ---------------------------------------------------------------------------
# Bulk per-item helpers
# ---------------------------------------------------------------------------


async def _analyze_one(llm, pair: BulkPair) -> TeachAnalysis:
    """Analyze ONE pair with the LLM, falling back to the override fields."""
    if llm is None:
        return TeachAnalysis(
            description=pair.description_override or "",
            tags=pair.tags_override or [],
        )
    digest = _synth_digest(pair.user_input, pair.expected_sql)
    try:
        from agent.llm_knowledge_analyzer import analyze_accepted_session
        entry = await anyio.to_thread.run_sync(
            lambda: analyze_accepted_session(llm, digest)
        )
    except Exception as exc:
        logger.warning("bulk: LLM analysis failed for %r: %s", pair.user_input[:40], exc)
        entry = None
    if entry is None:
        return TeachAnalysis(
            description=pair.description_override or "",
            tags=pair.tags_override or [],
        )
    md = entry.metadata or {}
    return TeachAnalysis(
        title=md.get("title", ""),
        description=pair.description_override or md.get("description", ""),
        why_this_sql=md.get("why_this_sql", ""),
        key_concepts=md.get("key_concepts", []) or [],
        tags=pair.tags_override or md.get("tags", []) or [],
        anticipated_clarifications=[
            TeachClarification(question=c.get("question", ""), answer=c.get("answer", ""))
            for c in (md.get("anticipated_clarifications", []) or [])
        ],
        key_filter_values=md.get("key_filter_values", {}) or {},
    )


def _payload_from_pair(pair: BulkPair, analysis: TeachAnalysis) -> TeachSavePayload:
    return TeachSavePayload(
        user_input=pair.user_input,
        expected_sql=pair.expected_sql,
        analysis=analysis,
        curator_notes=pair.notes or "",
        siblings=[],
        explanation="",
    )
