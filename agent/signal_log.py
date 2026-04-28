"""Append-only JSONL log of implicit user signals."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


SignalEventType = Literal[
    "copied_sql",
    "opened_in_editor",
    "ran_unchanged",
    "edited_then_ran",
    "abandoned_session",
    "zero_rows_retry",
]


@dataclass
class SignalEvent:
    event: SignalEventType
    session_id: str
    entry_id: Optional[str]
    mode: str
    sql_hash: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class SignalLog:
    def __init__(self, persist_dir: str) -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path_for_today(self) -> Path:
        return self.persist_dir / f"signals-{date.today().isoformat()}.jsonl"

    def append(self, event: SignalEvent) -> None:
        line = json.dumps(asdict(event), default=str)
        with self._lock:
            with open(self._path_for_today(), "a") as f:
                f.write(line + "\n")

    def load(
        self,
        event: Optional[str] = None,
        session_id: Optional[str] = None,
        entry_id: Optional[str] = None,
    ) -> List[SignalEvent]:
        results: List[SignalEvent] = []
        with self._lock:
            for path in sorted(self.persist_dir.glob("signals-*.jsonl")):
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if event and rec.get("event") != event:
                            continue
                        if session_id and rec.get("session_id") != session_id:
                            continue
                        if entry_id and rec.get("entry_id") != entry_id:
                            continue
                        results.append(SignalEvent(**rec))
        return results
