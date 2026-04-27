# Comprehensive Session Learning for the KYC Business Agent

**Date:** 2026-04-19
**Author:** Nilendu Ganguli (with Claude)
**Status:** Draft — pending implementation plan

---

## 1. Goal

Make the KYC Business Agent learn comprehensively from each accepted query interaction, so that:

1. The captured knowledge is a **single rich document** describing the entire reasoning chain — not just `(user_input, sql)` — including every clarification, every search/tool call the agent made, the schema it explored, validation retries, the executed result shape, and any rejected candidates with reasons they were not chosen.
2. When the **same or similar query** is asked again, the system reuses the captured session and asks **zero clarification questions** — surfacing the saved candidates immediately.
3. When a question admits **multiple reasonable interpretations**, the SQL generator emits up to **5 candidate queries**. The user can multi-select which are valid; the union is recorded as one comprehensive learned entry, and one selected candidate is executed.

This builds directly on the existing `KYCKnowledgeStore`, the `kyc_business_agent` clarification interceptor, and the existing single-candidate ambiguity flow in `agent/nodes/sql_generator.py`.

## 2. Non-Goals

- No change to the in-memory knowledge graph build or to Oracle metadata extraction.
- No change to LLM provider plumbing or prompt loading mechanism.
- No new external services, databases, or queues. Persistence stays in the existing on-disk JSON knowledge store.
- No automatic execution of multiple candidates simultaneously. Execution remains user-driven, one candidate at a time.
- No change to retired `learned_patterns` (Jaccard-keyed clarification patterns) — they continue to function alongside the new entries.

## 3. Architecture

### 3.1 Component Map

| # | File | Change |
|---|---|---|
| 1 | `agent/session_digest.py` (new) | Pure function `build_session_digest(state, trace) -> dict`. No I/O. Returns the structured digest used by the LLM analyzer and persisted in metadata. |
| 2 | `agent/nodes/sql_generator.py` | Tighten the system prompt's ambiguity rules. When the question admits multiple reasonable readings (different join paths, different aggregations, different filter scopes, different fact tables), always emit a `\`\`\`ambiguity\`\`\`` block with up to 5 interpretations. Raise `_parse_ambiguity_block` cap from 4 to 5. |
| 3 | `agent/llm_knowledge_analyzer.py` | Add `analyze_accepted_session(llm, digest) -> KnowledgeEntry`. Returns **one** rich entry (`source="query_session"`, `category="query_session"`). Old `analyze_accepted_query` is kept as a thin compatibility shim that delegates to the new function. |
| 4 | `agent/knowledge_store.py` | Add `find_session_match(enriched_query, graph) -> Optional[KnowledgeEntry]`. Jaccard ≥ 0.65 over `metadata["original_query"] + " " + metadata["enriched_query"]`, restricted to `category="query_session"` entries; rejects matches whose `metadata["tables_used"]` references tables no longer present in `graph`. |
| 5 | `agent/nodes/session_lookup.py` (new) | LangGraph node placed after `retrieve_schema`, before `check_clarification`. On match: load saved candidates into state, set `has_candidates=True`, route to `present_sql`. On miss: pass through. |
| 6 | `agent/pipeline.py` | Wire `session_lookup` into both LangGraph and sequential fallback. Add conditional edge from `session_lookup`: `"matched" → present_sql`, `"miss" → check_clarification`. Skip when `intent == "RESULT_FOLLOWUP"` or `conversation_history` is non-empty. |
| 7 | `backend/routers/query.py` | Extend `accept-query` body to accept `accepted_candidates`, `rejected_candidates`, `executed_candidate_id`, `session_digest`. Spawn background `analyze_accepted_session`. Emit a new SSE event `session_match` from the streaming endpoint when `session_lookup` short-circuits. |
| 8 | `prompts/session_analyzer_system.txt` (new) | Prompt for `analyze_accepted_session`. |
| 9 | `frontend/src/components/SqlCandidatesPicker.tsx` | Multi-select: checkbox per candidate + a single radio for "execute now". New "Accept Selected" button posts the chosen subset. |
| 10 | `frontend/src/api/query.ts` | Update `acceptGeneratedQuery` signature to include `accepted_candidates`, `rejected_candidates`, `executed_candidate_id`, `session_digest`. Add `session_match` SSE event handler. |
| 11 | `frontend/src/store/chatStore.ts` | Track per-message `acceptedCandidates`, `rejectedCandidates`, and accumulate `sessionDigest` from streamed trace events. |
| 12 | `frontend/src/pages/InvestigatePage.tsx` | New trace panels: `session_lookup` step (match score + entry id) and a collapsible `session_digest` JSON view. |
| 13 | `frontend/src/pages/KYCAgentPage.tsx` | Filter chip for `source="query_session"`. Detail-view renders metadata fields (clarifications, accepted/rejected candidates, tables used). New "Re-run query" button on `query_session` entries that pre-fills the Chat tab with `metadata.original_query`. Metrics card adds "Query Sessions" counter. |
| 14 | `frontend/src/pages/ChatPage.tsx` (or wherever the chat header renders) | Show a "♻ Reused from session" badge when a `session_match` event fires. |

### 3.2 Pipeline Flow

```
enrich_query → classify_intent → extract_entities → retrieve_schema
                                                      │
                                                      ▼
                                              session_lookup (NEW)
                                                  │      │
                                            (match)    (miss / follow-up / mid-thread)
                                                  │      │
                                                  ▼      ▼
                                          present_sql    check_clarification → ...
```

Short-circuit conditions (all must hold):
- `intent != "RESULT_FOLLOWUP"`
- `len(conversation_history) == 0`
- `find_session_match(enriched_query, graph)` returns a non-None entry
- All `metadata["tables_used"]` for that entry exist in the current graph

When all hold: emit `session_match` SSE event, copy `metadata["accepted_candidates"]` into `state["sql_candidates"]`, set `state["has_candidates"] = True`, route to `present_sql`. The user sees the candidate picker immediately, with the "♻ Reused from session" badge.

### 3.3 Data Shapes

**`SessionDigest` (in-memory only — built client-side from streamed trace, sent on accept):**

```python
{
  "session_id": "<uuid>",
  "original_query": "show me high-risk customers",
  "enriched_query": "show me customers with risk_rating='HIGH' or pep_flag='Y'",
  "intent": "DATA_QUERY",
  "entities": {"tables": [...], "columns": [...], "conditions": [...]},
  "clarifications": [
    {"question": "...", "answer": "...", "auto_answered_by_kyc_agent": false}
  ],
  "tool_calls": [
    {"tool": "search_schema",  "args": {...}, "result_summary": "<= 200 chars"},
    {"tool": "find_join_path", "args": {...}, "result_summary": "<= 200 chars"},
    {"tool": "query_oracle",   "args": {...}, "result_summary": "<= 200 chars"}
    // capped at 30 calls
  ],
  "schema_context_tables": ["KYC.CUSTOMERS", "KYC.RISK_SCORES"],
  "candidates": [
    {"id": "a1b2", "interpretation": "scope to active customers only",
     "sql": "...", "explanation": "...", "accepted": true,  "executed": true},
    {"id": "c3d4", "interpretation": "include historical customers",
     "sql": "...", "explanation": "...", "accepted": true,  "executed": false},
    {"id": "e5f6", "interpretation": "use risk_rating only, ignore pep",
     "sql": "...", "explanation": "...", "accepted": false,
     "rejection_reason": "user wants both flags"}
  ],
  "validation_retries": 0,
  "result_shape": {"columns": ["CUSTOMER_ID", "FULL_NAME", "RISK_RATING"], "row_count": 47},
  "created_at": 1734567890.0
}
```

**`KnowledgeEntry` (persisted, source=`query_session`, category=`query_session`):**

```python
KnowledgeEntry(
  id=<sha1[:16]>,
  source="query_session",
  category="query_session",
  content=<LLM-written prose, ~500-1500 words>,
  metadata={
    "session_id": "...",
    "original_query": "...",
    "enriched_query": "...",
    "accepted_candidates": [
      {"interpretation": "...", "sql": "...", "explanation": "..."}
    ],
    "rejected_candidates": [
      {"interpretation": "...", "sql": "...", "rejection_reason": "..."}
    ],
    "clarifications": [{"q": "...", "a": "..."}],
    "tables_used": ["KYC.CUSTOMERS", "KYC.RISK_SCORES"],
    "tool_calls_summary": [
      {"tool": "...", "args_brief": "...", "result_summary": "..."}
    ],
    "result_shape": {"columns": [...], "row_count": N},
    "created_at": 1734567890.0
  }
)
```

The `content` field is the LLM-generated comprehensive paragraph. The system prompt for `analyze_accepted_session` instructs the LLM to write prose covering, in order:

1. What the user was asking
2. What clarifications were resolved and how
3. What searches the agent performed and what it discovered
4. Which tables and joins were chosen and why
5. The N accepted SQL variants with the condition under which each applies
6. What alternatives were rejected and why

This is the document the `kyc_business_agent` retrieves when answering future clarifications on similar queries.

### 3.4 Multi-Select Acceptance UX

`SqlCandidatesPicker` becomes a two-axis control:

- **Checkbox** per candidate — "this is a valid interpretation, learn it."
- **Single radio button** across all candidates — "execute this one now."
- "Accept Selected" button: enabled when at least one checkbox is set AND a radio is chosen.

On click, the frontend posts to `/api/query/accept-query` with the full candidate set partitioned into `accepted_candidates` (checked) and `rejected_candidates` (unchecked, with optional `rejection_reason` from a small popover the user may fill in), the `executed_candidate_id`, and the accumulated `session_digest`.

The radio-selected SQL is then executed via the existing `executeCandidateSql` flow. The accept-query call is fire-and-forget for learning; the user's chat experience does not block on it.

### 3.5 Backend Route Changes

`POST /api/query/accept-query` body shape (Pydantic):

```python
class AcceptedCandidate(BaseModel):
    id: str
    sql: str
    explanation: str
    interpretation: str

class RejectedCandidate(AcceptedCandidate):
    rejection_reason: str = ""

class _AcceptQueryRequest(BaseModel):
    user_input: str
    accepted_candidates: List[AcceptedCandidate]
    rejected_candidates: List[RejectedCandidate] = []
    executed_candidate_id: Optional[str] = None
    session_digest: Dict[str, Any] = {}
    conversation_history: List[ConversationMessage] = []
    accepted: bool = True   # retained for backward compat
```

Existing per-clarification `LearnedPattern` recording is preserved. The new background task runs `analyze_accepted_session` and writes a single `KnowledgeEntry` via `KYCKnowledgeStore.add_manual_entry` (with the new source/category).

### 3.6 SSE Event Additions

| Event | Emitted by | Payload |
|---|---|---|
| `session_match` | `query_router._stream_query` when `session_lookup` short-circuits | `{matched_entry_id, score, candidates: [...], original_query}` |
| `sql_candidates` | unchanged — reused for the short-circuit case | `{candidates: [...]}` |

### 3.7 Prompt Studio Integration

`prompts/session_analyzer_system.txt` is a new prompt file. The existing prompt-loading mechanism (`prompts.py::load_prompt`, `list_prompts`) discovers it automatically — no extra wiring beyond creating the file.

## 4. Error Handling

| Failure | Behavior |
|---|---|
| `build_session_digest` raises | Log warning; the frontend sends an empty digest. Backend records narrow per-clarification `LearnedPattern`s only (today's behavior). |
| `analyze_accepted_session` LLM call fails or returns malformed JSON | Log warning; fall back to per-clarification pattern recording. Background task does not raise to caller. |
| `find_session_match` returns an entry whose tables are missing from the current graph | Silently skip the short-circuit; pipeline runs normally. Trace records a `session_lookup_skipped` step with reason. |
| Saved candidates fail validation when re-run by the user | Existing `validate_sql` → user-visible error path. No special handling. |
| Multi-select with zero candidates checked or no radio selected | Frontend disables the "Accept Selected" button. |
| `session_digest` missing or malformed in accept-query body | Backend logs warning, records narrow patterns only, returns `{status: "accepted_partial"}`. |

## 5. Edge Cases

1. **`RESULT_FOLLOWUP` intent** — short-circuit skipped; clarifications continue normally.
2. **Mid-thread turn** (`conversation_history` non-empty) — short-circuit skipped.
3. **Renamed table** — graph-FQN check excludes; treated as miss. Stale entry remains in store; future "Re-run query" attempt surfaces the regenerated flow.
4. **Multiple `query_session` entries match** — pick the highest Jaccard score; tiebreak by `created_at` (newer wins).
5. **Session digest oversize** — `tool_calls` truncated server-side (max 30, each `result_summary` ≤ 200 chars). Full trace stays in History tab.
6. **User accepts but never executes** — `executed_candidate_id` is `null`; `result_shape` omitted from digest. Entry still persists.
7. **kyc_business_agent already auto-answered some clarifications** — flagged with `auto_answered_by_kyc_agent: true` so the analyzer prompt does not re-derive that resolution.
8. **Pruning** — `query_session` entries are persisted as static manual entries; they are not subject to the `learned_patterns` LRU pruning. If users want to clear them, the existing KYC Agent tab delete control suffices.

## 6. Testing

### Unit
- `tests/test_session_digest.py` — builder produces correct shape from a mocked `_trace` + state. Verifies tool-call truncation and entity passthrough.
- `tests/test_knowledge_store_session.py` — `find_session_match` Jaccard threshold, FQN-existence guard, tiebreak by `created_at`, `category` filter.
- `tests/test_llm_knowledge_analyzer.py` (extend) — `analyze_accepted_session` parses LLM JSON, handles malformed input, produces a `query_session` `KnowledgeEntry`.
- `tests/test_sql_generator.py` (extend) — `_parse_ambiguity_block` accepts up to 5 interpretations.

### Integration
- `tests/test_pipeline_session_lookup.py` — verifies LangGraph routing for: match, miss, `RESULT_FOLLOWUP`, mid-thread.
- `tests/test_e2e_session_learning.py` — round trip: submit query → accept multi-candidate → re-submit similar query → assert `session_match` event fires + clarification skipped + candidates surfaced.

### Frontend
- `SqlCandidatesPicker.test.tsx` — multi-select checkbox state; radio selection; Accept-button enable/disable; payload shape posted to `acceptGeneratedQuery`.
- Manual smoke: KYC Agent shows `query_session` source badge and re-run button; Investigate shows `session_lookup` step + digest panel; Chat shows "♻ Reused from session" badge after re-asking a learned query.

## 7. Migration & Rollout

- No data migration required. Existing `LearnedPattern`s and static entries continue to function.
- The `query_session` entry source is additive. If `analyze_accepted_session` is disabled (e.g. no LLM available), the system degrades gracefully to today's narrow `analyze_accepted_query` path.
- Feature flag (env): `SESSION_LEARNING_ENABLED=true` (default). Disables `session_lookup` short-circuit and the new analyzer when set to `false`. The multi-select UI remains active regardless.

## 8. Open Questions (to resolve during planning)

- The exact word-cap and prose style for the `content` field — settle once we draft `session_analyzer_system.txt`.
- Whether to surface a UI affordance for the user to author a `rejection_reason` per unchecked candidate, or to leave it blank by default. Leaning toward optional popover, defaulting to blank.
