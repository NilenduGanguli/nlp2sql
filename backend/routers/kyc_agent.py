"""
KYC Business Agent Tuning API
===============================
Endpoints for managing the KYC knowledge store:
- CRUD for static knowledge entries
- CRUD for learned patterns
- Metrics dashboard
- Test agent interactively
- Import/export JSON
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.deps import get_config, get_knowledge_store, get_llm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kyc-agent", tags=["kyc-agent"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class KnowledgeEntryCreate(BaseModel):
    content: str
    category: str = "business_rule"
    metadata: Dict[str, Any] = {}

class KnowledgeEntryUpdate(BaseModel):
    content: str
    category: str
    metadata: Dict[str, Any] = {}

class PatternUpdate(BaseModel):
    answer: Optional[str] = None
    confidence: Optional[float] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None

class AgentTestRequest(BaseModel):
    question: str
    user_query: str = ""

class ImportRequest(BaseModel):
    data: Dict[str, Any]
    mode: str = "merge"  # "merge" or "replace"


# ---------------------------------------------------------------------------
# Static knowledge entries
# ---------------------------------------------------------------------------

@router.get("/knowledge")
async def list_knowledge(
    category: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = None,
    knowledge_store=Depends(get_knowledge_store),
):
    """List static knowledge entries with optional filters."""
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    entries = knowledge_store.search_entries(query=search or "", category=category, source=source)
    return {"entries": [e.to_dict() for e in entries], "total": len(entries)}


@router.post("/knowledge")
async def create_knowledge(
    req: KnowledgeEntryCreate,
    knowledge_store=Depends(get_knowledge_store),
):
    """Create a manual knowledge entry."""
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    entry = knowledge_store.add_manual_entry(req.content, req.category, req.metadata)
    return entry.to_dict()


@router.put("/knowledge/{entry_id}")
async def update_knowledge(
    entry_id: str,
    req: KnowledgeEntryUpdate,
    knowledge_store=Depends(get_knowledge_store),
):
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    ok = knowledge_store.update_entry(entry_id, req.content, req.category, req.metadata)
    if not ok:
        raise HTTPException(404, "Entry not found")
    return {"status": "updated"}


@router.delete("/knowledge/{entry_id}")
async def delete_knowledge(
    entry_id: str,
    knowledge_store=Depends(get_knowledge_store),
):
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    ok = knowledge_store.delete_entry(entry_id)
    if not ok:
        raise HTTPException(404, "Entry not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Learned patterns
# ---------------------------------------------------------------------------

@router.get("/patterns")
async def list_patterns(
    category: Optional[str] = None,
    min_confidence: Optional[float] = None,
    sort: str = "last_used",
    knowledge_store=Depends(get_knowledge_store),
):
    """List learned patterns with optional filters."""
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    patterns = knowledge_store.learned_patterns
    if category:
        patterns = [p for p in patterns if p.category == category]
    if min_confidence is not None:
        patterns = [p for p in patterns if p.confidence >= min_confidence]
    if sort == "confidence":
        patterns = sorted(patterns, key=lambda p: p.confidence, reverse=True)
    elif sort == "use_count":
        patterns = sorted(patterns, key=lambda p: p.use_count, reverse=True)
    else:
        patterns = sorted(patterns, key=lambda p: p.last_used_at, reverse=True)
    return {"patterns": [p.to_dict() for p in patterns], "total": len(patterns)}


@router.put("/patterns/{pattern_id}")
async def update_pattern(
    pattern_id: str,
    req: PatternUpdate,
    knowledge_store=Depends(get_knowledge_store),
):
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    ok = knowledge_store.update_pattern(pattern_id, **updates)
    if not ok:
        raise HTTPException(404, "Pattern not found")
    return {"status": "updated"}


@router.delete("/patterns/{pattern_id}")
async def delete_pattern(
    pattern_id: str,
    knowledge_store=Depends(get_knowledge_store),
):
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    ok = knowledge_store.delete_pattern(pattern_id)
    if not ok:
        raise HTTPException(404, "Pattern not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def get_metrics(knowledge_store=Depends(get_knowledge_store)):
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    return knowledge_store.get_metrics()


# ---------------------------------------------------------------------------
# Test agent
# ---------------------------------------------------------------------------

@router.post("/test")
async def test_agent(
    req: AgentTestRequest,
    config=Depends(get_config),
    knowledge_store=Depends(get_knowledge_store),
    llm=Depends(get_llm),
):
    """Test the KYC business agent with a question — returns the agent's response + reasoning."""
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")

    from agent.nodes.kyc_business_agent import make_kyc_business_agent
    agent_fn = make_kyc_business_agent(llm=llm, knowledge_store=knowledge_store)

    # Build a minimal state with a clarification question
    state = {
        "user_input": req.user_query or req.question,
        "conversation_history": [],
        "need_clarification": True,
        "clarification_question": req.question,
        "clarification_options": [],
        "clarification_context": "",
        "kyc_auto_answered": False,
        "kyc_auto_answer": "",
        "intent": "DATA_QUERY",
        "_trace": [],
    }

    result = agent_fn(state)
    trace_steps = result.get("_trace", [])
    last_trace = trace_steps[-1] if trace_steps else {}

    return {
        "auto_answered": result.get("kyc_auto_answered", False),
        "answer": result.get("kyc_auto_answer", ""),
        "trace": last_trace,
    }


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------

@router.get("/export")
async def export_store(knowledge_store=Depends(get_knowledge_store)):
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    return knowledge_store.export_json()


@router.post("/import")
async def import_store(
    req: ImportRequest,
    knowledge_store=Depends(get_knowledge_store),
):
    if not knowledge_store:
        raise HTTPException(503, "Knowledge store not initialized")
    counts = knowledge_store.import_json(req.data, mode=req.mode)
    return {"status": "imported", **counts}
