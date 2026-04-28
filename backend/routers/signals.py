"""Signals router — accepts implicit user-signal events and persists to JSONL."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.signal_log import SignalEvent, SignalLog
from backend.deps import get_signal_log

router = APIRouter(tags=["signals"])


class SignalRequest(BaseModel):
    event: Literal[
        "copied_sql",
        "opened_in_editor",
        "ran_unchanged",
        "edited_then_ran",
        "abandoned_session",
        "zero_rows_retry",
    ]
    session_id: str
    entry_id: Optional[str] = None
    mode: Literal["curator", "consumer"] = "curator"
    sql_hash: str = ""
    metadata: Dict[str, Any] = {}


@router.post("/signals")
def post_signal(req: SignalRequest, log: SignalLog = Depends(get_signal_log)) -> Dict[str, str]:
    log.append(SignalEvent(
        event=req.event,
        session_id=req.session_id,
        entry_id=req.entry_id,
        mode=req.mode,
        sql_hash=req.sql_hash,
        metadata=req.metadata,
    ))
    return {"status": "logged"}
