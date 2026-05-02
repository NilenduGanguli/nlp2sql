# Teaching-Knowledge System — Phase 1 (Enriched session entries + RAG retrieval)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every accepted-query knowledge entry richer (LLM-produced description, why_this_sql, key_concepts, tags, anticipated_clarifications, key_filter_values) so it improves *future* matching, AND replace the binary `find_session_match` with graded ranking that short-circuits at high similarity (≥0.75) but injects top-3 accepted examples into the SQL-generator's prompt at moderate similarity (0.30–0.75).

**Architecture:** Extend the existing `KYCKnowledgeStore` / `analyze_accepted_session` / `session_lookup` / `sql_generator` chain. No new tables, no new node, no breaking change to the public API of `KYCKnowledgeStore`.

**Tech Stack:** Python 3.11, existing langchain LLM client, sqlglot (for filter-value extraction), pytest.

---

## Out of Scope (deferred)

- Phase 2 (Teaching tab UX + endpoints) — separate plan
- Phase 3 (Bulk upload) — separate plan
- One-time backfill script for entries created before Phase 1 — graceful fallback covers them

---

## File Structure

| Status | Path | Responsibility |
|---|---|---|
| **MODIFY** | `prompts/session_analyzer_system.txt` | Ask the LLM for the new structured fields |
| **MODIFY** | `agent/llm_knowledge_analyzer.py` | `analyze_accepted_session` returns the enriched entry |
| **MODIFY** | `agent/knowledge_store.py` | Add `rank_accepted_entries(query, top_k, graph)`; mark `find_session_match` deprecated (keep working) |
| **MODIFY** | `agent/state.py` | Add `accepted_examples: List[Dict[str,Any]]` field |
| **MODIFY** | `agent/nodes/session_lookup.py` | Use `rank_accepted_entries`; ≥0.75 short-circuit, 0.30–0.75 inject `accepted_examples` |
| **MODIFY** | `backend/routers/query.py` | Initialise `accepted_examples: []` in pipeline state |
| **MODIFY** | `agent/nodes/sql_generator.py` + `prompts/sql_generator_system.txt` | New rule 20; render `accepted_examples` into the user message |
| **CREATE** | `tests/test_session_analyzer_enriched.py` | analyze_accepted_session produces all new fields |
| **CREATE** | `tests/test_rank_accepted_entries.py` | scoring + top-K ordering |
| **CREATE** | `tests/test_session_lookup_rag.py` | three thresholds: short-circuit, RAG, ignore |
| **CREATE** | `tests/test_sql_generator_rag_examples.py` | accepted_examples reach the prompt |

---

## Conventions

- New `KnowledgeEntry.metadata` keys (all optional for backward compat): `description`, `why_this_sql`, `key_concepts`, `tags`, `anticipated_clarifications`, `key_filter_values`
- `rank_accepted_entries(query, top_k=3, graph)` returns `List[Tuple[KnowledgeEntry, float]]` sorted by descending score
- Score is **max** of:
  - Jaccard on tokens of `(description + why_this_sql + key_concepts + tags + original_query)`
  - Jaccard on tokens of `original_query + enriched_query` (legacy fallback for old entries)
- Conventional commits: `feat(teach): ...`, `test(teach): ...`

---

## Task 1: Enriched session-analyzer prompt + parser

**Files:**
- Modify: `prompts/session_analyzer_system.txt`
- Modify: `agent/llm_knowledge_analyzer.py:345-...` (`analyze_accepted_session`)
- Test: `tests/test_session_analyzer_enriched.py`

- [ ] **Step 1.1: Write the failing test**

```python
"""Tests for enriched analyze_accepted_session output."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agent.llm_knowledge_analyzer import analyze_accepted_session


class _FakeResp:
    def __init__(self, content):
        self.content = content


def _digest():
    return {
        "user_input": "How many active customers per region?",
        "enriched_query": "Count of customers with STATUS='A' grouped by REGION",
        "accepted_candidates": [{
            "interpretation": "active = STATUS='A'",
            "sql": "SELECT REGION, COUNT(*) FROM KYC.CUSTOMERS WHERE STATUS='A' GROUP BY REGION",
            "explanation": "Counts customers with active status by region.",
        }],
        "rejected_candidates": [],
        "clarifications_resolved": [
            {"question": "Active means STATUS='A'?", "answer": "Yes"},
        ],
        "tables_used": ["KYC.CUSTOMERS"],
    }


def test_analyze_accepted_session_produces_all_enriched_fields():
    fake_payload = {
        "title": "Active customers by region",
        "content": "Count of customers with STATUS='A' grouped by REGION.",
        "description": "Counts how many customers are currently active, broken down by region.",
        "why_this_sql": "Filters CUSTOMERS to STATUS='A' (the active code in this DB) and "
                        "groups by REGION; no joins needed because both columns live on CUSTOMERS.",
        "key_concepts": ["active customer", "regional breakdown"],
        "tags": ["customer", "status-filter", "aggregation"],
        "anticipated_clarifications": [
            {"question": "What does 'active' mean?", "answer": "STATUS='A'"},
            {"question": "How is region defined?", "answer": "CUSTOMERS.REGION column"},
        ],
        "key_filter_values": {"STATUS": ["A"]},
    }
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=_FakeResp(json.dumps(fake_payload)))

    entry = analyze_accepted_session(fake_llm, _digest())
    assert entry is not None
    md = entry.metadata
    assert md["description"].startswith("Counts how many")
    assert "STATUS='A'" in md["why_this_sql"]
    assert "active customer" in md["key_concepts"]
    assert "status-filter" in md["tags"]
    assert any(c["answer"] == "STATUS='A'" for c in md["anticipated_clarifications"])
    assert md["key_filter_values"] == {"STATUS": ["A"]}


def test_analyze_accepted_session_handles_partial_llm_output():
    """LLM omits some optional fields → entry still saved, missing fields are []/{}/''.."""
    minimal = {
        "title": "X",
        "content": "Y",
        # description, why_this_sql, etc. absent
    }
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=_FakeResp(json.dumps(minimal)))

    entry = analyze_accepted_session(fake_llm, _digest())
    assert entry is not None
    md = entry.metadata
    assert md.get("description", "") == ""
    assert md.get("key_concepts", []) == []
    assert md.get("anticipated_clarifications", []) == []
    assert md.get("key_filter_values", {}) == {}
```

- [ ] **Step 1.2: Run to verify failure**

```
python3.12 -m pytest tests/test_session_analyzer_enriched.py -v
```
Expected: FAIL — current `analyze_accepted_session` doesn't include the new fields.

- [ ] **Step 1.3: Update the prompt template**

Replace contents of `prompts/session_analyzer_system.txt` with:

```
You are a KYC analyst reviewing a successful natural-language → SQL session.

Produce a SINGLE JSON object that captures the session as reusable knowledge:

{
  "title":  "≤ 8 word summary of the question",
  "content": "1 short paragraph (≤ 80 words) summarising what was asked AND how the SQL answered it",
  "description": "1-3 sentence plain-English description of the question's intent (no SQL terms)",
  "why_this_sql": "2-4 sentences explaining WHY these specific tables/joins/filters/values are correct — "
                  "the reasoning trace a future agent could imitate",
  "key_concepts": ["business concept 1", "business concept 2", ...],
  "tags": ["domain-tag-1", "domain-tag-2", ...],
  "anticipated_clarifications": [
      {"question": "follow-up question a future user might ask", "answer": "concise canonical answer"},
      ...
  ],
  "key_filter_values": {"COLUMN_NAME": ["value1", "value2", ...], ...}
}

Rules:
- key_filter_values: extract ONLY string/numeric literals that appear in the SQL's WHERE / HAVING / IN clauses.
  Do NOT include date arithmetic or sub-queries.
- anticipated_clarifications: 2–5 entries; each captures a clarification a NEW user (without context)
  might need to land on the same SQL. Reuse the actual clarifications resolved during this session
  if they exist. Do NOT include user-preference questions ("which specific status?").
- key_concepts: 2–6 short noun phrases.
- tags: 2–6 short kebab-case tags.
- All fields are required; use empty arrays / objects / strings when there's nothing to say.

Return ONLY the JSON object, no prose, no markdown fences.
```

- [ ] **Step 1.4: Update `analyze_accepted_session` to parse the new fields**

In `agent/llm_knowledge_analyzer.py:345`, change the metadata it builds to pass through the new fields:

```python
def analyze_accepted_session(llm, digest: Dict[str, Any]) -> Optional[KnowledgeEntry]:
    """
    Analyze an accepted session and produce ONE rich KnowledgeEntry.

    The metadata captures (in addition to the existing fields):
      description: str         — plain-English question intent
      why_this_sql: str        — reasoning trace
      key_concepts: List[str]  — business concepts (2-6)
      tags: List[str]          — kebab-case domain tags (2-6)
      anticipated_clarifications: List[{question, answer}]  — Q&A pairs for KYC business agent
      key_filter_values: Dict[str, List[str]]               — column → distinct literals from WHERE
    """
    # ... existing prompt-load + LLM call code ...
    parsed = _parse_llm_json(raw) if raw else None
    if not isinstance(parsed, dict):
        return None
    title = str(parsed.get("title", "")).strip()
    content = str(parsed.get("content", "")).strip()
    if not content:
        return None
    md = digest.get("metadata", {}) or {}
    md.update({
        "original_query": digest.get("user_input", ""),
        "enriched_query": digest.get("enriched_query", ""),
        "tables_used": digest.get("tables_used", []),
        "accepted_candidates": digest.get("accepted_candidates", []),
        "rejected_candidates": digest.get("rejected_candidates", []),
        "clarifications": digest.get("clarifications_resolved", []),
        "created_at": time.time(),
        "title": title,
        # NEW fields (all optional with sensible defaults)
        "description": str(parsed.get("description", "")).strip(),
        "why_this_sql": str(parsed.get("why_this_sql", "")).strip(),
        "key_concepts": [str(c).strip() for c in parsed.get("key_concepts", []) if c],
        "tags": [str(t).strip() for t in parsed.get("tags", []) if t],
        "anticipated_clarifications": [
            {"question": str(c.get("question", "")).strip(),
             "answer":   str(c.get("answer", "")).strip()}
            for c in parsed.get("anticipated_clarifications", [])
            if isinstance(c, dict) and c.get("question") and c.get("answer")
        ],
        "key_filter_values": {
            str(k).upper(): [str(v) for v in (vs if isinstance(vs, list) else [vs])]
            for k, vs in (parsed.get("key_filter_values") or {}).items()
        },
    })
    return KnowledgeEntry(
        id=str(uuid.uuid4())[:16],
        source="query_session",
        category="query_session",
        content=content,
        metadata=md,
    )
```

- [ ] **Step 1.5: Run tests to verify they pass**

```
python3.12 -m pytest tests/test_session_analyzer_enriched.py -v
```
Expected: PASS.

- [ ] **Step 1.6: Commit**

```bash
git add prompts/session_analyzer_system.txt agent/llm_knowledge_analyzer.py tests/test_session_analyzer_enriched.py
git commit -m "feat(teach): enrich query_session entries with description + Q&A + filter values

analyze_accepted_session now produces description, why_this_sql,
key_concepts, tags, anticipated_clarifications, key_filter_values.
All optional with safe defaults so old entries keep working."
```

---

## Task 2: `rank_accepted_entries` on KYCKnowledgeStore

**Files:**
- Modify: `agent/knowledge_store.py` (add new method)
- Test: `tests/test_rank_accepted_entries.py`

- [ ] **Step 2.1: Write the failing test**

```python
"""Tests for KYCKnowledgeStore.rank_accepted_entries — graded ranking, top-K."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry


def _entry(content, **md):
    return KnowledgeEntry(
        id=md.pop("id", f"id_{content[:6]}"),
        source="query_session",
        category="query_session",
        content=content,
        metadata={
            "original_query": md.pop("original_query", ""),
            "enriched_query": md.pop("enriched_query", ""),
            "description": md.pop("description", ""),
            "why_this_sql": md.pop("why_this_sql", ""),
            "key_concepts": md.pop("key_concepts", []),
            "tags": md.pop("tags", []),
            "tables_used": md.pop("tables_used", []),
            "created_at": md.pop("created_at", time.time()),
            **md,
        },
    )


def _store_with(*entries):
    s = KYCKnowledgeStore(persist_path="/tmp/test_rank_" + str(time.time()) + ".json")
    for e in entries:
        s.add_session_entry(e)
    return s


def _graph_with_tables(*table_fqns):
    """Mock graph that says all listed FQNs exist."""
    g = MagicMock()
    g.get_node = lambda label, fqn: {"fqn": fqn} if fqn in table_fqns else None
    return g


def test_rank_returns_top_k_sorted_by_score():
    s = _store_with(
        _entry("active customers by region",
               original_query="active customers by region",
               description="counts of active customers per region",
               key_concepts=["active customer", "region"],
               tables_used=["KYC.CUSTOMERS"]),
        _entry("transactions per account",
               original_query="transactions per account",
               description="counts transactions for each account",
               key_concepts=["transaction", "account"],
               tables_used=["KYC.TRANSACTIONS"]),
        _entry("inactive customers count",
               original_query="how many inactive customers",
               description="counts customers with inactive status",
               key_concepts=["inactive customer"],
               tables_used=["KYC.CUSTOMERS"]),
    )
    g = _graph_with_tables("KYC.CUSTOMERS", "KYC.TRANSACTIONS")
    ranked = s.rank_accepted_entries(
        "show me active customers in each region", top_k=3, graph=g,
    )
    assert len(ranked) == 3
    # First should be the regional one (highest overlap)
    assert "region" in ranked[0][0].content
    # Scores must be in descending order
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_skips_entries_whose_tables_are_missing():
    s = _store_with(
        _entry("active customers",
               original_query="active customers",
               tables_used=["KYC.GONE_TABLE"]),
    )
    g = _graph_with_tables("KYC.CUSTOMERS")  # no GONE_TABLE
    ranked = s.rank_accepted_entries("active customers", top_k=3, graph=g)
    assert ranked == []


def test_rank_falls_back_to_legacy_jaccard_when_description_missing():
    """Entries without the new fields still rank correctly via the legacy path."""
    e = _entry(
        "old style entry",
        original_query="how many active customers per region",
        # no description, no key_concepts
    )
    e.metadata.pop("description", None)
    e.metadata.pop("key_concepts", None)
    s = _store_with(e)
    g = _graph_with_tables("KYC.CUSTOMERS")
    ranked = s.rank_accepted_entries("active customers per region", top_k=3, graph=g)
    assert len(ranked) == 1
    assert ranked[0][1] > 0.0


def test_rank_returns_empty_when_query_too_short():
    s = _store_with(_entry("anything"))
    ranked = s.rank_accepted_entries("a", top_k=3, graph=_graph_with_tables())
    assert ranked == []
```

- [ ] **Step 2.2: Run to verify failure**

```
python3.12 -m pytest tests/test_rank_accepted_entries.py -v
```
Expected: FAIL — `rank_accepted_entries` does not exist.

- [ ] **Step 2.3: Add `rank_accepted_entries` to KYCKnowledgeStore**

In `agent/knowledge_store.py`, after `find_session_match`:

```python
def rank_accepted_entries(
    self,
    query: str,
    top_k: int = 3,
    graph=None,
) -> List[Tuple["KnowledgeEntry", float]]:
    """Return the top-k accepted-query entries by graded similarity.

    Score is the max of two Jaccard scores:
      - tokens of (description + why_this_sql + key_concepts + tags + original_query)
      - tokens of (original_query + enriched_query)  — legacy fallback

    Entries whose `tables_used` aren't all present in *graph* are filtered out
    when graph is provided. Returned list is sorted descending by score.
    """
    if not query or len(query.strip()) < 3:
        return []
    qtoks = _tokenize(query)
    if not qtoks:
        return []
    out: List[Tuple["KnowledgeEntry", float]] = []
    with self._lock:
        for e in self.static_entries:
            if e.source != "query_session" or e.category != "query_session":
                continue
            md = e.metadata or {}
            if graph is not None:
                tables = md.get("tables_used", []) or []
                if tables and not all(graph.get_node("Table", t) for t in tables):
                    continue
            enriched_text = " ".join([
                str(md.get("description", "")),
                str(md.get("why_this_sql", "")),
                " ".join(md.get("key_concepts", []) or []),
                " ".join(md.get("tags", []) or []),
                str(md.get("original_query", "")),
            ]).strip()
            legacy_text = (
                str(md.get("original_query", "")) + " "
                + str(md.get("enriched_query", ""))
            ).strip()
            s_enriched = _jaccard(qtoks, _tokenize(enriched_text)) if enriched_text else 0.0
            s_legacy = _jaccard(qtoks, _tokenize(legacy_text)) if legacy_text else 0.0
            score = max(s_enriched, s_legacy)
            if score > 0:
                out.append((e, score))
    out.sort(key=lambda t: t[1], reverse=True)
    return out[:top_k]
```

- [ ] **Step 2.4: Run tests to verify pass**

```
python3.12 -m pytest tests/test_rank_accepted_entries.py -v
```
Expected: PASS.

- [ ] **Step 2.5: Commit**

```bash
git add agent/knowledge_store.py tests/test_rank_accepted_entries.py
git commit -m "feat(teach): rank_accepted_entries — graded top-K retrieval

Returns up to top_k accepted-query entries scored by Jaccard against
description+why_this_sql+key_concepts+tags+original_query, with
legacy fallback so pre-Phase-1 entries still rank correctly."
```

---

## Task 3: AgentState.accepted_examples + session_lookup uses RAG

**Files:**
- Modify: `agent/state.py` (add field)
- Modify: `agent/nodes/session_lookup.py` (use new method, three-way routing)
- Modify: `backend/routers/query.py` (initialise field)
- Test: `tests/test_session_lookup_rag.py`

- [ ] **Step 3.1: Add field to AgentState**

In `agent/state.py`, append to the TypedDict:

```python
    accepted_examples: List[Dict[str, Any]]
    """
    Phase 1 (teaching-knowledge): up to top_k=3 accepted-query session entries
    whose similarity to the current query is between 0.30 and 0.75.

    The SQL generator injects these into its prompt as strongly-preferred
    examples. Above 0.75 the session_lookup node short-circuits the pipeline
    instead of populating this field.

    Each entry: {
      "score": float,
      "description": str,
      "why_this_sql": str,
      "sql": str,
      "key_concepts": List[str],
      "tags": List[str],
    }
    """
```

- [ ] **Step 3.2: Initialise field in pipeline state**

In `backend/routers/query.py` near the other initialisations (around line 90):

```python
        "accepted_examples": [],
```

- [ ] **Step 3.3: Write the failing test**

```python
"""Tests for session_lookup three-way routing: short-circuit / RAG / ignore."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry
from agent.nodes.session_lookup import make_session_lookup


def _entry(query, score_target=0.5, **md):
    """Build an entry whose `original_query` will roughly produce the target score."""
    return KnowledgeEntry(
        id=md.pop("id", f"e_{query[:6]}"),
        source="query_session",
        category="query_session",
        content=query,
        metadata={
            "original_query": query,
            "enriched_query": "",
            "description": md.pop("description", query),
            "key_concepts": md.pop("key_concepts", []),
            "tables_used": md.pop("tables_used", ["KYC.CUSTOMERS"]),
            "accepted_candidates": [{
                "interpretation": "x",
                "sql": md.pop("sql", "SELECT * FROM KYC.CUSTOMERS"),
                "explanation": md.pop("explanation", ""),
            }],
            "created_at": time.time(),
            **md,
        },
    )


def _graph():
    g = MagicMock()
    g.get_node = lambda label, fqn: {"fqn": fqn}    # all tables exist
    return g


def _state(query):
    return {"user_input": query, "intent": "DATA_QUERY",
            "conversation_history": [], "_trace": []}


def test_short_circuits_at_high_similarity():
    """Score ≥ 0.75 → has_candidates=True, session_match_entry_id set."""
    s = KYCKnowledgeStore(persist_path=f"/tmp/sl_{time.time()}.json")
    s.add_session_entry(_entry("active customers by region today"))
    node = make_session_lookup(s, _graph())
    out = node(_state("active customers by region today"))
    assert out.get("has_candidates") is True
    assert out.get("session_match_entry_id") is not None


def test_rag_injects_examples_at_moderate_similarity():
    """0.30 ≤ score < 0.75 → no short-circuit, accepted_examples populated."""
    s = KYCKnowledgeStore(persist_path=f"/tmp/sl_{time.time()}.json")
    s.add_session_entry(_entry("count customers per region", description="regional customer counts"))
    node = make_session_lookup(s, _graph())
    out = node(_state("how many active customers grouped by their region"))
    # Should NOT short-circuit
    assert not out.get("has_candidates")
    # Should populate accepted_examples
    examples = out.get("accepted_examples", [])
    assert len(examples) >= 1
    assert all("sql" in ex for ex in examples)
    assert all(0.30 <= ex["score"] < 0.75 for ex in examples)


def test_ignores_below_threshold():
    """Score < 0.30 → both has_candidates and accepted_examples are empty."""
    s = KYCKnowledgeStore(persist_path=f"/tmp/sl_{time.time()}.json")
    s.add_session_entry(_entry("transactions and amounts"))
    node = make_session_lookup(s, _graph())
    out = node(_state("active customer onboarding workflow"))
    assert not out.get("has_candidates")
    assert out.get("accepted_examples", []) == []
```

- [ ] **Step 3.4: Run to verify failure**

```
python3.12 -m pytest tests/test_session_lookup_rag.py -v
```
Expected: FAIL — current `session_lookup` doesn't populate `accepted_examples`.

- [ ] **Step 3.5: Update `session_lookup` to use graded ranking**

In `agent/nodes/session_lookup.py`, replace the `session_lookup` function body:

```python
SHORT_CIRCUIT_THRESHOLD = 0.75
RAG_INJECT_MIN = 0.30


def session_lookup(state: Dict[str, Any]) -> Dict[str, Any]:
    _trace = list(state.get("_trace", []))
    trace = TraceStep("session_lookup", "session_lookup")

    if knowledge_store is None or graph is None:
        trace.output_summary = {"action": "skip", "reason": "disabled"}
        _trace.append(trace.finish().to_dict())
        return {**state, "_trace": _trace}

    intent = state.get("intent", "DATA_QUERY")
    history = state.get("conversation_history", []) or []
    if intent == "RESULT_FOLLOWUP" or len(history) > 0:
        trace.output_summary = {"action": "skip",
                                "reason": "followup_or_mid_thread", "intent": intent,
                                "history_len": len(history)}
        _trace.append(trace.finish().to_dict())
        return {**state, "_trace": _trace}

    query = state.get("user_input") or state.get("enriched_query", "")

    # 1. Verified-pattern short-circuit (existing — unchanged)
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
        trace.output_summary = {"action": "match", "match_kind": "verified_pattern",
                                "matched_pattern_id": vp.pattern_id, "candidate_count": 1,
                                "matched_query": (vp.exemplar_query or "")[:80]}
        _trace.append(trace.finish().to_dict())
        return {**state, "sql_candidates": [candidate], "has_candidates": True,
                "session_match_entry_id": vp.pattern_id, "step": "session_matched", "_trace": _trace}

    # 2. Graded session-entry retrieval (NEW)
    try:
        ranked = knowledge_store.rank_accepted_entries(query, top_k=3, graph=graph)
    except Exception as exc:
        logger.warning("rank_accepted_entries failed: %s", exc)
        trace.error = str(exc)
        _trace.append(trace.finish().to_dict())
        return {**state, "_trace": _trace}

    if not ranked:
        trace.output_summary = {"action": "miss", "query_preview": query[:80]}
        _trace.append(trace.finish().to_dict())
        return {**state, "_trace": _trace}

    top_entry, top_score = ranked[0]

    # 2a. Short-circuit at high similarity
    if top_score >= SHORT_CIRCUIT_THRESHOLD:
        accepted = (top_entry.metadata or {}).get("accepted_candidates", []) or []
        candidates = [{
            "id": f"sm{i+1:02d}",
            "interpretation": c.get("interpretation", "Reused interpretation"),
            "sql": c.get("sql", ""),
            "explanation": c.get("explanation", ""),
        } for i, c in enumerate(accepted)]
        if not candidates:   # entry has no SQL — fall through to RAG
            ranked = ranked
        else:
            trace.output_summary = {"action": "match", "match_kind": "query_session",
                                    "matched_entry_id": top_entry.id,
                                    "candidate_count": len(candidates), "score": top_score}
            _trace.append(trace.finish().to_dict())
            return {**state, "sql_candidates": candidates, "has_candidates": True,
                    "session_match_entry_id": top_entry.id, "step": "session_matched",
                    "_trace": _trace}

    # 2b. RAG injection at moderate similarity
    examples = []
    for entry, score in ranked:
        if score < RAG_INJECT_MIN or score >= SHORT_CIRCUIT_THRESHOLD:
            continue
        md = entry.metadata or {}
        accepted = md.get("accepted_candidates", []) or []
        sql = accepted[0]["sql"] if accepted else ""
        examples.append({
            "score": round(score, 2),
            "description": md.get("description", "") or md.get("original_query", ""),
            "why_this_sql": md.get("why_this_sql", ""),
            "sql": sql,
            "key_concepts": md.get("key_concepts", []),
            "tags": md.get("tags", []),
        })

    if examples:
        trace.output_summary = {"action": "rag_inject", "example_count": len(examples),
                                "top_score": top_score}
        _trace.append(trace.finish().to_dict())
        return {**state, "accepted_examples": examples, "_trace": _trace}

    trace.output_summary = {"action": "below_threshold", "top_score": top_score}
    _trace.append(trace.finish().to_dict())
    return {**state, "_trace": _trace}
```

- [ ] **Step 3.6: Run tests to verify pass**

```
python3.12 -m pytest tests/test_session_lookup_rag.py tests/test_session_lookup_node.py -v
```
Expected: PASS.

- [ ] **Step 3.7: Commit**

```bash
git add agent/state.py agent/nodes/session_lookup.py backend/routers/query.py tests/test_session_lookup_rag.py
git commit -m "feat(teach): session_lookup uses graded RAG retrieval

Three-way routing on top-1 score:
  - >= 0.75: short-circuit pipeline (existing behaviour, raised threshold)
  - 0.30..0.75: inject top-3 entries into state.accepted_examples
  - < 0.30: ignore"
```

---

## Task 4: SQL generator consumes accepted_examples

**Files:**
- Modify: `prompts/sql_generator_system.txt` (rule 20)
- Modify: `agent/nodes/sql_generator.py` (render examples in user message + fallback prompt)
- Test: `tests/test_sql_generator_rag_examples.py`

- [ ] **Step 4.1: Write the failing test**

```python
"""Tests that accepted_examples reach the SQL generator's user message."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.nodes.sql_generator import make_sql_generator


class _FakeResp:
    def __init__(self, content):
        self.content = content


def test_accepted_examples_appear_in_user_message():
    captured = {}

    def fake_invoke(messages):
        captured["messages"] = messages
        return _FakeResp(
            "```sql\nSELECT 1 FROM DUAL\n```\n```explanation\nstub\n```"
        )

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(side_effect=fake_invoke)
    node = make_sql_generator(fake_llm)

    state = {
        "user_input": "active customers per region",
        "schema_context": "-- TABLE: KYC.CUSTOMERS\nCREATE TABLE ...",
        "conversation_history": [],
        "validation_errors": [],
        "retry_count": 0,
        "intent": "DATA_QUERY",
        "_trace": [],
        "accepted_examples": [{
            "score": 0.62,
            "description": "Counts active customers grouped by region",
            "why_this_sql": "Filter STATUS='A', GROUP BY REGION on CUSTOMERS",
            "sql": "SELECT REGION, COUNT(*) FROM KYC.CUSTOMERS WHERE STATUS='A' GROUP BY REGION",
            "key_concepts": ["active customer", "region"],
            "tags": ["customer", "aggregation"],
        }],
    }

    node(state)

    user_msg = captured["messages"][1].content
    assert "ACCEPTED EXAMPLES" in user_msg
    assert "STATUS='A'" in user_msg
    assert "Counts active customers" in user_msg
    assert "0.62" in user_msg


def test_no_accepted_examples_means_no_extra_block():
    captured = {}

    def fake_invoke(messages):
        captured["messages"] = messages
        return _FakeResp("```sql\nSELECT 1 FROM DUAL\n```\n```explanation\nx\n```")

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(side_effect=fake_invoke)
    node = make_sql_generator(fake_llm)

    state = {
        "user_input": "anything",
        "schema_context": "x",
        "conversation_history": [],
        "validation_errors": [],
        "retry_count": 0,
        "intent": "DATA_QUERY",
        "_trace": [],
        "accepted_examples": [],
    }
    node(state)
    assert "ACCEPTED EXAMPLES" not in captured["messages"][1].content
```

- [ ] **Step 4.2: Run to verify failure**

```
python3.12 -m pytest tests/test_sql_generator_rag_examples.py -v
```
Expected: FAIL — sql_generator doesn't render `accepted_examples`.

- [ ] **Step 4.3: Add rule 20 to system prompt**

In `prompts/sql_generator_system.txt`, after rule 19, add:

```
20. ACCEPTED EXAMPLES — when the user message contains an "ACCEPTED EXAMPLES"
    block, those are previously-accepted (curator-approved) SQLs that scored
    moderately similar to the current question. You MUST:
    - Prefer their tables, joins, filters, and value literals over fresh
      invention when the user's intent matches.
    - When two examples disagree, prefer the one with the higher score.
    - When the user's question goes beyond what the examples cover, use them
      as a *starting point* and extend rather than ignoring them.
    - Never copy an example verbatim if the question wants something different
      (e.g. different aggregation, different filter); adapt and explain.
```

Also append the same text to `_SYSTEM_PROMPT` constant in `agent/nodes/sql_generator.py` after rule 19.

- [ ] **Step 4.4: Render `accepted_examples` in the user message**

In `agent/nodes/sql_generator.py:generate_sql`, after the `user_msg_parts` is initially built and before `if history_text:`:

```python
        accepted_examples = state.get("accepted_examples") or []
        if accepted_examples:
            ex_lines = ["", "--- ACCEPTED EXAMPLES (sorted by similarity) ---"]
            for i, ex in enumerate(accepted_examples, 1):
                ex_lines.append(
                    f"\nExample {i} (score={ex.get('score', 0)}):"
                )
                if ex.get("description"):
                    ex_lines.append(f"  Description: {ex['description']}")
                if ex.get("why_this_sql"):
                    ex_lines.append(f"  Reasoning: {ex['why_this_sql']}")
                if ex.get("key_concepts"):
                    ex_lines.append(f"  Key concepts: {', '.join(ex['key_concepts'])}")
                if ex.get("sql"):
                    ex_lines.append(f"  SQL:\n```sql\n{ex['sql']}\n```")
            ex_lines.append("\nFollow rule 20: prefer the tables/joins/filters of the most-similar example.")
            user_msg_parts.append("\n".join(ex_lines))
```

- [ ] **Step 4.5: Run tests to verify pass**

```
python3.12 -m pytest tests/test_sql_generator_rag_examples.py -v
```
Expected: PASS.

- [ ] **Step 4.6: Run full suite (no regressions)**

```
python3.12 -m pytest tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_value_grounding.py
```
Expected: All tests pass.

- [ ] **Step 4.7: Commit**

```bash
git add prompts/sql_generator_system.txt agent/nodes/sql_generator.py tests/test_sql_generator_rag_examples.py
git commit -m "feat(teach): SQL generator consumes accepted_examples

New rule 20 in the system prompt + an ACCEPTED EXAMPLES block injected
into the user message when state.accepted_examples is non-empty. The
LLM is told to prefer the examples' tables/joins/filters over fresh
invention but adapt for genuine differences."
```

---

## Self-Review Checklist

- [ ] **Spec coverage** — Obs 1 (richer entries) covered by Tasks 1+2; Obs 2 (priority boost) covered by Tasks 3+4.
- [ ] **Backward compat** — old query_session entries (no description/why_this_sql) still rank via legacy Jaccard; existing `find_session_match` left in place (deprecated but functional).
- [ ] **No new dependencies** — uses existing langchain, sqlglot, pytest.
- [ ] **Tests cover three thresholds** — short-circuit, RAG, ignore.
- [ ] **State shape additive** — `accepted_examples: List[Dict]` is the only new field.
- [ ] **Prompt rule numbered consistently** — rule 20 follows the rules-1-19 already in production.
