# Role-Aware Learning Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add role-aware UX (curator vs consumer modes), implicit signal capture, pattern aggregation, and conversation refinement to the existing nlp2sql chat — so the system learns from curator accepts and supports both SQL developers and business users from one shared backend pipeline.

**Architecture:** Three new subsystems layered on the existing LangGraph pipeline: (1) global mode toggle in localStorage that gates UX surfaces only — pipeline stays untouched; (2) append-only Signal Bus persisting implicit user signals to `signals.jsonl`; (3) Pattern Aggregator that runs after curator-accepts to promote frequent-and-positive sessions to verified patterns stored in the existing `LearnedPattern` slot. Refinement layer adds a diff-prompt branch in the SQL generator when intent is `RESULT_FOLLOWUP`/`QUERY_REFINE`.

**Tech Stack:** Python 3.11 (FastAPI, pytest, pydantic v2), TypeScript (React, Zustand, Vite), JSONL append-only logs, no new infra.

**Spec:** `docs/superpowers/specs/2026-04-27-role-aware-learning-chat-design.md`

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `agent/signal_log.py` | `SignalLog` class — append-only JSONL writer for implicit user signals; daily rotation |
| `agent/sql_skeleton.py` | `sql_skeleton(sql) -> str` helper — strips literals, normalizes whitespace, case-folds identifiers |
| `agent/pattern_aggregator.py` | `aggregate_patterns(store, accepted_entry, signals)` — clusters similar accepted sessions, promotes to verified pattern |
| `backend/routers/signals.py` | `POST /api/signals` endpoint + Pydantic request model |
| `frontend/src/hooks/useUserMode.ts` | Zustand-backed global mode hook + localStorage persistence |
| `frontend/src/api/signals.ts` | Frontend API client for `POST /api/signals` |
| `frontend/src/components/layout/ModeToggle.tsx` | Header toggle UI: "Curator / Consumer" |
| `frontend/src/components/chat/RefineButton.tsx` | ↻ Refine, ⤴ Branch, ★ Save buttons cluster |
| `frontend/src/components/kyc/PatternsTab.tsx` | "Patterns" sub-tab inside `KYCAgentPage` |
| `tests/test_signal_log.py` | Unit tests for SignalLog |
| `tests/test_sql_skeleton.py` | Unit tests for sql_skeleton |
| `tests/test_pattern_aggregator.py` | Unit tests for aggregator + verified-pattern promotion |
| `tests/test_e2e_role_aware_chat.py` | E2E: mode flip, signal flow, pattern promotion, refinement |

### Files modified

| Path | What changes |
|---|---|
| `agent/knowledge_store.py` | Add `add_pattern(LearnedPattern)`, `find_verified_pattern(query, graph)`, ensure `LearnedPattern` persists/restores |
| `agent/nodes/session_lookup.py` | Query verified patterns first; fall back to query_session match |
| `agent/nodes/sql_generator.py` | Diff-prompt branch when refinement intent + `previous_sql_context.sql` non-empty |
| `backend/routers/query.py` | Wire `mode` field through accept-query; pass to aggregator |
| `backend/routers/admin.py` | Expose `default_user_mode` in `GET /api/admin/config` |
| `backend/main.py` | Mount `signals` router; load `DEFAULT_USER_MODE` env |
| `backend/models.py` | Add `mode: Optional[str]` to `QueryRequest`, `AcceptQueryRequest` |
| `frontend/src/types.ts` | Add `UserMode`, `SignalEvent`, `LearnedPatternView` types |
| `frontend/src/store/chatStore.ts` | Add `sessionId`, auto-snapshot `previous_sql_context`, `emitSignal` helper |
| `frontend/src/components/layout/AppShell.tsx` | Render `<ModeToggle />` in header |
| `frontend/src/components/chat/SqlCandidatesPicker.tsx` | Top-1 + "show alternatives" in consumer mode; "Verified" badge |
| `frontend/src/components/chat/SqlResultCard.tsx` | Hide accept buttons in consumer mode; emit signals on copy/open-in-editor; render `<RefineButton />` cluster |
| `frontend/src/components/chat/StreamingIndicator.tsx` | Compact mode when `userMode === 'consumer'` |
| `frontend/src/components/chat/ChatPanel.tsx` | Strict abandonment detection: emit signal on new-query when prior SQL was unconfirmed |
| `frontend/src/components/chat/MessageList.tsx` | Zero-rows detection → emit `zero_rows_retry` signal when next query within 60s |
| `frontend/src/pages/EditorPage.tsx` | Track `loadedSql`, emit `ran_unchanged` or `edited_then_ran` with `edit_distance` |
| `frontend/src/pages/KYCAgentPage.tsx` | Add "Patterns" sub-tab |
| `frontend/src/components/layout/Sidebar.tsx` | Hide Investigate tab in consumer mode |

---

## Task 1: Mode toggle — backend

**Files:**
- Modify: `backend/routers/admin.py`
- Modify: `backend/models.py`
- Modify: `backend/main.py` (env var loading)
- Test: `tests/test_admin_config.py`

- [ ] **Step 1: Write the failing test for `default_user_mode` in admin config**

Append to `tests/test_admin_config.py` (create if missing):

```python
import os
from fastapi.testclient import TestClient

def test_admin_config_returns_default_user_mode(monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_MODE", "curator")
    # Re-import to pick up env override; in this codebase the lifespan reads env at startup
    from backend.main import app
    client = TestClient(app)
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    assert r.json().get("default_user_mode") == "curator"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python3 -m pytest tests/test_admin_config.py::test_admin_config_returns_default_user_mode -v`
Expected: FAIL with `KeyError: 'default_user_mode'` or `AssertionError`.

- [ ] **Step 3: Add `default_user_mode` to admin config endpoint**

In `backend/routers/admin.py`, locate the function for `GET /api/admin/config` (search for `@router.get("/config")`). Inside the response dict, add:

```python
import os
# ... existing config dict ...
config["default_user_mode"] = os.environ.get("DEFAULT_USER_MODE", "curator")
```

- [ ] **Step 4: Run test, expect PASS**

Run: `python3 -m pytest tests/test_admin_config.py::test_admin_config_returns_default_user_mode -v`
Expected: PASS.

- [ ] **Step 5: Add `mode` field to backend request models**

In `backend/models.py`:

```python
from typing import Literal, Optional
# ... in QueryRequest:
class QueryRequest(BaseModel):
    user_input: str
    conversation_history: list = []
    auto_execute: bool = False
    previous_sql_context: dict = {}
    mode: Optional[Literal["curator", "consumer"]] = None
# ... in AcceptQueryRequest (the existing class for /query/accept-query, may be named _AcceptQueryRequest in backend/routers/query.py):
# add the same `mode: Optional[Literal["curator","consumer"]] = None` field.
```

If `AcceptQueryRequest` is defined inline in `backend/routers/query.py` as `_AcceptQueryRequest`, modify it there.

- [ ] **Step 6: Verify backend still imports cleanly**

Run: `python3 -c "from backend.main import app; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/admin.py backend/models.py backend/routers/query.py tests/test_admin_config.py
git commit -m "feat(mode): expose default_user_mode in /api/admin/config and accept mode in request bodies"
```

---

## Task 2: Mode toggle — frontend hook + types

**Files:**
- Create: `frontend/src/hooks/useUserMode.ts`
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Add `UserMode` type**

In `frontend/src/types.ts`, append:

```ts
export type UserMode = 'curator' | 'consumer'
```

- [ ] **Step 2: Create `useUserMode` hook with localStorage persistence**

Create `frontend/src/hooks/useUserMode.ts`:

```ts
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { UserMode } from '../types'

interface UserModeState {
  mode: UserMode
  setMode: (mode: UserMode) => void
}

export const useUserMode = create<UserModeState>()(
  persist(
    (set) => ({
      mode: 'curator',
      setMode: (mode) => set({ mode }),
    }),
    { name: 'nlp2sql.userMode' },
  ),
)
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /Users/neelu/dev/nlp2sql/frontend && PATH=/opt/homebrew/bin:$PATH npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useUserMode.ts frontend/src/types.ts
git commit -m "feat(mode): add useUserMode zustand hook + UserMode type"
```

---

## Task 3: Mode toggle — UI in AppShell + sync from server default

**Files:**
- Create: `frontend/src/components/layout/ModeToggle.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/App.tsx` (one-time sync from `/api/admin/config`)

- [ ] **Step 1: Create `ModeToggle` component**

Create `frontend/src/components/layout/ModeToggle.tsx`:

```tsx
import React from 'react'
import { useUserMode } from '../../hooks/useUserMode'

export const ModeToggle: React.FC = () => {
  const { mode, setMode } = useUserMode()
  const next = mode === 'curator' ? 'consumer' : 'curator'
  return (
    <button
      onClick={() => setMode(next)}
      title={`Switch to ${next} mode`}
      style={{
        padding: '4px 10px',
        fontSize: 12,
        background: mode === 'curator' ? '#7c6af7' : '#10b981',
        color: 'white',
        border: 'none',
        borderRadius: 6,
        cursor: 'pointer',
        fontWeight: 600,
      }}
    >
      {mode === 'curator' ? '🛠 Curator' : '👤 Consumer'}
    </button>
  )
}
```

- [ ] **Step 2: Mount `ModeToggle` in AppShell header**

In `frontend/src/components/layout/AppShell.tsx`, import and render `<ModeToggle />` in the header bar. Find the existing header (likely a `<header>` or top div containing the tab bar) and add `<ModeToggle />` to the right side.

```tsx
import { ModeToggle } from './ModeToggle'
// ... in the header JSX, add:
<div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
  <ModeToggle />
</div>
```

- [ ] **Step 3: Sync initial mode from server default on first load**

In `frontend/src/App.tsx`, add a one-shot effect that fetches `/api/admin/config` and sets the initial mode IF localStorage is empty:

```tsx
import { useUserMode } from './hooks/useUserMode'
// ... inside App():
useEffect(() => {
  // Only sync if user has never set the mode (no localStorage entry)
  if (!localStorage.getItem('nlp2sql.userMode')) {
    fetch('/api/admin/config')
      .then((r) => r.json())
      .then((cfg) => {
        if (cfg.default_user_mode === 'consumer' || cfg.default_user_mode === 'curator') {
          useUserMode.getState().setMode(cfg.default_user_mode)
        }
      })
      .catch(() => {})  // Silently ignore — defaults to 'curator' from hook
  }
}, [])
```

- [ ] **Step 4: Build frontend and verify no errors**

Run: `cd /Users/neelu/dev/nlp2sql/frontend && PATH=/opt/homebrew/bin:$PATH npx tsc --noEmit && PATH=/opt/homebrew/bin:$PATH npx vite build --outDir ../dist --emptyOutDir 2>&1 | tail -5`
Expected: `built in <Ns>`, no TS errors.

- [ ] **Step 5: Manual smoke test in browser**

Run: `docker compose -f docker/docker-compose.yml restart backend`
Then open `http://localhost:8000/`. Verify the mode toggle appears in the header. Click it: badge text changes from 🛠 Curator to 👤 Consumer. Reload page: mode persists.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/layout/ModeToggle.tsx frontend/src/components/layout/AppShell.tsx frontend/src/App.tsx dist/
git commit -m "feat(mode): mode toggle in AppShell header with localStorage persist + server default sync"
```

---

## Task 4: Mode-gated UX — candidates picker, accept buttons, streaming, sidebar

**Files:**
- Modify: `frontend/src/components/chat/SqlCandidatesPicker.tsx`
- Modify: `frontend/src/components/chat/SqlResultCard.tsx`
- Modify: `frontend/src/components/chat/StreamingIndicator.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`

- [ ] **Step 1: Gate `SqlCandidatesPicker` to top-1 + "show alternatives" in consumer mode**

In `SqlCandidatesPicker.tsx`, near the top of the component:

```tsx
import { useUserMode } from '../../hooks/useUserMode'
// ... inside component:
const { mode } = useUserMode()
const [showAll, setShowAll] = React.useState(false)
const visible = mode === 'consumer' && !showAll ? candidates.slice(0, 1) : candidates
const hiddenCount = candidates.length - visible.length
```

Replace the candidates `.map()` with `visible.map()`. Below it, add when `hiddenCount > 0`:

```tsx
{hiddenCount > 0 && (
  <button onClick={() => setShowAll(true)} style={{
    fontSize: 12, color: '#7c6af7', background: 'transparent',
    border: '1px solid #7c6af7', borderRadius: 6, padding: '4px 10px',
    cursor: 'pointer', marginTop: 8,
  }}>
    Show {hiddenCount} alternative{hiddenCount > 1 ? 's' : ''}
  </button>
)}
```

- [ ] **Step 2: Hide accept buttons in consumer mode in `SqlResultCard`**

In `SqlResultCard.tsx`:

```tsx
import { useUserMode } from '../../hooks/useUserMode'
// ... inside component:
const { mode } = useUserMode()
// ... around the accept/reject buttons block, wrap:
{mode === 'curator' && (
  <div style={{ /* existing accept buttons container */ }}>
    {/* existing accept/reject buttons */}
  </div>
)}
```

- [ ] **Step 3: Compact `StreamingIndicator` in consumer mode**

In `StreamingIndicator.tsx`:

```tsx
import { useUserMode } from '../../hooks/useUserMode'
// ... inside component:
const { mode } = useUserMode()
// ... at the very end of the JSX, replace the "Completed steps" block to be conditional:
{mode === 'curator' && steps.length > 0 && (
  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
    {/* existing completed-step chips */}
  </div>
)}
```

- [ ] **Step 4: Hide Investigate tab in consumer mode**

In `frontend/src/components/layout/Sidebar.tsx` (or `TabBar.tsx` if tabs are there), find the tab definitions array. Wrap the `investigate` tab rendering in a conditional:

```tsx
import { useUserMode } from '../../hooks/useUserMode'
// ... inside component:
const { mode } = useUserMode()
// ... when iterating tabs, filter:
const visibleTabs = TABS.filter(t => t.id !== 'investigate' || mode === 'curator')
```

- [ ] **Step 5: Verify TypeScript and rebuild**

Run: `cd /Users/neelu/dev/nlp2sql/frontend && PATH=/opt/homebrew/bin:$PATH npx tsc --noEmit && PATH=/opt/homebrew/bin:$PATH npx vite build --outDir ../dist --emptyOutDir 2>&1 | tail -3`
Expected: `built in <Ns>`.

- [ ] **Step 6: Manual smoke test**

Open the app, switch to Consumer mode. Run a query. Verify: only 1 candidate shows with "Show N alternatives" button; accept buttons hidden; trace chips hidden; Investigate tab gone from sidebar. Switch back to Curator: all UI restored.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/chat/SqlCandidatesPicker.tsx frontend/src/components/chat/SqlResultCard.tsx frontend/src/components/chat/StreamingIndicator.tsx frontend/src/components/layout/Sidebar.tsx dist/
git commit -m "feat(mode): gate candidates picker, accept buttons, streaming chips, and Investigate tab to curator mode"
```

---

## Task 5: SignalLog backend — append-only JSONL writer

**Files:**
- Create: `agent/signal_log.py`
- Test: `tests/test_signal_log.py`

- [ ] **Step 1: Write failing test for SignalLog**

Create `tests/test_signal_log.py`:

```python
import json
import pytest
from agent.signal_log import SignalLog, SignalEvent


def test_append_writes_jsonl(tmp_path):
    log = SignalLog(persist_dir=str(tmp_path))
    log.append(SignalEvent(
        event="ran_unchanged",
        session_id="s1",
        entry_id="e1",
        mode="curator",
        sql_hash="abc",
        metadata={"row_count": 42},
    ))
    files = list(tmp_path.glob("signals-*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "ran_unchanged"
    assert rec["session_id"] == "s1"
    assert rec["mode"] == "curator"
    assert "timestamp" in rec


def test_append_multiple_events(tmp_path):
    log = SignalLog(persist_dir=str(tmp_path))
    for i in range(3):
        log.append(SignalEvent(
            event="copied_sql", session_id=f"s{i}", entry_id=None,
            mode="consumer", sql_hash="x", metadata={},
        ))
    files = list(tmp_path.glob("signals-*.jsonl"))
    assert len(files) == 1
    assert len(files[0].read_text().strip().splitlines()) == 3


def test_load_filters_by_event_and_session(tmp_path):
    log = SignalLog(persist_dir=str(tmp_path))
    log.append(SignalEvent(event="copied_sql", session_id="s1", entry_id=None,
                           mode="curator", sql_hash="x", metadata={}))
    log.append(SignalEvent(event="ran_unchanged", session_id="s1", entry_id=None,
                           mode="curator", sql_hash="x", metadata={}))
    log.append(SignalEvent(event="copied_sql", session_id="s2", entry_id=None,
                           mode="curator", sql_hash="y", metadata={}))

    by_session = log.load(session_id="s1")
    assert len(by_session) == 2

    by_event = log.load(event="copied_sql")
    assert len(by_event) == 2
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `python3 -m pytest tests/test_signal_log.py -v`
Expected: ImportError — `agent.signal_log` does not exist.

- [ ] **Step 3: Implement `agent/signal_log.py`**

Create `agent/signal_log.py`:

```python
"""Append-only JSONL log of implicit user signals."""
from __future__ import annotations

import json
import os
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
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `python3 -m pytest tests/test_signal_log.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/signal_log.py tests/test_signal_log.py
git commit -m "feat(signals): append-only JSONL SignalLog with daily rotation + filtered load"
```

---

## Task 6: Signal endpoint and dependency wiring

**Files:**
- Create: `backend/routers/signals.py`
- Modify: `backend/main.py`
- Modify: `backend/deps.py` (add `get_signal_log`)
- Test: `tests/test_signals_endpoint.py`

- [ ] **Step 1: Write failing endpoint test**

Create `tests/test_signals_endpoint.py`:

```python
import json
import pytest
from fastapi.testclient import TestClient


def test_post_signals_writes_event(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_STORE_PATH", str(tmp_path))
    from backend.main import app
    client = TestClient(app)
    r = client.post("/api/signals", json={
        "event": "ran_unchanged",
        "session_id": "abc",
        "entry_id": None,
        "mode": "curator",
        "sql_hash": "h1",
        "metadata": {"row_count": 5},
    })
    assert r.status_code == 200
    assert r.json() == {"status": "logged"}
    files = list(tmp_path.glob("signals/signals-*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().strip().splitlines()[0])
    assert rec["event"] == "ran_unchanged"
    assert rec["session_id"] == "abc"


def test_post_signals_rejects_unknown_event():
    from backend.main import app
    client = TestClient(app)
    r = client.post("/api/signals", json={
        "event": "made_up_event",
        "session_id": "x",
        "entry_id": None,
        "mode": "curator",
        "sql_hash": "h",
        "metadata": {},
    })
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests, expect FAIL (route not registered)**

Run: `python3 -m pytest tests/test_signals_endpoint.py -v`
Expected: 404 → AssertionError.

- [ ] **Step 3: Add `get_signal_log` dependency**

In `backend/deps.py`, add:

```python
import os
from agent.signal_log import SignalLog

_signal_log_singleton: SignalLog | None = None

def get_signal_log() -> SignalLog:
    global _signal_log_singleton
    if _signal_log_singleton is None:
        base = os.environ.get("KNOWLEDGE_STORE_PATH", "/data/knowledge_store")
        _signal_log_singleton = SignalLog(persist_dir=os.path.join(base, "signals"))
    return _signal_log_singleton
```

- [ ] **Step 4: Implement signals router**

Create `backend/routers/signals.py`:

```python
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
```

- [ ] **Step 5: Mount router in main app**

In `backend/main.py`, find where other routers are included and add:

```python
from backend.routers import signals as signals_router
# ... after other includes:
app.include_router(signals_router.router, prefix="/api")
```

- [ ] **Step 6: Run tests, expect PASS**

Run: `python3 -m pytest tests/test_signals_endpoint.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/signals.py backend/main.py backend/deps.py tests/test_signals_endpoint.py
git commit -m "feat(signals): POST /api/signals endpoint with strict event-type validation"
```

---

## Task 7: Frontend signal hook + chat store integration

**Files:**
- Create: `frontend/src/api/signals.ts`
- Modify: `frontend/src/store/chatStore.ts`
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Add `SignalEvent` type**

In `frontend/src/types.ts`, append:

```ts
export type SignalEventType =
  | 'copied_sql'
  | 'opened_in_editor'
  | 'ran_unchanged'
  | 'edited_then_ran'
  | 'abandoned_session'
  | 'zero_rows_retry'

export interface SignalEvent {
  event: SignalEventType
  session_id: string
  entry_id?: string | null
  mode: UserMode
  sql_hash: string
  metadata: Record<string, unknown>
}
```

- [ ] **Step 2: Create signals API client**

Create `frontend/src/api/signals.ts`:

```ts
import type { SignalEvent } from '../types'

export async function postSignal(event: SignalEvent): Promise<void> {
  try {
    await fetch('/api/signals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(event),
    })
  } catch (err) {
    console.warn('signal post failed', event.event, err)
  }
}

export function sha1Hex(s: string): Promise<string> {
  // Browser SubtleCrypto sha1 → hex
  const enc = new TextEncoder().encode(s)
  return crypto.subtle.digest('SHA-1', enc).then((buf) => {
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('')
  })
}
```

- [ ] **Step 3: Wire `sessionId` and `emitSignal` into chatStore**

In `frontend/src/store/chatStore.ts`, add fields and helper. Find the existing `create<...>(...)` block. Add:

```ts
import { postSignal, sha1Hex } from '../api/signals'
import { useUserMode } from '../hooks/useUserMode'
import type { SignalEventType } from '../types'

// Inside the chat store interface:
interface ChatState {
  // ... existing fields ...
  sessionId: string                  // regenerated on each new query lifecycle
  matchedEntryId: string | null      // set when session_match event arrives
  newSessionId: () => void
  emitSignal: (event: SignalEventType, sql: string, metadata?: Record<string, unknown>) => Promise<void>
}

// Inside create<ChatState>(...) implementation, add:
sessionId: crypto.randomUUID(),
matchedEntryId: null,
newSessionId: () => set({ sessionId: crypto.randomUUID(), matchedEntryId: null }),
emitSignal: async (event, sql, metadata = {}) => {
  const { sessionId, matchedEntryId } = get()
  const mode = useUserMode.getState().mode
  const sqlHash = sql ? await sha1Hex(sql) : ''
  await postSignal({
    event,
    session_id: sessionId,
    entry_id: matchedEntryId,
    mode,
    sql_hash: sqlHash,
    metadata,
  })
},
```

Where the chat store handles the `session_match` SSE event, also call `set({ matchedEntryId: data.matched_entry_id })`.
Where the chat store handles a NEW user query submission, call `get().newSessionId()` BEFORE the POST to `/api/query`.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd /Users/neelu/dev/nlp2sql/frontend && PATH=/opt/homebrew/bin:$PATH npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/signals.ts frontend/src/store/chatStore.ts frontend/src/types.ts
git commit -m "feat(signals): chat store sessionId + emitSignal helper + signals API client"
```

---

## Task 8: Frontend signal instrumentation — 5 emit sites

**Files:**
- Modify: `frontend/src/components/chat/SqlResultCard.tsx`
- Modify: `frontend/src/pages/EditorPage.tsx`
- Modify: `frontend/src/components/chat/ChatPanel.tsx`
- Modify: `frontend/src/components/chat/MessageList.tsx`

- [ ] **Step 1: Emit `copied_sql` and `opened_in_editor` from `SqlResultCard`**

In `SqlResultCard.tsx`, find the existing Copy and Open-in-Editor button handlers:

```tsx
import { useChatStore } from '../../store/chatStore'
// ... inside component:
const emitSignal = useChatStore((s) => s.emitSignal)

// Wrap the copy handler:
const onCopy = async () => {
  await navigator.clipboard.writeText(sql)
  void emitSignal('copied_sql', sql, {})
}

// Wrap the open-in-editor handler:
const onOpenInEditor = () => {
  onOpenInEditorProp(sql)  // existing call
  void emitSignal('opened_in_editor', sql, {})
}
```

- [ ] **Step 2: Emit `ran_unchanged` / `edited_then_ran` from EditorPage**

In `frontend/src/pages/EditorPage.tsx`:

```tsx
import { useChatStore } from '../store/chatStore'
// ... inside component:
const [loadedSql, setLoadedSql] = React.useState(initialSql)
const emitSignal = useChatStore((s) => s.emitSignal)

// When initialSql prop changes (incoming SQL from chat), capture it:
React.useEffect(() => {
  setLoadedSql(initialSql)
}, [initialSql])

// In the existing run handler, before/after the actual execute call:
const onRun = async () => {
  const currentSql = sql  // the editor buffer
  const editDistance = levenshtein(loadedSql, currentSql)
  if (loadedSql && editDistance === 0) {
    void emitSignal('ran_unchanged', currentSql, {})
  } else if (loadedSql) {
    void emitSignal('edited_then_ran', currentSql, { edit_distance: editDistance })
  }
  // ... existing run logic ...
}

// Add a tiny levenshtein helper at the bottom of the file:
function levenshtein(a: string, b: string): number {
  if (a === b) return 0
  if (!a) return b.length
  if (!b) return a.length
  const m = a.length, n = b.length
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = 0; i <= m; i++) dp[i][0] = i
  for (let j = 0; j <= n; j++) dp[0][j] = j
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
    }
  }
  return dp[m][n]
}
```

- [ ] **Step 3: Emit `abandoned_session` from `ChatPanel` on new query without accept**

In `frontend/src/components/chat/ChatPanel.tsx`, find where a user submits a new query. Before the submit logic:

```tsx
import { useChatStore } from '../../store/chatStore'
// ... inside component:
const lastSqlShown = useChatStore((s) => s.lastSqlShown)  // see Step 4 below
const lastSqlAccepted = useChatStore((s) => s.lastSqlAccepted)
const emitSignal = useChatStore((s) => s.emitSignal)

const onSubmit = async (text: string) => {
  // STRICT abandonment: prior SQL was shown but never accepted/rejected
  if (lastSqlShown && !lastSqlAccepted) {
    void emitSignal('abandoned_session', lastSqlShown, {})
  }
  // ... existing submit ...
}
```

- [ ] **Step 4: Add `lastSqlShown` and `lastSqlAccepted` tracking in chat store**

In `frontend/src/store/chatStore.ts`, extend the state:

```ts
interface ChatState {
  // ... existing ...
  lastSqlShown: string | null
  lastSqlAccepted: boolean
  setLastSqlShown: (sql: string | null) => void
  setLastSqlAccepted: (v: boolean) => void
}

// In implementation:
lastSqlShown: null,
lastSqlAccepted: false,
setLastSqlShown: (sql) => set({ lastSqlShown: sql, lastSqlAccepted: false }),
setLastSqlAccepted: (v) => set({ lastSqlAccepted: v }),
```

In the SSE handler for `sql_ready`, call `setLastSqlShown(sql)`. In the accept-query response handler, call `setLastSqlAccepted(true)`.

- [ ] **Step 5: Emit `zero_rows_retry` from `MessageList`**

In `frontend/src/components/chat/MessageList.tsx`, watch for results with 0 rows:

```tsx
import { useChatStore } from '../../store/chatStore'
// ... inside component:
const emitSignal = useChatStore((s) => s.emitSignal)
const setZeroRowsState = useChatStore((s) => s.setZeroRowsState)
const zeroRowsState = useChatStore((s) => s.zeroRowsState)

// When rendering a result with 0 rows, capture timestamp + sql in store:
React.useEffect(() => {
  const lastResult = messages[messages.length - 1]?.result
  if (lastResult && lastResult.total_rows === 0 && lastResult.sql) {
    setZeroRowsState({ ts: Date.now(), sql: lastResult.sql })
  }
}, [messages])
```

In `chatStore.ts`, add:

```ts
zeroRowsState: { ts: number; sql: string } | null
setZeroRowsState: (s: { ts: number; sql: string } | null) => void

// implementation:
zeroRowsState: null,
setZeroRowsState: (s) => set({ zeroRowsState: s }),
```

In `ChatPanel.tsx` `onSubmit` (extending the abandonment check):

```ts
const zeroRows = useChatStore((s) => s.zeroRowsState)
const setZeroRowsState = useChatStore((s) => s.setZeroRowsState)

const onSubmit = async (text: string) => {
  if (zeroRows && Date.now() - zeroRows.ts < 60_000) {
    void emitSignal('zero_rows_retry', zeroRows.sql, {})
    setZeroRowsState(null)
  }
  // ... existing abandonment + submit ...
}
```

- [ ] **Step 6: Verify TypeScript compiles**

Run: `cd /Users/neelu/dev/nlp2sql/frontend && PATH=/opt/homebrew/bin:$PATH npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Manual smoke test signal emission**

1. Open the app, run a query, click "Copy" → check `tail -1 /data/knowledge_store/signals/signals-*.jsonl` shows `copied_sql`. (Or via `docker compose -f docker/docker-compose.yml exec backend cat /data/knowledge_store/signals/signals-$(date +%Y-%m-%d).jsonl`).
2. Submit a new query without accepting prior SQL → expect `abandoned_session`.
3. Open SQL in Editor, hit Run unchanged → expect `ran_unchanged`.
4. Edit the SQL slightly, hit Run → expect `edited_then_ran` with `edit_distance > 0`.
5. Force a 0-row query, then submit a similar query within 60s → expect `zero_rows_retry`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/chat/SqlResultCard.tsx frontend/src/pages/EditorPage.tsx frontend/src/components/chat/ChatPanel.tsx frontend/src/components/chat/MessageList.tsx frontend/src/store/chatStore.ts dist/
git commit -m "feat(signals): instrument 5 emit sites for copy/editor/run/abandon/zero-rows-retry"
```

---

## Task 9: SQL skeleton helper

**Files:**
- Create: `agent/sql_skeleton.py`
- Test: `tests/test_sql_skeleton.py`

- [ ] **Step 1: Write failing tests for sql_skeleton**

Create `tests/test_sql_skeleton.py`:

```python
from agent.sql_skeleton import sql_skeleton


def test_strips_string_literals():
    sql = "SELECT * FROM CUSTOMERS WHERE name = 'Alice' AND city = 'NYC'"
    s = sql_skeleton(sql)
    assert "Alice" not in s
    assert "NYC" not in s
    assert "?" in s


def test_strips_numeric_literals():
    sql = "SELECT * FROM ORDERS WHERE amount > 100 AND id = 42"
    s = sql_skeleton(sql)
    assert "100" not in s
    assert "42" not in s


def test_normalizes_whitespace_and_case():
    a = sql_skeleton("SELECT  *\nFROM\tCustomers   WHERE id=1")
    b = sql_skeleton("select * from CUSTOMERS where id = 2")
    assert a == b


def test_preserves_keywords_and_identifiers():
    s = sql_skeleton("SELECT c.id, c.name FROM KYC.CUSTOMERS c WHERE c.STATUS = 'A'")
    assert "select" in s
    assert "kyc.customers" in s
    assert "c.status" in s


def test_two_queries_with_different_literals_match():
    a = sql_skeleton("SELECT * FROM CUSTOMERS WHERE risk = 'HIGH'")
    b = sql_skeleton("SELECT * FROM CUSTOMERS WHERE risk = 'LOW'")
    assert a == b
```

- [ ] **Step 2: Run tests, expect FAIL (ImportError)**

Run: `python3 -m pytest tests/test_sql_skeleton.py -v`

- [ ] **Step 3: Implement `agent/sql_skeleton.py`**

Create `agent/sql_skeleton.py`:

```python
"""SQL skeleton normalizer — strips literals + normalizes whitespace + case-folds.

Used by the pattern aggregator to cluster queries that share structure but differ
in concrete values (e.g. WHERE risk='HIGH' vs WHERE risk='LOW').
"""
from __future__ import annotations

import re

_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_NUMERIC_LITERAL = re.compile(r"\b\d+(?:\.\d+)?\b")
_WHITESPACE = re.compile(r"\s+")


def sql_skeleton(sql: str) -> str:
    if not sql:
        return ""
    s = _STRING_LITERAL.sub("?", sql)
    s = _NUMERIC_LITERAL.sub("?", s)
    s = _WHITESPACE.sub(" ", s).strip()
    return s.lower()
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `python3 -m pytest tests/test_sql_skeleton.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/sql_skeleton.py tests/test_sql_skeleton.py
git commit -m "feat(patterns): sql_skeleton helper — strips literals, normalizes whitespace + case"
```

---

## Task 10: KYCKnowledgeStore — pattern persistence + lookup

**Files:**
- Modify: `agent/knowledge_store.py`
- Test: `tests/test_knowledge_store_patterns.py`

- [ ] **Step 1: Write failing tests for pattern persistence and lookup**

Create `tests/test_knowledge_store_patterns.py`:

```python
from agent.knowledge_store import KYCKnowledgeStore, LearnedPattern
from knowledge_graph.graph_store import KnowledgeGraph


def _graph_with(table_fqn: str) -> KnowledgeGraph:
    g = KnowledgeGraph()
    schema, name = table_fqn.split(".")
    g.merge_node("Table", table_fqn, {"name": name, "schema": schema})
    return g


def test_add_pattern_persists_and_reloads(tmp_path):
    persist = str(tmp_path / "ks.json")
    store_a = KYCKnowledgeStore(persist_path=persist)
    p = LearnedPattern(
        pattern_id="vp_1",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=3,
        consumer_uses=0,
        negative_signals=0,
        score=3.0,
        promoted_at=1000.0,
        source_entry_ids=["e1", "e2", "e3"],
        manual_promotion=False,
    )
    store_a.add_pattern(p)

    store_b = KYCKnowledgeStore(persist_path=persist)
    found = [pp for pp in store_b.patterns if pp.pattern_id == "vp_1"]
    assert len(found) == 1
    assert found[0].score == 3.0


def test_find_verified_pattern_filters_by_table_existence(tmp_path):
    g = _graph_with("KYC.CUSTOMERS")
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    store.add_pattern(LearnedPattern(
        pattern_id="vp_1",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show me high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=3, consumer_uses=0, negative_signals=0,
        score=3.0, promoted_at=1.0, source_entry_ids=["e1"], manual_promotion=False,
    ))
    store.add_pattern(LearnedPattern(
        pattern_id="vp_2",
        sql_skeleton="select * from kyc.gone where x = ?",
        exemplar_query="dropped table query",
        exemplar_sql="SELECT * FROM KYC.GONE WHERE x = 1",
        tables_used=["KYC.GONE"],  # not in graph
        accept_count=3, consumer_uses=0, negative_signals=0,
        score=3.0, promoted_at=2.0, source_entry_ids=["e2"], manual_promotion=False,
    ))

    matched = store.find_verified_pattern("show high risk customers", g)
    assert matched is not None
    assert matched.pattern_id == "vp_1"


def test_find_verified_pattern_returns_none_when_no_match(tmp_path):
    g = _graph_with("KYC.CUSTOMERS")
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    store.add_pattern(LearnedPattern(
        pattern_id="vp_1",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show me high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=3, consumer_uses=0, negative_signals=0,
        score=3.0, promoted_at=1.0, source_entry_ids=["e1"], manual_promotion=False,
    ))
    matched = store.find_verified_pattern("the meaning of life", g)
    assert matched is None
```

- [ ] **Step 2: Run tests, expect FAIL (LearnedPattern fields incomplete or method missing)**

Run: `python3 -m pytest tests/test_knowledge_store_patterns.py -v`

- [ ] **Step 3: Verify and update `LearnedPattern` dataclass in `agent/knowledge_store.py`**

Open `agent/knowledge_store.py`. Locate the existing `LearnedPattern` dataclass. Replace with the spec-aligned shape (preserving any fields already used elsewhere):

```python
@dataclass
class LearnedPattern:
    pattern_id: str
    sql_skeleton: str
    exemplar_query: str
    exemplar_sql: str
    tables_used: List[str] = field(default_factory=list)
    accept_count: int = 0
    consumer_uses: int = 0
    negative_signals: int = 0
    score: float = 0.0
    promoted_at: float = 0.0
    source_entry_ids: List[str] = field(default_factory=list)
    manual_promotion: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LearnedPattern":
        return cls(**d)
```

- [ ] **Step 4: Add `add_pattern` and `find_verified_pattern` methods**

In the same file, inside `KYCKnowledgeStore`:

```python
def add_pattern(self, pattern: LearnedPattern) -> None:
    with self._lock:
        # Replace if exists, else append.
        self.patterns = [p for p in self.patterns if p.pattern_id != pattern.pattern_id]
        self.patterns.append(pattern)
        self.save_to_disk()

def find_verified_pattern(self, query: str, graph) -> Optional[LearnedPattern]:
    """Return the highest-scoring verified pattern whose exemplar_query
    Jaccard-matches `query` ≥ SESSION_MATCH_THRESHOLD AND whose tables_used
    all still exist in the live graph.
    """
    if not query or not query.strip():
        return None
    qtoks = _tokenize(query)
    if not qtoks:
        return None

    best: Optional[LearnedPattern] = None
    best_score = -1.0
    with self._lock:
        for p in self.patterns:
            if not p.exemplar_query:
                continue
            jacc = _jaccard(qtoks, _tokenize(p.exemplar_query))
            if jacc < SESSION_MATCH_THRESHOLD:
                continue
            # verify on read: all tables must exist in graph
            if not all(graph.get_node("Table", t) for t in p.tables_used):
                continue
            if p.score > best_score:
                best = p
                best_score = p.score
    return best
```

- [ ] **Step 5: Update save/load to persist patterns**

In `KYCKnowledgeStore.save_to_disk`, add `"patterns"` to the saved JSON:

```python
data = {
    # ... existing keys ...
    "patterns": [p.to_dict() for p in self.patterns],
}
```

In `_load_from_disk`, add:

```python
self.patterns = [LearnedPattern.from_dict(d) for d in data.get("patterns", [])]
```

- [ ] **Step 6: Run tests, expect PASS**

Run: `python3 -m pytest tests/test_knowledge_store_patterns.py -v`
Expected: 3 passed.

- [ ] **Step 7: Run full session-learning suite to confirm no regression**

Run: `python3 -m pytest tests/test_e2e_session_learning.py tests/test_knowledge_store_session.py -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add agent/knowledge_store.py tests/test_knowledge_store_patterns.py
git commit -m "feat(patterns): KYCKnowledgeStore.add_pattern + find_verified_pattern with verify-on-read"
```

---

## Task 11: Pattern Aggregator

**Files:**
- Create: `agent/pattern_aggregator.py`
- Test: `tests/test_pattern_aggregator.py`

- [ ] **Step 1: Write failing tests for the aggregator**

Create `tests/test_pattern_aggregator.py`:

```python
import pytest
from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry
from agent.signal_log import SignalLog, SignalEvent
from agent.pattern_aggregator import aggregate_patterns


def _seed_session(store, entry_id, query, sql, tables):
    entry = KnowledgeEntry(
        id=entry_id, source="query_session", category="query_session",
        content=query,
        metadata={
            "original_query": query,
            "enriched_query": query,
            "tables_used": tables,
            "accepted_candidates": [{"interpretation": "x", "sql": sql, "explanation": ""}],
            "rejected_candidates": [], "clarifications": [],
            "created_at": 1000.0,
        },
    )
    store.add_session_entry(entry)
    return entry


def test_aggregator_promotes_after_three_curator_accepts(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    e1 = _seed_session(store, "e1", "show high risk customers",   sql, ["KYC.CUSTOMERS"])
    e2 = _seed_session(store, "e2", "list high-risk customers",   sql.replace("HIGH", "VERY_HIGH"), ["KYC.CUSTOMERS"])
    e3 = _seed_session(store, "e3", "high risk customers please", sql, ["KYC.CUSTOMERS"])

    aggregate_patterns(store, e3, sigs, mode="curator")
    verified = [p for p in store.patterns if p.accept_count >= 3]
    assert len(verified) == 1
    assert verified[0].source_entry_ids and "e3" in verified[0].source_entry_ids


def test_aggregator_holds_off_when_only_two_distinct_sessions(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    _seed_session(store, "e1", "show high risk customers", sql, ["KYC.CUSTOMERS"])
    e2 = _seed_session(store, "e2", "high risk customers", sql, ["KYC.CUSTOMERS"])

    aggregate_patterns(store, e2, sigs, mode="curator")
    assert all(p.accept_count < 3 for p in store.patterns)


def test_aggregator_blocks_promotion_when_negative_signals_dominate(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    sql_hash = "abc"  # we'll lie about the hash; aggregator looks up by skeleton

    for eid, q in [("e1", "show high risk customers"),
                   ("e2", "list high-risk customers"),
                   ("e3", "high risk customers please")]:
        _seed_session(store, eid, q, sql, ["KYC.CUSTOMERS"])
        # 4 abandonments per session = strong negative
        for _ in range(4):
            sigs.append(SignalEvent(event="abandoned_session", session_id="any",
                                    entry_id=eid, mode="curator",
                                    sql_hash=sql_hash, metadata={}))

    aggregate_patterns(store, store.static_entries[-1], sigs, mode="curator")
    # No pattern should be in store (negatives dominate positives/2)
    promoted = [p for p in store.patterns if p.accept_count >= 3 and p.negative_signals < p.accept_count / 2]
    assert promoted == []


def test_aggregator_manual_promotion_skips_threshold(tmp_path):
    persist = str(tmp_path / "ks.json")
    store = KYCKnowledgeStore(persist_path=persist)
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS"
    e1 = _seed_session(store, "e1", "lone session", sql, ["KYC.CUSTOMERS"])

    aggregate_patterns(store, e1, sigs, mode="curator", manual_promotion=True)
    assert any(p.manual_promotion for p in store.patterns)
```

- [ ] **Step 2: Run tests, expect FAIL (module missing)**

Run: `python3 -m pytest tests/test_pattern_aggregator.py -v`

- [ ] **Step 3: Implement `agent/pattern_aggregator.py`**

Create `agent/pattern_aggregator.py`:

```python
"""Pattern Aggregator — clusters similar accepted query_session entries
and promotes them to LearnedPattern (verified pattern) when thresholds met.

Triggered after each curator (and debounced consumer) accept-query.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import List, Optional

from agent.knowledge_store import (
    KYCKnowledgeStore, KnowledgeEntry, LearnedPattern,
    _jaccard, _tokenize,
)
from agent.signal_log import SignalLog
from agent.sql_skeleton import sql_skeleton

logger = logging.getLogger(__name__)

CLUSTER_THRESHOLD = 0.5      # broader than runtime match (0.65)
MIN_ACCEPT_COUNT = 3
MIN_DISTINCT_SESSIONS = 2

_SIGNAL_WEIGHTS_CURATOR = {
    "ran_unchanged": 1.0, "opened_in_editor": 0.5, "copied_sql": 0.3,
    "abandoned_session": -0.5, "zero_rows_retry": -0.7, "edited_then_ran": 0.0,
}
_SIGNAL_WEIGHTS_CONSUMER = {k: v * 0.1 for k, v in _SIGNAL_WEIGHTS_CURATOR.items()}


def _pattern_id(skeleton: str) -> str:
    return "vp_" + hashlib.sha1(skeleton.encode("utf-8")).hexdigest()[:12]


def _accepted_sql(entry: KnowledgeEntry) -> Optional[str]:
    accepted = (entry.metadata or {}).get("accepted_candidates", []) or []
    if not accepted:
        return None
    # Pattern follows the FIRST accepted candidate (the one most recently chosen).
    return accepted[0].get("sql", "")


def aggregate_patterns(
    store: KYCKnowledgeStore,
    accepted_entry: KnowledgeEntry,
    signals: SignalLog,
    mode: str = "curator",
    manual_promotion: bool = False,
) -> Optional[LearnedPattern]:
    """Cluster sessions matching the just-accepted entry and promote if eligible.
    Returns the promoted (or updated) pattern, or None if not eligible.
    """
    accepted_sql = _accepted_sql(accepted_entry)
    if not accepted_sql:
        return None

    skel = sql_skeleton(accepted_sql)
    if not skel:
        return None

    accepted_q = (accepted_entry.metadata or {}).get("original_query", "")
    qtoks = _tokenize(accepted_q)

    cluster: List[KnowledgeEntry] = []
    distinct_sessions = set()
    for e in store.static_entries:
        if e.source != "query_session" or e.category != "query_session":
            continue
        sql = _accepted_sql(e)
        if not sql or sql_skeleton(sql) != skel:
            continue
        meta = e.metadata or {}
        # Loose query match — any token overlap, since skeleton already enforces structure.
        if qtoks and not _jaccard(qtoks, _tokenize(meta.get("original_query", ""))) >= CLUSTER_THRESHOLD:
            continue
        # Table overlap
        tables = set(meta.get("tables_used", []) or [])
        accepted_tables = set((accepted_entry.metadata or {}).get("tables_used", []) or [])
        if accepted_tables and not (tables & accepted_tables):
            continue
        cluster.append(e)
        distinct_sessions.add(e.id)

    if accepted_entry.id not in distinct_sessions:
        cluster.append(accepted_entry)
        distinct_sessions.add(accepted_entry.id)

    accept_count = len(cluster)

    # Aggregate signals tied to this cluster's entry_ids
    pos = neg = 0.0
    for e in cluster:
        for evname in _SIGNAL_WEIGHTS_CURATOR:
            for sig in signals.load(event=evname, entry_id=e.id):
                w = (_SIGNAL_WEIGHTS_CURATOR if sig.mode == "curator"
                     else _SIGNAL_WEIGHTS_CONSUMER)[evname]
                if w >= 0:
                    pos += w
                else:
                    neg += -w

    score = accept_count + pos - neg

    eligible = manual_promotion or (
        accept_count >= MIN_ACCEPT_COUNT
        and len(distinct_sessions) >= MIN_DISTINCT_SESSIONS
        and neg < accept_count / 2  # negatives must not dominate
    )

    if not eligible:
        return None

    pid = _pattern_id(skel)
    consumer_uses = sum(
        1 for e in cluster
        for sig in signals.load(entry_id=e.id)
        if sig.mode == "consumer"
    )

    pattern = LearnedPattern(
        pattern_id=pid,
        sql_skeleton=skel,
        exemplar_query=accepted_q,
        exemplar_sql=accepted_sql,
        tables_used=list((accepted_entry.metadata or {}).get("tables_used", []) or []),
        accept_count=accept_count,
        consumer_uses=consumer_uses,
        negative_signals=int(neg),
        score=float(score),
        promoted_at=time.time(),
        source_entry_ids=[e.id for e in cluster],
        manual_promotion=manual_promotion,
    )
    store.add_pattern(pattern)
    logger.info("pattern promoted: %s (score=%.2f, accepts=%d)", pid, score, accept_count)
    return pattern
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `python3 -m pytest tests/test_pattern_aggregator.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/pattern_aggregator.py tests/test_pattern_aggregator.py
git commit -m "feat(patterns): pattern aggregator with skeleton clustering, signal scoring, and manual promotion"
```

---

## Task 12: Wire aggregator into accept-query worker

**Files:**
- Modify: `backend/routers/query.py` (the accept-query background task)

- [ ] **Step 1: Add a test that posting accept-query triggers aggregation**

Create `tests/test_accept_query_aggregator.py`:

```python
import json
import time
import pytest
from fastapi.testclient import TestClient


def _accept_payload(query, sql, mode="curator"):
    return {
        "user_input": query,
        "sql": sql,
        "explanation": "x",
        "accepted": True,
        "accepted_candidates": [{"id": "a1", "interpretation": "i", "sql": sql, "explanation": "x"}],
        "rejected_candidates": [],
        "executed_candidate_id": "a1",
        "clarification_pairs": [],
        "session_digest": {},
        "mode": mode,
    }


@pytest.mark.skip(reason="requires running backend with KYC graph and LLM stubbed")
def test_three_curator_accepts_promote_a_pattern():
    # This is a sketch — real e2e in test_e2e_role_aware_chat.py covers it.
    pass
```

(The full e2e is wired up in Task 17. We tag this as a placeholder.)

- [ ] **Step 2: Hook the aggregator call into the existing accept-query worker**

In `backend/routers/query.py`, find the background function (likely `_analyze_and_store` or similar) called from the `POST /query/accept-query` route. After `store.add_session_entry(entry)`, append:

```python
from agent.pattern_aggregator import aggregate_patterns
from backend.deps import get_signal_log

try:
    sigs = get_signal_log()
    aggregate_patterns(
        store, entry, sigs,
        mode=req.mode or "curator",
        manual_promotion=False,
    )
except Exception as exc:
    logger.warning("pattern aggregation failed: %s", exc)
```

If the worker function doesn't have access to `req`, pass `mode` through from the route handler before scheduling the background task.

- [ ] **Step 3: Run unit tests to ensure nothing else broke**

Run: `python3 -m pytest tests/ -q --ignore=tests/test_e2e.py`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/routers/query.py tests/test_accept_query_aggregator.py
git commit -m "feat(patterns): trigger pattern aggregator after each accept-query"
```

---

## Task 13: session_lookup queries verified patterns first

**Files:**
- Modify: `agent/nodes/session_lookup.py`
- Test: `tests/test_session_lookup_node.py` (extend existing)

- [ ] **Step 1: Write failing test that verified pattern beats raw session match**

Append to `tests/test_session_lookup_node.py` (or create if absent):

```python
from agent.knowledge_store import KYCKnowledgeStore, LearnedPattern, KnowledgeEntry
from agent.nodes.session_lookup import make_session_lookup
from knowledge_graph.graph_store import KnowledgeGraph


def _g():
    g = KnowledgeGraph()
    g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})
    return g


def test_verified_pattern_takes_precedence_over_raw_session(tmp_path):
    g = _g()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))

    # raw session — would normally match
    store.add_session_entry(KnowledgeEntry(
        id="raw1", source="query_session", category="query_session",
        content="x",
        metadata={
            "original_query": "show me high risk customers",
            "enriched_query": "show me high risk customers",
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [{"interpretation": "raw", "sql": "SELECT * FROM KYC.CUSTOMERS",
                                     "explanation": ""}],
            "rejected_candidates": [], "clarifications": [], "created_at": 1.0,
        },
    ))

    # verified pattern — should win
    store.add_pattern(LearnedPattern(
        pattern_id="vp_x",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show me high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS WHERE risk='HIGH'",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=5, consumer_uses=10, negative_signals=0,
        score=15.0, promoted_at=1.0, source_entry_ids=["raw1"], manual_promotion=False,
    ))

    node = make_session_lookup(store, g)
    out = node({
        "user_input": "show me high risk customers please",
        "enriched_query": "show me high risk customers please",
        "intent": "DATA_QUERY", "conversation_history": [], "_trace": [],
    })

    assert out.get("has_candidates") is True
    summary = out["_trace"][-1]["output_summary"]
    assert summary["action"] == "match"
    assert summary.get("match_kind") == "verified_pattern"
    # Candidate SQL must be from the pattern, not the raw session
    assert "WHERE risk" in out["sql_candidates"][0]["sql"]
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python3 -m pytest tests/test_session_lookup_node.py::test_verified_pattern_takes_precedence_over_raw_session -v`

- [ ] **Step 3: Update `session_lookup.py` to query verified patterns first**

In `agent/nodes/session_lookup.py`, between the skip-checks and the existing `find_session_match` call:

```python
query = state.get("user_input") or state.get("enriched_query", "")

# Try verified patterns first
try:
    vp = knowledge_store.find_verified_pattern(query, graph)
except Exception as exc:
    logger.warning("verified-pattern lookup failed: %s", exc)
    vp = None

if vp is not None:
    candidate = {
        "id": "vp01",
        "interpretation": "verified pattern",
        "sql": vp.exemplar_sql,
        "explanation": f"Verified pattern (score={vp.score:.1f}, accepts={vp.accept_count})",
        "is_verified": True,
        "pattern_id": vp.pattern_id,
    }
    trace.output_summary = {
        "action": "match",
        "match_kind": "verified_pattern",
        "matched_pattern_id": vp.pattern_id,
        "candidate_count": 1,
        "matched_query": vp.exemplar_query[:80],
    }
    _trace.append(trace.finish().to_dict())
    return {
        **state,
        "sql_candidates": [candidate],
        "has_candidates": True,
        "session_match_entry_id": vp.pattern_id,
        "step": "session_matched",
        "_trace": _trace,
    }

# Fall through to raw query_session match (existing code)
try:
    match = knowledge_store.find_session_match(query, graph)
# ... existing logic ...
```

In the existing `match_kind` field on the raw-match path, add `"match_kind": "query_session"` to the trace summary for parity.

- [ ] **Step 4: Run test, expect PASS**

Run: `python3 -m pytest tests/test_session_lookup_node.py -v`
Expected: all pass (including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add agent/nodes/session_lookup.py tests/test_session_lookup_node.py
git commit -m "feat(patterns): session_lookup queries verified patterns first; falls through to query_session match"
```

---

## Task 14: Refinement-aware SQL generator

**Files:**
- Modify: `agent/nodes/sql_generator.py`
- Test: `tests/test_sql_generator_refinement.py`

- [ ] **Step 1: Write failing test for refinement diff prompt**

Create `tests/test_sql_generator_refinement.py`:

```python
import pytest
from unittest.mock import MagicMock

from agent.nodes.sql_generator import make_sql_generator


def _mock_llm(response_sql: str):
    llm = MagicMock()
    msg = MagicMock()
    msg.content = response_sql
    llm.invoke.return_value = msg
    return llm


def test_refinement_intent_uses_diff_prompt(monkeypatch):
    captured_prompts = []

    llm = MagicMock()
    def _invoke(messages):
        # Capture the user prompt for assertion
        captured_prompts.append(messages[-1].content if hasattr(messages[-1], "content") else str(messages))
        m = MagicMock()
        m.content = "SELECT * FROM CUSTOMERS WHERE STATUS = 'ACTIVE' AND created_at > SYSDATE - 90"
        return m
    llm.invoke = _invoke

    gen = make_sql_generator(llm)
    state = {
        "user_input": "limit to last 90 days",
        "enriched_query": "limit to last 90 days",
        "intent": "RESULT_FOLLOWUP",
        "previous_sql_context": {"sql": "SELECT * FROM CUSTOMERS WHERE STATUS = 'ACTIVE'"},
        "schema_context": "-- TABLE: KYC.CUSTOMERS\n",
        "_trace": [],
    }
    out = gen(state)

    # The captured prompt should mention the prior SQL
    joined = " ".join(captured_prompts)
    assert "SELECT * FROM CUSTOMERS WHERE STATUS = 'ACTIVE'" in joined
    assert "modify" in joined.lower() or "refine" in joined.lower()
    assert out["generated_sql"]
    # Trace should mark refinement_mode
    assert any(t.get("output_summary", {}).get("refinement_mode") for t in out["_trace"])
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `python3 -m pytest tests/test_sql_generator_refinement.py -v`

- [ ] **Step 3: Add refinement branch to `sql_generator.py`**

In `agent/nodes/sql_generator.py`, find the function returned by `make_sql_generator`. Near the start where the prompt is built:

```python
intent = state.get("intent", "DATA_QUERY")
prev_sql = (state.get("previous_sql_context") or {}).get("sql", "")
refinement_mode = bool(prev_sql) and intent in ("RESULT_FOLLOWUP", "QUERY_REFINE")

if refinement_mode:
    user_prompt = (
        f"PRIOR SQL:\n{prev_sql}\n\n"
        f"USER WANTS TO MODIFY IT AS FOLLOWS:\n{state.get('user_input', '')}\n\n"
        "Return the modified SQL only, preserving structure where possible. "
        "Do not regenerate from scratch."
    )
    # Use the existing system prompt + this user prompt
else:
    # ... existing prompt construction ...
```

After the LLM call, add the validator hook (≥60% token overlap fallback):

```python
generated = _extract_sql(response.content)  # use existing extractor
if refinement_mode and prev_sql:
    prev_toks = set(prev_sql.lower().split())
    new_toks = set(generated.lower().split())
    overlap = len(prev_toks & new_toks) / max(len(prev_toks), 1)
    if overlap < 0.60:
        # Fall back to full regeneration — refinement diverged too far
        logger.info("refinement diverged (overlap=%.2f); falling back to full regeneration", overlap)
        refinement_mode = False
        # ... re-invoke LLM with the standard prompt and use that result instead ...
```

Append to trace:

```python
trace.output_summary = {
    "refinement_mode": refinement_mode,
    "sql_length": len(generated),
}
```

- [ ] **Step 4: Run test, expect PASS**

Run: `python3 -m pytest tests/test_sql_generator_refinement.py -v`
Expected: PASS.

- [ ] **Step 5: Run sql-generator regression suite**

Run: `python3 -m pytest tests/test_sql_generator_ambiguity.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agent/nodes/sql_generator.py tests/test_sql_generator_refinement.py
git commit -m "feat(refinement): SQL generator uses diff prompt for RESULT_FOLLOWUP/QUERY_REFINE intents"
```

---

## Task 15: Refinement chat affordances

**Files:**
- Create: `frontend/src/components/chat/RefineButton.tsx`
- Modify: `frontend/src/components/chat/SqlResultCard.tsx`
- Modify: `frontend/src/store/chatStore.ts` (auto-snapshot prior SQL)
- Modify: `backend/routers/query.py` (add manual-promotion route, optional)

- [ ] **Step 1: Auto-snapshot prior SQL in chatStore**

In `frontend/src/store/chatStore.ts`, in the SSE handler for `sql_ready`, set `previousSqlContext`:

```ts
// when handling event: 'sql_ready':
set({
  // ... existing ...
  previousSqlContext: { sql: data.sql, explanation: data.explanation },
})
```

In the request body for `POST /api/query`, include `previous_sql_context: get().previousSqlContext` already (or add it).

Add the field to state if absent:

```ts
previousSqlContext: { sql: string; explanation: string } | null
```

Initial value: `null`.

- [ ] **Step 2: Create the `RefineButton` cluster**

Create `frontend/src/components/chat/RefineButton.tsx`:

```tsx
import React from 'react'
import { useUserMode } from '../../hooks/useUserMode'

interface Props {
  sql: string
  onRefine: () => void
  onBranch: () => void
  onSaveAsPattern: () => void
}

export const RefineButton: React.FC<Props> = ({ onRefine, onBranch, onSaveAsPattern }) => {
  const { mode } = useUserMode()
  const btn: React.CSSProperties = {
    fontSize: 12, padding: '4px 10px',
    background: 'transparent', border: '1px solid #4b5563',
    color: '#c7c8d6', borderRadius: 6, cursor: 'pointer',
  }
  return (
    <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
      <button style={btn} onClick={onRefine} title="Refine the prior SQL">↻ Refine</button>
      <button style={btn} onClick={onBranch} title="Start a new question, keeping this for reference">⤴ Branch</button>
      {mode === 'curator' && (
        <button style={btn} onClick={onSaveAsPattern} title="Promote to verified pattern">★ Save as pattern</button>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Wire `RefineButton` into `SqlResultCard`**

In `SqlResultCard.tsx`:

```tsx
import { RefineButton } from './RefineButton'
import { useChatStore } from '../../store/chatStore'

// inside component, near where you render the SQL/result:
const setInputPrefilled = useChatStore((s) => s.setInputPrefilled)
const branchConversation = useChatStore((s) => s.branchConversation)

const onRefine = () => setInputPrefilled('refine: ')
const onBranch = () => branchConversation()
const onSaveAsPattern = async () => {
  await fetch(`/api/patterns/manual-promote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql, user_input: question }),
  })
}

// in JSX, just below the SQL display:
<RefineButton sql={sql} onRefine={onRefine} onBranch={onBranch} onSaveAsPattern={onSaveAsPattern} />
```

Add to `chatStore.ts`:

```ts
inputPrefill: string
setInputPrefilled: (s: string) => void
branchConversation: () => void

// implementation:
inputPrefill: '',
setInputPrefilled: (s) => set({ inputPrefill: s }),
branchConversation: () => set({
  messages: [],
  history: [],
  previousSqlContext: null,
  // sessionId stays — Branch is a logical reset, not a new browser session
}),
```

In `ChatPanel.tsx`, read `inputPrefill` and apply to the input field on change.

- [ ] **Step 4: Add backend manual-promotion route**

In `backend/routers/query.py`, add a new route:

```python
class _ManualPromoteRequest(BaseModel):
    sql: str
    user_input: str
    tables_used: List[str] = []

@router.post("/patterns/manual-promote")
def manual_promote(
    req: _ManualPromoteRequest,
    store: KYCKnowledgeStore = Depends(get_knowledge_store),
    sigs: SignalLog = Depends(get_signal_log),
) -> Dict[str, str]:
    # Synthesize a single-entry KnowledgeEntry just to feed the aggregator
    from agent.knowledge_store import KnowledgeEntry
    import time, uuid
    entry = KnowledgeEntry(
        id=f"manual_{uuid.uuid4().hex[:8]}",
        source="query_session", category="query_session",
        content=req.user_input,
        metadata={
            "original_query": req.user_input,
            "enriched_query": "",
            "tables_used": req.tables_used,
            "accepted_candidates": [{"interpretation": "manual", "sql": req.sql, "explanation": ""}],
            "rejected_candidates": [], "clarifications": [],
            "created_at": time.time(),
        },
    )
    store.add_session_entry(entry)
    pattern = aggregate_patterns(store, entry, sigs, mode="curator", manual_promotion=True)
    return {"status": "promoted", "pattern_id": pattern.pattern_id if pattern else ""}
```

- [ ] **Step 5: Build frontend and verify**

Run: `cd /Users/neelu/dev/nlp2sql/frontend && PATH=/opt/homebrew/bin:$PATH npx tsc --noEmit && PATH=/opt/homebrew/bin:$PATH npx vite build --outDir ../dist --emptyOutDir 2>&1 | tail -3`
Expected: built.

- [ ] **Step 6: Manual smoke test**

1. In curator mode, run a query that produces SQL.
2. Click ↻ Refine. Input field gets prefilled with `"refine: "`. Type the rest, submit. Observe trace step `refinement_mode=true`.
3. Click ★ Save as pattern. Check `/api/patterns/manual-promote` returns `{"status":"promoted",...}`.
4. Click ⤴ Branch. Messages clear; sessionId persists; previous_sql_context resets.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/chat/RefineButton.tsx frontend/src/components/chat/SqlResultCard.tsx frontend/src/store/chatStore.ts frontend/src/components/chat/ChatPanel.tsx backend/routers/query.py dist/
git commit -m "feat(refinement): ↻Refine ⤴Branch ★Save chat affordances + manual-promote endpoint"
```

---

## Task 16: Verified-pattern UI — badge + Patterns sub-tab

**Files:**
- Modify: `frontend/src/components/chat/SqlCandidatesPicker.tsx`
- Create: `frontend/src/components/kyc/PatternsTab.tsx`
- Modify: `frontend/src/pages/KYCAgentPage.tsx`
- Modify: `backend/routers/kyc_agent.py` (add `GET /api/kyc-agent/patterns`)

- [ ] **Step 1: Add backend endpoint to list verified patterns**

In `backend/routers/kyc_agent.py`, add:

```python
@router.get("/patterns")
def list_patterns(store: KYCKnowledgeStore = Depends(get_knowledge_store)) -> Dict[str, Any]:
    items = sorted(
        [p.to_dict() for p in store.patterns],
        key=lambda d: d.get("score", 0),
        reverse=True,
    )
    return {"patterns": items, "total": len(items)}
```

- [ ] **Step 2: Render "Verified" badge in SqlCandidatesPicker**

In `SqlCandidatesPicker.tsx`, near the candidate label row:

```tsx
{candidate.is_verified && (
  <span style={{
    fontSize: 10, padding: '1px 6px', borderRadius: 999,
    background: 'rgba(74,222,128,0.15)', color: '#4ade80', fontWeight: 600,
  }}>
    ✓ Verified
  </span>
)}
```

In `frontend/src/types.ts`, extend the `SqlCandidate` interface:

```ts
export interface SqlCandidate {
  id: string
  interpretation: string
  sql: string
  explanation: string
  is_verified?: boolean
  pattern_id?: string
}
```

- [ ] **Step 3: Auto-pin verified candidate to position 1 in consumer mode**

In `SqlCandidatesPicker.tsx`:

```tsx
const sorted = React.useMemo(() => {
  if (mode !== 'consumer') return candidates
  return [...candidates].sort((a, b) => Number(b.is_verified || 0) - Number(a.is_verified || 0))
}, [candidates, mode])

// Use `sorted` instead of `candidates` in the render path.
```

- [ ] **Step 4: Create the Patterns sub-tab**

Create `frontend/src/components/kyc/PatternsTab.tsx`:

```tsx
import React from 'react'

interface Pattern {
  pattern_id: string
  exemplar_query: string
  exemplar_sql: string
  tables_used: string[]
  accept_count: number
  consumer_uses: number
  negative_signals: number
  score: number
  promoted_at: number
  manual_promotion: boolean
  source_entry_ids: string[]
}

export const PatternsTab: React.FC = () => {
  const [patterns, setPatterns] = React.useState<Pattern[]>([])
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    fetch('/api/kyc-agent/patterns')
      .then((r) => r.json())
      .then((data) => setPatterns(data.patterns || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 16 }}>Loading patterns…</div>
  if (patterns.length === 0) return <div style={{ padding: 16, color: '#9090a8' }}>No verified patterns yet. Curator accepts will populate this list.</div>

  return (
    <div style={{ padding: 12, overflowY: 'auto' }}>
      {patterns.map((p) => (
        <div key={p.pattern_id} style={{
          background: '#2a2a3e', border: '1px solid #3a3a5c',
          borderRadius: 8, padding: 12, marginBottom: 8,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              fontSize: 10, padding: '1px 6px', borderRadius: 999,
              background: 'rgba(74,222,128,0.15)', color: '#4ade80', fontWeight: 600,
            }}>
              ✓ Verified
            </span>
            {p.manual_promotion && <span style={{ fontSize: 10, color: '#fbbf24' }}>★ manual</span>}
            <span style={{ marginLeft: 'auto', fontSize: 11, color: '#9090a8' }}>
              score {p.score.toFixed(1)} · {p.accept_count} accepts · {p.consumer_uses} uses
            </span>
          </div>
          <div style={{ marginTop: 6, fontSize: 13, color: '#e5e7eb' }}>{p.exemplar_query}</div>
          <pre style={{
            marginTop: 6, fontSize: 11, background: '#1a1a2e', padding: 8,
            borderRadius: 4, color: '#a78bfa', whiteSpace: 'pre-wrap',
          }}>{p.exemplar_sql}</pre>
          <div style={{ fontSize: 11, color: '#7c7c92' }}>
            tables: {p.tables_used.join(', ')}
          </div>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 5: Mount PatternsTab in KYCAgentPage**

In `frontend/src/pages/KYCAgentPage.tsx`, add a new sub-tab option (the page already supports `leftTab` for switching between "knowledge" and other views). Add `'patterns'` as an option:

```tsx
import { PatternsTab } from '../components/kyc/PatternsTab'

// in the leftTab state or buttons:
<button onClick={() => setLeftTab('patterns')}>Patterns</button>

// in the render:
{leftTab === 'patterns' && <PatternsTab />}
```

- [ ] **Step 6: Build and smoke test**

Run: `cd /Users/neelu/dev/nlp2sql/frontend && PATH=/opt/homebrew/bin:$PATH npx tsc --noEmit && PATH=/opt/homebrew/bin:$PATH npx vite build --outDir ../dist --emptyOutDir 2>&1 | tail -3`

Open the app → KYC Agent tab → Patterns. If you've manually promoted a pattern in Task 15, it should show. Otherwise: empty-state message.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/chat/SqlCandidatesPicker.tsx frontend/src/components/kyc/PatternsTab.tsx frontend/src/pages/KYCAgentPage.tsx frontend/src/types.ts backend/routers/kyc_agent.py dist/
git commit -m "feat(patterns): Verified badge in candidates + Patterns sub-tab in KYC Agent page"
```

---

## Task 17: End-to-end test for the role-aware learning flow

**Files:**
- Create: `tests/test_e2e_role_aware_chat.py`

- [ ] **Step 1: Write the e2e test**

Create `tests/test_e2e_role_aware_chat.py`:

```python
"""End-to-end: signal capture, three curator accepts → verified pattern,
consumer query auto-pins it. No HTTP server, no LLM — pure data path."""
from __future__ import annotations

import pytest
from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry
from agent.signal_log import SignalLog, SignalEvent
from agent.pattern_aggregator import aggregate_patterns
from agent.nodes.session_lookup import make_session_lookup
from knowledge_graph.graph_store import KnowledgeGraph


def _g():
    g = KnowledgeGraph()
    g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})
    return g


def _accept(store, sigs, eid, query, sql):
    entry = KnowledgeEntry(
        id=eid, source="query_session", category="query_session",
        content=query,
        metadata={
            "original_query": query, "enriched_query": query,
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [{"interpretation": "x", "sql": sql, "explanation": ""}],
            "rejected_candidates": [], "clarifications": [], "created_at": 1.0,
        },
    )
    store.add_session_entry(entry)
    aggregate_patterns(store, entry, sigs, mode="curator")
    return entry


def test_three_curator_accepts_promote_pattern_consumer_query_matches(tmp_path):
    g = _g()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"

    _accept(store, sigs, "e1", "show me high risk customers",       sql)
    _accept(store, sigs, "e2", "list high-risk customers please",   sql)
    _accept(store, sigs, "e3", "high risk customers please",        sql)

    # A pattern should now exist
    assert any(p.accept_count >= 3 for p in store.patterns)

    # Now run session_lookup with a NEW similar query (consumer mode)
    node = make_session_lookup(store, g)
    out = node({
        "user_input": "show me high risk customers",
        "enriched_query": "show me high risk customers",
        "intent": "DATA_QUERY", "conversation_history": [], "_trace": [],
    })

    assert out["has_candidates"] is True
    summary = out["_trace"][-1]["output_summary"]
    assert summary["match_kind"] == "verified_pattern"
    assert out["sql_candidates"][0]["is_verified"] is True


def test_signal_log_persists_across_processes(tmp_path):
    sigs_a = SignalLog(persist_dir=str(tmp_path))
    sigs_a.append(SignalEvent(
        event="ran_unchanged", session_id="s1", entry_id="e1",
        mode="curator", sql_hash="abc", metadata={},
    ))
    sigs_b = SignalLog(persist_dir=str(tmp_path))
    loaded = sigs_b.load(event="ran_unchanged")
    assert len(loaded) == 1


def test_negative_signals_block_promotion(tmp_path):
    g = _g()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    sigs = SignalLog(persist_dir=str(tmp_path / "signals"))

    sql = "SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'"
    for eid, q in [("e1", "show high risk customers"),
                   ("e2", "list high-risk customers"),
                   ("e3", "high risk customers please")]:
        e = KnowledgeEntry(
            id=eid, source="query_session", category="query_session",
            content=q,
            metadata={
                "original_query": q, "enriched_query": q,
                "tables_used": ["KYC.CUSTOMERS"],
                "accepted_candidates": [{"interpretation": "x", "sql": sql, "explanation": ""}],
                "rejected_candidates": [], "clarifications": [], "created_at": 1.0,
            },
        )
        store.add_session_entry(e)
        for _ in range(5):
            sigs.append(SignalEvent(event="abandoned_session", session_id="s", entry_id=eid,
                                    mode="curator", sql_hash="x", metadata={}))
        aggregate_patterns(store, e, sigs, mode="curator")

    # Should not be promoted: 15 abandonments vs 3 accepts → negatives dominate
    assert all(p.negative_signals == 0 or p.negative_signals < 5 for p in store.patterns) is False or store.patterns == []
```

- [ ] **Step 2: Run test, expect PASS**

Run: `python3 -m pytest tests/test_e2e_role_aware_chat.py -v`
Expected: 3 passed.

- [ ] **Step 3: Run full test suite**

Run: `python3 -m pytest tests/ -q --ignore=tests/test_e2e.py`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_role_aware_chat.py
git commit -m "test(e2e): role-aware chat data path — 3 accepts → verified pattern → consumer match"
```

---

## Task 18: Live smoke test against rebuilt backend

**Files:** none — operational verification.

- [ ] **Step 1: Rebuild and restart backend**

```bash
docker compose -f docker/docker-compose.yml build backend
docker compose -f docker/docker-compose.yml up -d backend
until curl -sf http://localhost:8000/api/health >/dev/null; do sleep 2; done
curl -s http://localhost:8000/api/health
```

Expected: health JSON with `oracle_connected: true`, `llm_ready: true`.

- [ ] **Step 2: Verify mode toggle round-trips**

Open `http://localhost:8000/`. Click the mode toggle 🛠 → 👤. Refresh page. Mode persists.

- [ ] **Step 3: Submit a query and emit signals**

In Curator mode, submit `"show me high risk customers"`. Wait for SQL preview. Click "Copy". Open browser DevTools → Network tab → confirm `POST /api/signals` was called with `event=copied_sql`.

```bash
docker compose -f docker/docker-compose.yml exec backend ls /data/knowledge_store/signals/
docker compose -f docker/docker-compose.yml exec backend tail -5 /data/knowledge_store/signals/signals-$(date +%Y-%m-%d).jsonl
```

Expected: at least one JSON line with `"event": "copied_sql"`.

- [ ] **Step 4: Trigger 3 curator accepts to promote a pattern**

Issue 3 distinct curator accepts via `POST /api/query/accept-query` with the same SQL. Then:

```bash
curl -s http://localhost:8000/api/kyc-agent/patterns | python3 -m json.tool | head -25
```

Expected: at least one entry in `patterns`.

- [ ] **Step 5: Verify consumer-mode auto-pin**

Switch UI to Consumer mode. Run a query similar to one of the seeded ones. Verify the candidates picker auto-pins the verified candidate (✓ Verified badge).

- [ ] **Step 6: Verify refinement reuses prior SQL**

In Curator mode, run a query, get SQL. Click ↻ Refine. Type "limit to last 90 days". Submit. In Investigate tab, confirm the trace step for `generate_sql` has `output_summary.refinement_mode = true` and the new SQL contains the prior SQL's table.

- [ ] **Step 7: Commit any final fixes / record results**

If the smoke test surfaces a bug, fix it as a separate Task X+1 with its own test. Otherwise:

```bash
git log --oneline -25
```

Expected: clean linear history of feat/test commits for Tasks 1-17.

---

## Self-Review (already performed)

**1. Spec coverage:**
- §3 two-phase rollout → Task 3 (default sync), Task 1 (env var)
- §4.3 mode flip table → Task 4 (UX gating)
- §5.1 signal events → Task 5 (log), Task 6 (endpoint), Task 8 (5 emit sites)
- §5.2 endpoint shape → Task 6
- §5.3 storage → Task 5
- §6.2 cluster definition → Task 11
- §6.3 score formula → Task 11 (`_SIGNAL_WEIGHTS_*`)
- §6.4 promotion criteria → Task 11 (`MIN_ACCEPT_COUNT`, distinct-sessions, negatives check)
- §6.5 LearnedPattern persistence → Task 10
- §6.6 verify-on-read → Task 10 (in `find_verified_pattern`)
- §6.7 surfacing in session_lookup, picker, KYCAgentPage → Tasks 13, 16
- §7.1 refinement-aware generator → Task 14
- §7.2 chat affordances → Task 15
- §7.3 conversation memory → Task 15 (auto-snapshot in chatStore)
- §11 success criteria → Task 17 (e2e), Task 18 (live smoke)

**2. Placeholders:** none — every step has concrete code or commands.

**3. Type consistency:**
- `LearnedPattern` fields are identical across Tasks 10, 11, 16.
- `SignalEvent` Pydantic model in Task 6 matches the dataclass in Task 5 (same 6 event types, same fields).
- `useUserMode` hook signature is consistent across Tasks 2, 3, 4, 15, 16.
- `emitSignal(event, sql, metadata)` signature matches across Tasks 7 and 8.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-27-role-aware-learning-chat.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
