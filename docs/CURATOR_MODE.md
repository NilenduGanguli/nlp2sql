# KnowledgeQL — Curator Mode User Guide

This guide explains how to use **Curator Mode** in the KnowledgeQL UI to train the system, save patterns, edit prompts, and keep the knowledge base accurate over time.

> **Audience:** Domain experts (data analysts, KYC/compliance leads, DBAs) who validate generated SQL and feed corrections back into the system. Consumer users only need to read [§1](#1-modes-at-a-glance) and [§3](#3-the-chat-tab).

---

## Table of Contents

1. [Modes at a glance](#1-modes-at-a-glance)
2. [Switching to Curator Mode](#2-switching-to-curator-mode)
3. [The Chat tab — accept, reject, refine](#3-the-chat-tab)
4. [Promoting a query manually](#4-promoting-a-query-manually)
5. [Signals — what gets logged automatically](#5-signals--what-gets-logged-automatically)
6. [The Investigate tab — see what the system learned](#6-the-investigate-tab)
7. [Prompt Studio — edit the agent's brain](#7-prompt-studio)
8. [Regenerating the business knowledge file](#8-regenerating-the-business-knowledge-file)
9. [Rebuilding the graph vs. rebuilding the pipeline](#9-rebuild-graph-vs-rebuild-pipeline)
10. [Where curator data lives on disk](#10-where-curator-data-lives-on-disk)
11. [Curator workflow cheat-sheet](#11-curator-workflow-cheat-sheet)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Modes at a glance

KnowledgeQL has two user modes. The mode is a **client-side preference**, persisted in your browser's `localStorage` under `nlp2sql.userMode`. Most actions also send the mode to the backend so curator-only endpoints can enforce it.

| Capability | Consumer | Curator |
| --- | --- | --- |
| Ask questions in Chat | ✅ | ✅ |
| Run generated SQL | ✅ | ✅ |
| Browse Schema / Graph / Relationships tabs | ✅ | ✅ |
| Edit SQL in the editor | ✅ | ✅ |
| Resume past sessions in History | ✅ | ✅ |
| 👍 Accept / 👎 Reject candidates | ❌ | ✅ |
| Multi-select candidates with checkboxes | ❌ | ✅ |
| ★ Save as pattern (manual promotion) | ❌ | ✅ |
| Investigate tab | ❌ | ✅ |
| Prompt Studio editing & version restore | read-only | ✅ |
| Force Rebuild Graph | ❌ | ✅ |
| Rebuild Pipeline | ❌ | ✅ |
| Regenerate business knowledge file | ❌ | ✅ |

**Server-side enforcement.** Even if the UI is bypassed, curator-only endpoints (e.g. `POST /api/patterns/manual-promote`) return **HTTP 403** when the request body's `mode` field is anything other than `curator`. See [backend/routers/query.py](../backend/routers/query.py).

---

## 2. Switching to Curator Mode

The mode toggle lives in the **top-right corner of the navigation bar**, between the page title and the tab strip. It's a single pill-shaped button that flips between two labels:

- **🛠 Curator** — purple background (`#7c6af7`)
- **👤 Consumer** — green background (`#10b981`)

Click the pill to toggle. The change is instant — no reload required. The backend default for first-time visitors is set by the `DEFAULT_USER_MODE` env var (defaults to `curator`).

> **Tip:** When you switch into Curator Mode, the "Investigate" tab appears in the tab bar and the chat panel grows additional buttons. Switching back hides them again — your in-flight work is not lost.

---

## 3. The Chat tab

The Chat tab is the primary curation surface. You ask a question in plain English, the agent produces one or more SQL candidates, and you decide what to do with them.

### 3.1 Asking a question

Type your question in the input at the bottom of the chat panel and press Enter (or click **Send**). The streaming indicator shows what the agent is doing in real time:

```
enriching query…
classifying intent…
extracting entities (tool call: search_schema "customer")…
extracting entities (tool call: get_table_detail KYC.CUSTOMERS)…
generating SQL…
```

Curators see every internal step; consumers see a simplified spinner.

### 3.2 Single-candidate flow

When the agent is confident in one interpretation, you'll see an **SQL Preview Card** containing:

- The generated SQL (Monaco editor, read-only)
- A short natural-language explanation
- The list of tables the SQL touches
- **Curator-only** — an "Accept generated query?" footer with two buttons:
  - 👍 **Accept** — confirms the SQL is correct for the question. The pair (question → SQL) is added to the knowledge store as an *accepted session entry*. After ≥3 accepts of the same shape, it is promoted to a **VerifiedPattern**.
  - 👎 **Reject** — flags this generation as wrong. Adds a negative signal weighted against any pattern of the same shape. Patterns whose negative signals exceed `accept_count / 2` are blocked from future promotion.
- A **Run** button — executes the SQL against Oracle and shows results in a grid below.

### 3.3 Multi-candidate flow (clarification)

When the agent decides the question is ambiguous, it returns **2–6 candidates**. Each candidate is a complete `{question understanding, SQL, explanation}` triple. The card layout:

- One row per candidate
- **Curator-only** — checkbox on the left of every row; you can tick *more than one*
- A radio button to choose which candidate to actually run
- **Curator** button label: **Accept Selected (N) & Run** — the radio's choice runs, and *all checked* candidates are accepted into the knowledge store
- **Consumer** button label: just **Run** — only the radio choice executes, no learning happens

> **Why multi-accept matters.** Many KYC questions have several legitimate readings ("active customers" → KYC_STATUS='ACTIVE' vs. last_login within 30 days). Accepting both teaches the agent that either interpretation is acceptable; the next user gets the same multi-candidate prompt with both already pre-checked.

### 3.4 The ★ Save as pattern button

After a query has executed and the result grid is visible, the **Refine Bar** appears under the grid. It contains the editable user input, a *re-run* button, and (curator-only) a **★ Save as pattern** button.

Clicking **★ Save as pattern** force-promotes the *current* SQL — the one you just ran, including any edits — into the verified pattern store, **bypassing the ≥3-accept threshold**. After it saves, the button turns green and reads **★ Saved**. Use this when you've manually crafted SQL that you want the agent to remember verbatim.

---

## 4. Promoting a query manually

There are three ways a SQL pattern enters the verified pattern store:

| Mechanism | Trigger | Threshold | Where |
| --- | --- | --- | --- |
| **Auto-promotion** | Curator clicks 👍 on a generated SQL | ≥3 distinct accepts AND negative_signals < accept_count/2 | knowledge store, `patterns` array |
| **Manual promotion** | Curator clicks ★ Save as pattern | None — single click promotes | knowledge store, `manual_promotion=True` flag |
| **API direct** | `POST /api/patterns/manual-promote` | Server requires `mode: "curator"` (HTTP 403 otherwise) | Same store, server-validated |

The manual-promote API request body:

```json
{
  "sql": "SELECT * FROM KYC.CUSTOMERS WHERE KYC_STATUS = 'ACTIVE'",
  "user_input": "all active customers",
  "tables_used": ["KYC.CUSTOMERS"],
  "mode": "curator"
}
```

If `mode` is omitted or set to anything other than `"curator"`, the endpoint returns **403 Forbidden** with `{"detail": "manual-promote requires curator mode"}`.

---

## 5. Signals — what gets logged automatically

Every curator action below is logged as a JSONL signal under `$GRAPH_CACHE_PATH/signals/`. You don't need to do anything — these fire automatically as you work and feed pattern confidence scoring.

| Signal | When it fires |
| --- | --- |
| `copied_sql` | You hit the Copy button on an SQL preview |
| `opened_in_editor` | You click "Open in Editor" on a result |
| `ran_unchanged` | The generated SQL ran without any edits |
| `edited_then_ran` | You modified the SQL in the editor before running |
| `abandoned_session` | You leave the chat without running anything |
| `zero_rows_retry` | You re-ran after a 0-row result |

A pattern's confidence score is `accept_count / (accept_count + negative_signals)`, weighted by the recency and consistency of these signals. `edited_then_ran` is a strong negative — it means the agent's first attempt was wrong.

---

## 6. The Investigate tab

**Curator-only** — appears in the tab strip only when you're in Curator Mode.

The Investigate tab shows the system's *learned state*:

- **Pattern store summary** — total verified patterns, total session entries, top tables by accept count
- **Verified patterns table** — every promoted pattern with its question template, SQL template, accept count, last-seen timestamp, manual-promotion flag
- **Recent sessions** — last N accepted query sessions (question, SQL, table list, timestamp)
- **Pipeline trace viewer** — for each query you can expand every node call (enricher → classifier → extractor → SQL generator) and see the full rendered prompt sent to the LLM. **This view is read-only**: editing happens in Prompt Studio.

> **Use Investigate when:** the agent is generating bad SQL and you want to see *why* — Investigate shows the exact prompt the LLM saw, which entities were resolved, and which schema was retrieved. Often the fix is a prompt edit, not a code change.

---

## 7. Prompt Studio

Tab 8 in the navigation bar. Two view modes selectable from the left panel:

### 7.1 Prompts mode

Lists all editable prompts. Click a name on the left to open it in the full-height monaco editor on the right. Each prompt has a one-line description above the editor.

| Prompt name | Controls |
| --- | --- |
| `query_enricher_system` | How sparse user queries get expanded with KYC domain hints |
| `query_enricher_human` | The user-message template for enrichment |
| `intent_classifier_system` | Classifies queries as DATA_QUERY / SCHEMA_EXPLORE / RESULT_FOLLOWUP / etc. |
| `entity_extractor_system` | The agentic loop's instructions and tool schema |
| `clarification_agent_system` | Decides whether to ask the user a clarifying question |
| `clarification_agent_human` | Template for the clarification request |
| `sql_generator_system` | Main SQL generation rules + Oracle-specific constraints |
| `sql_presenter_system` | How the SQL is presented for confirmation in the chat |
| `kyc_business_agent_system` | KYC-specific clarification resolver |

**Editing flow:**

1. Click the prompt name in the left panel.
2. Edit in the monaco editor.
3. Click **Save** (top-right of editor). The save is atomic (`*.tmp` + `os.replace`), and a timestamped copy is written to the version-history directory.
4. Click **⚡ Rebuild Pipeline** (bottom-left footer) — the running pipeline reloads all prompts without touching the graph or Oracle. Takes ~1 second.
5. Test the change by running a query in Chat.

**Version history.** Below the editor is a collapsible **Version History** panel listing up to 30 prior saves with a 200-character preview. Each row has a **Restore** button — click it to load that version *and* trigger an automatic pipeline rebuild. Useful when an edit makes things worse and you want to revert quickly.

> **Caveat — version history requires `PROMPTS_PERSIST_PATH`.** If the env var is unset, history is empty after every container restart. Set it to a host-mounted directory (defaults to `$GRAPH_CACHE_PATH/prompts`).

### 7.2 Agent Behavior mode

Read-only inspector. Shows:

- The pipeline DAG: every node + every conditional edge (e.g. `check_clarification → END if need_clarification else generate_sql`)
- The entity extractor's tool list (`search_schema`, `get_table_detail`, `find_join_path`, `resolve_business_term`, `list_related_tables`, `query_oracle`, `submit_entities`)
- Tuning constants: `MAX_TOOL_CALLS`, `oracle_max_rows`

Use this when you need to explain to a colleague *what* the agent does without showing them the prompts.

---

## 8. Regenerating the business knowledge file

`kyc_business_knowledge.txt` is a 1–2 page LLM-written briefing of the most important tables, their purposes, key terms, and common joins. It is read by the **query enricher** node before every question to give the agent domain context.

### When to regenerate

- **First run** — the file is empty by default; the system regenerates it automatically on first startup if an LLM is configured.
- **After a schema change** — new tables, renamed columns, new FK relationships.
- **After tweaking prompts** — if you want the briefing in a different style or tone.

### How to regenerate

1. Open the Sidebar (left rail).
2. Scroll to **Knowledge File**.
3. Click **Regenerate Knowledge**.
4. Wait — the operation runs asynchronously; the UI shows a spinner. The button greys out during regeneration. Typical duration: 30–90 seconds depending on schema size and LLM speed.
5. When the spinner clears, the new content appears in the preview pane.

Behind the scenes:
- Top ~30 tables (by `importance_rank` × FK degree) are selected.
- They are batched 10/LLM call (so 3 calls + 1 for common patterns).
- Output is written atomically (`*.tmp` → `os.replace`) to the path given by `KYC_KNOWLEDGE_FILE` env var (defaults to project root `kyc_business_knowledge.txt`).
- The query enricher's cache is cleared and the pipeline rebuilt.

> **Heads-up — this requires LLM credentials.** If the backend has no `LLM_PROVIDER` configured, the endpoint returns `503 Service Unavailable`.

---

## 9. Rebuild Graph vs Rebuild Pipeline

Two distinct buttons, both in the Sidebar. They do **very** different things.

|  | Rebuild Graph | Rebuild Pipeline |
| --- | --- | --- |
| Runs Oracle introspection | ✅ | ❌ |
| Re-extracts tables, columns, FKs | ✅ | ❌ |
| Re-runs LLM importance ranking | ✅ (if LLM configured) | ❌ |
| Reloads all prompt files from disk | ✅ | ✅ |
| Rebuilds LangGraph DAG | ✅ | ✅ |
| Wipes graph cache file | ✅ | ❌ |
| Typical duration | 30–120 s | < 1 s |
| When to use | Schema deployed, new tables added, FK constraints changed | You edited a prompt, you restored an older version |

**Endpoints:**
- `POST /api/admin/rebuild` — graph rebuild (async; track via `GET /api/health` → `graph_loaded` flag)
- `POST /api/admin/rebuild-pipeline` — pipeline rebuild (synchronous, fast)

> **Common mistake.** Editing a prompt and clicking *Rebuild Graph* — that's a 2-minute Oracle round-trip when **Rebuild Pipeline** would have done what you wanted in 1 second.

---

## 10. Where curator data lives on disk

All curator-curated state is persisted to the directory pointed to by `GRAPH_CACHE_PATH`. In Docker that's the named volume `graph_cache_data` mounted at `/data/graph_cache`. Locally it defaults to `~/.cache/knowledgeql/`.

```
$GRAPH_CACHE_PATH/
├── graph_<hash>.pkl              # Serialized KnowledgeGraph snapshot (schema + LLM enrichment)
├── kyc_knowledge_store.json      # Pattern store: verified + learned patterns + sessions
├── prompts/                      # Persisted copies of every prompt edit
│   ├── query_enricher_system.txt
│   ├── sql_generator_system.txt
│   ├── …
│   └── history/
│       ├── query_enricher_system/
│       │   ├── 20260429T142753Z.txt
│       │   └── 20260429T150812Z.txt
│       └── sql_generator_system/…
└── signals/
    └── signals-<session_id>.jsonl   # Append-only signal log per session
```

The business knowledge file (`kyc_business_knowledge.txt`) lives at the project root by default; override with `KYC_KNOWLEDGE_FILE`.

**Key env vars curators may need to know:**

| Variable | Purpose | Default |
| --- | --- | --- |
| `GRAPH_CACHE_PATH` | Root directory for all curator state | `/data/graph_cache` (Docker) / `~/.cache/knowledgeql` |
| `PROMPTS_PERSIST_PATH` | Override prompt history dir | `$GRAPH_CACHE_PATH/prompts` |
| `KYC_KNOWLEDGE_FILE` | Path to business knowledge file | `kyc_business_knowledge.txt` (project root) |
| `DEFAULT_USER_MODE` | First-visit mode for new sessions | `curator` |
| `GRAPH_CACHE_VERSION` | Bump to invalidate cache on next start | `1` |
| `QUERY_ENRICHER_ENABLED` | Disable the query enricher node | `true` |

---

## 11. Curator workflow cheat-sheet

A typical curator session, end-to-end:

```
1. Switch to Curator (top-right pill)
2. Ask a question in Chat
3. Look at the SQL the agent produced
   ├─ Looks right?            → Click 👍 Accept (single) or check ✓ + Accept Selected (multi)
   ├─ Wrong but fixable?      → Edit in SQL Editor → Run → ★ Save as pattern
   ├─ Completely wrong?       → Click 👎 Reject + diagnose in Investigate tab
4. Did the agent ask a clarification?
   └─ Pick the radio answer + tick every other interpretation that's also valid
5. After 3+ accepts of similar shape: pattern auto-promotes (visible in Investigate)
6. Periodic maintenance:
   ├─ Schema changed → Sidebar → Rebuild Graph
   ├─ Prompt tweaked → Prompt Studio → Save → Rebuild Pipeline
   └─ Domain hints stale → Sidebar → Regenerate Knowledge
```

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Accept/Reject buttons missing | Mode is Consumer | Click the top-right pill to switch |
| 403 on `/api/patterns/manual-promote` | Request body's `mode` ≠ `"curator"` | Send `"mode": "curator"` |
| Investigate tab not visible | Mode is Consumer | Switch to Curator |
| Edited prompt has no effect | Pipeline not rebuilt after save | Click ⚡ Rebuild Pipeline |
| Version History panel empty | `PROMPTS_PERSIST_PATH` unset OR persistent dir missing | Set the env var to a host-mounted path |
| Regenerate Knowledge returns 503 | No LLM provider configured | Set `LLM_PROVIDER` + credentials in `.env` |
| Pattern not promoting after many accepts | `negative_signals ≥ accept_count / 2` | Inspect Investigate; remove rejects; or use ★ Save as pattern to force-promote |
| ORA-00942 during Rebuild Graph | Service user lacks DBA grants | Auto-fallback already kicks in (DBA→ALL); confirm in backend logs — `Falling back to ALL_*` |
| "crypto.randomUUID is not a function" — blank UI | App served over plain HTTP from non-localhost host | Polyfill is shipped in `index.html`; rebuild backend image to pick it up |

---

## See also

- [TECHNICAL_ARCHITECTURE.md](./TECHNICAL_ARCHITECTURE.md) — overall system design
- [KNOWLEDGE_GRAPH.md](./KNOWLEDGE_GRAPH.md) — how the graph is built and queried
- [GRAPH_SCHEMA_REFERENCE.md](./GRAPH_SCHEMA_REFERENCE.md) — node and relationship types
- [oracle_to_graph_pipeline.md](./oracle_to_graph_pipeline.md) — extraction internals
