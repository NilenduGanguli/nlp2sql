# Role-Aware Learning Chat — Design Spec

**Date:** 2026-04-27
**Status:** Approved (Section-by-section)
**Builds on:** 2026-04-19 Comprehensive Session Learning (already shipped)

---

## 1. Goal

Evolve the existing nlp2sql chat from a one-shot Q&A into an **enterprise-grade, KYC-specialized conversational SQL assistant** that:

- Serves **two user populations** simultaneously (SQL developers in curator mode, business users in consumer mode) with the **same backend pipeline** but **different UX surfaces**.
- **Learns continuously** — explicit curator accepts are ground truth; implicit business-user signals are logged and aggregated.
- **Supports multi-turn refinement** — follow-up questions modify prior SQL rather than regenerating from scratch.
- Surfaces a **verified-pattern** layer that becomes the trusted training output of curator work and the first-line ranking signal for consumer queries.

## 2. Non-goals (v1)

- No semantic embedding / vector search (Jaccard + SQL skeleton is enough for the dev pilot).
- No automatic prompt-template tuning — the data is exposed; tuning stays manual.
- No auto-execute on verified-pattern matches — SQL is **always** shown; user clicks Run. Confirmed by user.
- No conversation summarization/compaction — won't matter at <20 turns.
- No identity/auth — mode is a per-browser localStorage setting in the dev pilot.
- No cross-session vector memory — Tier 3.

## 3. Two-phase rollout, manual flip

| | Phase 1 (now) | Phase 2 (later) |
|---|---|---|
| Population | SQL developers | Mostly business users |
| Accept UX | Required | Logged only (UI hidden) |
| Default mode | `curator` | `consumer` |
| Trigger to flip | Manual config flag | — |

The system is a **mixed pilot** — both modes can be active simultaneously via a per-browser toggle. There is no auth boundary; this is a development tool.

## 4. Architecture

### 4.1 What stays unchanged

- The LangGraph pipeline (one path, all 12+ nodes).
- The Oracle/graph/prompt subsystems.
- The existing session-learning machinery (`query_session` entries, `accept-query` route, session_lookup node, SSE event types).
- The `KnowledgeGraph`, `KYCKnowledgeStore`, and Investigate panel.

### 4.2 What is added

Three new subsystems on top of the existing pipeline:

```
┌──────────────────────────────────────────────────────────┐
│                     Frontend (React)                     │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────┐    │
│  │ useUserMode│  │  Signal hook │  │  Chat affords. │    │
│  │   (global) │  │ (5 emit pts) │  │  ↻Refine ⤴Br★Sv │    │
│  └─────┬──────┘  └──────┬───────┘  └────────┬───────┘    │
└────────┼────────────────┼──────────────────┼─────────────┘
         │                │                  │
         ▼                ▼                  ▼
┌──────────────────────────────────────────────────────────┐
│                       Backend (FastAPI)                  │
│   POST /api/signals  ─────► SignalLog (signals.jsonl)    │
│                              │                           │
│   POST /api/query/accept ────┴─►  Pattern Aggregator     │
│                              │   (in accept worker)      │
│                              ▼                           │
│                          LearnedPattern (verified)       │
│                              │                           │
│   session_lookup ◄───────────┘                           │
│   sql_generator (refinement-aware diff prompt) ◄─────────┤
└──────────────────────────────────────────────────────────┘
```

### 4.3 Mode flip

A single toggle in the AppShell header. Persisted as `localStorage["nlp2sql.userMode"]`. Backend reads `mode` from request body / signal payload; falls back to `DEFAULT_USER_MODE` env var.

| Component | Curator | Consumer |
|---|---|---|
| `SqlCandidatesPicker` | All N candidates, multi-select | Top 1; "Show {N-1} alternatives" toggle |
| `StreamingIndicator` + trace | Full step list + tool calls | Compact "Thinking… ({step})" |
| `SqlResultCard` accept buttons | Visible, required | Hidden — implicit signals only |
| Investigate tab in sidebar | Visible | Hidden |
| 0-rows result | Empty grid + columns shown | "No results — try broader filter?" auto-followup |
| Verified-pattern badge | Informational | Auto-pins to position 1 (still requires Run) |

Mode is **global** across all tabs in a browser session. To work in both modes simultaneously, open a second browser window.

## 5. Signal Bus

### 5.1 Event types

| Event | Trigger | Weight (curator) | Weight (consumer) |
|---|---|---|---|
| `copied_sql` | "Copy" button on SqlResultCard | 0.3 | 0.03 |
| `opened_in_editor` | "Open in Editor" button | 0.5 | 0.05 |
| `ran_unchanged` | Editor runs SQL with byte-identical text | 1.0 | 0.1 |
| `edited_then_ran` | Editor runs SQL after edits | 0.0 (diagnostic only) | 0.0 |
| `abandoned_session` | New query typed without accept (strict) | -0.5 | -0.05 |
| `zero_rows_retry` | 0-row result followed by similar reformulated query within 60s | -0.7 | -0.07 |

`abandoned_session` uses **strict** detection: fires only on a new-query event after a SQL was shown without explicit accept/reject. Tab close and idle timeouts do NOT fire.

### 5.2 Endpoint

`POST /api/signals`

```json
{
  "event": "ran_unchanged",
  "session_id": "uuid",
  "entry_id": "abc123",
  "mode": "curator",
  "sql_hash": "sha1(sql)",
  "metadata": { "edit_distance": 0, "row_count": 42 }
}
```

`session_id` is generated by the chat store at the start of each query lifecycle (one ID per user input → final outcome). `entry_id` is set when the query was matched against an existing query_session.

### 5.3 Storage

`agent/signal_log.py` with a `SignalLog` class. Append-only JSONL at `${KNOWLEDGE_STORE_PATH}/signals.jsonl`. One JSON line per event. Daily rotation (`signals-YYYY-MM-DD.jsonl`).

### 5.4 Frontend instrumentation

Five hook sites:

1. `SqlResultCard` — copy, open-in-editor, accept, reject
2. `EditorPage` — run (with `edit_distance` against the loaded SQL)
3. `ChatPanel` — strict abandonment detection on new-query submit
4. `MessageList` — zero-rows detection on result render (paired with subsequent query for `zero_rows_retry`)
5. `chatStore.ts` — wraps the others via a single `emitSignal(event, metadata)` helper

### 5.5 Runtime use

**None in v1.** Signals only accumulate as raw data. The Pattern Aggregator (§6) reads them in batch at curator-accept time. This is deliberate: it prevents short-term noisy feedback loops where one impatient user reranks the model.

## 6. Pattern Aggregator + Verified Patterns

### 6.1 Trigger

Runs as a background task **after every curator-accept**. Extends the existing `analyze_accepted_session` worker so we don't add another async hop. Consumer accepts also enqueue but with a debounce (one aggregation per consumer accept per minute).

### 6.2 Cluster definition

For the just-accepted entry, find all prior `query_session` entries where:

- `_jaccard(query_tokens) ≥ 0.5` (lower than runtime match's 0.65 — we want broad clustering)
- AND `tables_used` overlaps ≥1 table
- AND `sql_skeleton(accepted_sql)` matches

`sql_skeleton(sql)` is defined as: SQL with literal values stripped (`'HIGH'` → `?`, `42` → `?`), whitespace normalized, identifiers case-folded. Cheap and good enough; embeddings are Tier 3.

### 6.3 Score

```
weighted_score =
  curator_accepts * 1.0
+ ran_unchanged * 1.0     (consumer)
+ opened_in_editor * 0.5  (consumer)
+ copied_sql * 0.3        (consumer)
- abandoned_session * 0.5
- zero_rows_retry * 0.7
```

### 6.4 Promotion criteria

A cluster becomes a **verified pattern** when:

- `weighted_score ≥ 3`
- AND from ≥2 distinct sessions (no self-vouching)
- AND `negative_signals` not dominating (`negatives < positives / 2`)

Or, manually, when a curator clicks **★ Save as pattern** in the chat (skips threshold).

### 6.5 Storage

Uses the existing `KYCKnowledgeStore.patterns: List[LearnedPattern]` slot — finally populated.

```python
@dataclass
class LearnedPattern:
    pattern_id: str           # vp_<hash>
    sql_skeleton: str
    exemplar_query: str       # canonical phrasing (most recent accepted)
    exemplar_sql: str         # full SQL
    tables_used: List[str]
    accept_count: int
    consumer_uses: int
    negative_signals: int
    score: float
    promoted_at: float
    source_entry_ids: List[str]
    manual_promotion: bool
```

Persisted alongside session entries in the same JSON store.

### 6.6 Staleness handling

**Verify on read.** Every time a pattern is surfaced (session_lookup, candidates ranker, Patterns tab), check that all `tables_used` still exist in the live graph. Patterns failing this check are filtered out of results. Cost is bounded — typical patterns reference 1-3 tables, graph node lookups are O(1).

Column-level skeleton validation deferred to Tier 2 (requires SQL parsing on the hot path).

### 6.7 Where verified patterns surface

1. **`session_lookup` node** — queries verified patterns first. If a verified pattern's `exemplar_query` Jaccard-matches the user input AND tables exist, returns it with metadata `is_verified=true`. Verified-pattern matches take precedence over raw query_session matches when both exist.
2. **`SqlCandidatesPicker`** — green ✓ **"Verified"** badge on candidates whose `sql_skeleton` matches a verified pattern.
3. **Consumer mode** — verified candidate auto-pinned to position 1.
4. **`KYCAgentPage`** — new "Patterns" sub-tab listing verified patterns sorted by score, with click-through to source sessions. This is the curator's training-progress dashboard.

## 7. Conversation Refinement

### 7.1 Refinement-aware SQL generator

When `intent ∈ {RESULT_FOLLOWUP, QUERY_REFINE}` AND `previous_sql_context.sql` is non-empty:

- Generator uses a **diff prompt**: *"Here is the prior SQL: {sql}. The user wants to modify it: {user_input}. Return the modified SQL, preserving structure where possible."*
- Validator expects ≥60% token overlap with prior SQL — if not, falls back to full regeneration (catches misclassified intents).
- Trace records `refinement_mode=true` so the Investigate panel shows it.

### 7.2 Chat affordances on `SqlResultCard`

| Button | Modes | Behavior |
|---|---|---|
| ↻ Refine | both | Prefills input with `"refine: "`, pins prior SQL into `previous_sql_context`, forces RESULT_FOLLOWUP intent. |
| ⤴ Branch | both | Resets thread but keeps current SQL accessible as `"the previous query"`. |
| ★ Save as pattern | curator only | Promotes session to verified pattern immediately, bypassing threshold. Logs `manual_promotion=true`. |

### 7.3 Conversation memory

`chatStore` auto-snapshots the last `sql_ready` event into `previous_sql_context` and includes it in every subsequent `/api/query` POST. Backend already accepts this field — no schema change needed.

## 8. Build order and PR plan

| PR | Component | Files touched |
|---|---|---|
| 1 | Mode toggle | `useUserMode` hook, AppShell header, `DEFAULT_USER_MODE` env, all gated components |
| 2 | Signal Bus | `agent/signal_log.py`, `POST /api/signals`, 5 frontend instrumentation sites, `chatStore` helper |
| 3 | Pattern Aggregator | `KYCKnowledgeStore.aggregate_patterns()`, sql_skeleton helper, integration in accept worker, verify-on-read in session_lookup |
| 4 | Refinement hardening | `agent/nodes/sql_generator.py` diff branch, `chatStore` auto-snapshot, ↻/⤴/★ buttons |
| 5 | Verified-pattern UI | Patterns tab in KYCAgentPage, ✓ badge in SqlCandidatesPicker, consumer-mode auto-pin |

Each PR is independently shippable. Order is topological: PR 4 can ship before PR 5 (refinement doesn't depend on the patterns UI). PR 5 depends on PR 3.

## 9. Out of scope (Tier 2 / 3)

- Vector embeddings for query similarity (replaces Jaccard).
- Column-level pattern staleness detection (requires SQL parsing).
- Per-domain readiness dashboards (per-table verified-pattern count, accuracy).
- Prompt drift detection (when accepted SQL diverges from generated SQL → tune prompt).
- Auto-prompt tuning from accepted patterns.
- Agent-initiated proactive followups beyond the 0-row recovery.
- Multi-user identity, RBAC, audit logs.
- Conversation summarization at long thread length.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Pattern aggregator runs slow as session count grows | Index by `tables_used` first; only score sessions sharing ≥1 table. JSON store is fine to ~10k sessions; revisit when we hit it. |
| Verify-on-read adds latency to session lookup | Bounded by N tables per pattern (typically 1-3); O(1) graph lookups. Acceptable up to ~1000 verified patterns. |
| Strict abandonment misses tab-close abandonment | Acceptable for v1 — accepting noisier negative signals would corrupt training data. Revisit if `weighted_score` distribution shows we need more recall. |
| Mode toggle confuses developers using both | Mode badge in header at all times; disabled tabs are visible-but-greyed not hidden. |
| Manual ★ Save as pattern abused | Logged as `manual_promotion=true`; visible in Patterns tab; dev pilot only. |

## 11. Success criteria

For the dev pilot to declare v1 done:

- Curator runs ≥20 queries, accepts ≥10. Pattern Aggregator produces ≥3 verified patterns.
- Same query asked in consumer mode after acceptance auto-pins the verified candidate.
- Multi-turn refinement: "show me high-risk customers" → "now only from last quarter" produces SQL that reuses the prior query (validator confirms ≥60% token overlap).
- Signal log accumulates ≥50 events across both modes with no errors.
- Mode toggle round-trips through page reload.
