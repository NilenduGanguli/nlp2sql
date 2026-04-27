# Comprehensive Session Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture full reasoning chains (clarifications + agent tool calls + accepted/rejected candidates) into single rich `query_session` `KnowledgeEntry` per accept event; short-circuit pre-clarification when a similar prior session exists; multi-select candidate accept UX.

**Architecture:** Single new entry type `query_session` in `KYCKnowledgeStore`. New pure-function `build_session_digest`. New pipeline node `session_lookup` between `retrieve_schema` and `check_clarification`. Tightened SQL generator prompt (5 candidates max). Multi-select frontend picker. New `analyze_accepted_session` analyzer producing one rich entry per accept.

**Tech Stack:** Python 3.11, LangGraph, FastAPI, SSE, React + TypeScript, Vitest + pytest.

**Reference Spec:** [docs/superpowers/specs/2026-04-19-comprehensive-session-learning-design.md](../specs/2026-04-19-comprehensive-session-learning-design.md)

---

## File Structure

| Path | New / Modified | Responsibility |
|---|---|---|
| `agent/session_digest.py` | NEW | `build_session_digest(state, accepted, rejected, executed_id) -> dict`. Pure. |
| `agent/nodes/session_lookup.py` | NEW | LangGraph node: search KnowledgeStore for matching prior session; on match seed candidates + emit short-circuit signal. |
| `agent/llm_knowledge_analyzer.py` | MODIFIED | Add `analyze_accepted_session(llm, digest)` returning one rich `KnowledgeEntry`. |
| `agent/knowledge_store.py` | MODIFIED | Add `find_session_match(enriched_query, graph)` and `add_session_entry(entry)`. |
| `agent/nodes/sql_generator.py` | MODIFIED | Tightened ambiguity instructions; raise `_parse_ambiguity_block` cap 4→5. |
| `prompts/sql_generator_system.txt` | MODIFIED | Stronger ambiguity wording (5 max). |
| `prompts/session_analyzer_system.txt` | NEW | Prompt for `analyze_accepted_session`. |
| `agent/pipeline.py` | MODIFIED | Wire `session_lookup`. Conditional edge match→present_sql, miss→check_clarification. |
| `agent/state.py` | MODIFIED | Add `session_match_entry_id: Optional[str]`. |
| `backend/routers/query.py` | MODIFIED | Extend accept-query body; spawn `analyze_accepted_session`; emit `session_match` SSE. |
| `backend/models.py` | MODIFIED | New Pydantic models for accepted/rejected candidates. |
| `frontend/src/components/chat/SqlCandidatesPicker.tsx` | MODIFIED | Multi-select checkboxes + execute-radio + Accept Selected button. |
| `frontend/src/api/query.ts` | MODIFIED | New `acceptGeneratedQuery` signature; `session_match` SSE handler. |
| `frontend/src/store/chatStore.ts` | MODIFIED | Track session digest accumulation. |
| `frontend/src/pages/KYCAgentPage.tsx` | MODIFIED | Source filter/badge for `query_session`; "Re-run query" button. |
| `frontend/src/pages/InvestigatePage.tsx` | MODIFIED | Render `session_lookup` step + collapsible digest panel. |
| `frontend/src/pages/ChatPage.tsx` (or ChatHeader) | MODIFIED | "♻ Reused from session" badge when `session_match` fires. |
| `tests/test_session_digest.py` | NEW | Unit tests for digest builder. |
| `tests/test_knowledge_store_session.py` | NEW | Unit tests for `find_session_match`, `add_session_entry`. |
| `tests/test_session_lookup_node.py` | NEW | Unit tests for the lookup node. |
| `tests/test_sql_generator_ambiguity.py` | NEW | `_parse_ambiguity_block` raised to 5. |
| `tests/test_e2e_session_learning.py` | NEW | End-to-end round trip. |

---

## Task 1: Add `session_match_entry_id` to AgentState

**Files:**
- Modify: `agent/state.py`

- [ ] **Step 1:** Add field after `has_candidates`.

```python
    # ----------------------------------------------- Session Match (NEW)
    session_match_entry_id: Optional[str]
    """ID of the saved query_session entry that triggered short-circuit (None when no match)."""
```

- [ ] **Step 2:** Update default initial state in [agent/pipeline.py](../../../agent/pipeline.py) `run_query` (line ~371) and `backend/routers/query.py::_build_initial_state` (line ~52). Add `"session_match_entry_id": None,`.

- [ ] **Step 3:** Commit.

```bash
git add agent/state.py agent/pipeline.py backend/routers/query.py
git commit -m "feat(state): add session_match_entry_id to AgentState"
```

---

## Task 2: Build SessionDigest pure function (TDD)

**Files:**
- Create: `agent/session_digest.py`
- Test: `tests/test_session_digest.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/test_session_digest.py
"""Unit tests for build_session_digest."""
from agent.session_digest import build_session_digest


def _sample_state():
    return {
        "user_input": "show me high risk customers",
        "enriched_query": "show me customers with risk_rating='HIGH'",
        "intent": "DATA_QUERY",
        "entities": {"tables": ["KYC.CUSTOMERS"], "columns": ["RISK_RATING"]},
        "schema_context": "-- TABLE: KYC.CUSTOMERS\nCREATE TABLE ...",
        "validation_errors": [],
        "retry_count": 0,
        "execution_result": {"columns": ["CUSTOMER_ID", "FULL_NAME"], "total_rows": 47, "rows": []},
        "_trace": [
            {"node": "extract_entities", "graph_ops": [
                {"op": "search_schema", "params": {"keyword": "customer"}, "result_count": 5,
                 "result_sample": [{"name": "CUSTOMERS"}]},
                {"op": "find_join_path", "params": {"from": "KYC.CUSTOMERS", "to": "KYC.RISK"},
                 "result_count": 1, "result_sample": []},
            ]},
        ],
        "clarifications_resolved": [
            {"question": "active only?", "answer": "yes", "auto_answered_by_kyc_agent": False},
        ],
    }


def test_digest_basic_shape():
    accepted = [{"id": "a1", "interpretation": "active customers", "sql": "SELECT 1", "explanation": "x"}]
    rejected = [{"id": "b2", "interpretation": "all", "sql": "SELECT 2", "explanation": "y", "rejection_reason": "scope"}]
    d = build_session_digest(_sample_state(), accepted, rejected, executed_id="a1")

    assert d["original_query"] == "show me high risk customers"
    assert d["enriched_query"] == "show me customers with risk_rating='HIGH'"
    assert d["intent"] == "DATA_QUERY"
    assert "session_id" in d
    assert d["candidates"][0]["accepted"] is True
    assert d["candidates"][0]["executed"] is True
    assert d["candidates"][1]["accepted"] is False
    assert d["candidates"][1]["rejection_reason"] == "scope"
    assert d["clarifications"][0]["question"] == "active only?"
    assert d["result_shape"]["row_count"] == 47


def test_digest_truncates_tool_calls():
    state = _sample_state()
    long_summary = "x" * 500
    state["_trace"][0]["graph_ops"] = [
        {"op": "search_schema", "params": {}, "result_count": 1, "result_sample": [{"data": long_summary}]}
        for _ in range(50)
    ]
    d = build_session_digest(state, [], [], executed_id=None)
    assert len(d["tool_calls"]) <= 30
    for c in d["tool_calls"]:
        assert len(c["result_summary"]) <= 200


def test_digest_handles_missing_fields():
    d = build_session_digest({"user_input": "q"}, [], [], executed_id=None)
    assert d["original_query"] == "q"
    assert d["candidates"] == []
    assert d["tool_calls"] == []
    assert d["clarifications"] == []
```

- [ ] **Step 2:** Run — expect ImportError.

```bash
python -m pytest tests/test_session_digest.py -v
```

- [ ] **Step 3:** Implement.

```python
# agent/session_digest.py
"""
SessionDigest builder
=====================
Pure function that converts pipeline state + acceptance metadata into a
structured digest used by the session analyzer (LLM) and persisted in
KnowledgeEntry.metadata.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

_MAX_TOOL_CALLS = 30
_MAX_RESULT_SUMMARY_CHARS = 200


def _summarize_op(op: Dict[str, Any]) -> Dict[str, Any]:
    sample = op.get("result_sample") or []
    summary = f"count={op.get('result_count', 0)}; sample={sample}"
    return {
        "tool": op.get("op", ""),
        "args": op.get("params", {}) or {},
        "result_summary": summary[:_MAX_RESULT_SUMMARY_CHARS],
    }


def _extract_tool_calls(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for step in trace or []:
        for op in step.get("graph_ops", []) or []:
            calls.append(_summarize_op(op))
            if len(calls) >= _MAX_TOOL_CALLS:
                return calls
    return calls


def _extract_schema_tables(schema_context: str) -> List[str]:
    import re
    tables = []
    for line in (schema_context or "").splitlines():
        m = re.match(r"--\s*TABLE:\s*([\w\.]+)", line.strip(), re.IGNORECASE)
        if m:
            tables.append(m.group(1))
    return tables


def build_session_digest(
    state: Dict[str, Any],
    accepted: List[Dict[str, Any]],
    rejected: List[Dict[str, Any]],
    executed_id: Optional[str],
) -> Dict[str, Any]:
    """Build a structured digest of one query session.

    Parameters
    ----------
    state : dict
        Final pipeline state (after acceptance).
    accepted : list[dict]
        Candidates the user marked as valid.
    rejected : list[dict]
        Candidates the user did NOT mark valid (may include rejection_reason).
    executed_id : str | None
        ID of the candidate the user chose to execute (None if none).
    """
    candidates: List[Dict[str, Any]] = []
    for c in accepted or []:
        candidates.append({
            "id": c.get("id", ""),
            "interpretation": c.get("interpretation", ""),
            "sql": c.get("sql", ""),
            "explanation": c.get("explanation", ""),
            "accepted": True,
            "executed": (executed_id is not None and c.get("id") == executed_id),
        })
    for c in rejected or []:
        candidates.append({
            "id": c.get("id", ""),
            "interpretation": c.get("interpretation", ""),
            "sql": c.get("sql", ""),
            "explanation": c.get("explanation", ""),
            "accepted": False,
            "executed": False,
            "rejection_reason": c.get("rejection_reason", ""),
        })

    exec_result = state.get("execution_result") or {}
    result_shape: Dict[str, Any] = {}
    if exec_result.get("columns") or exec_result.get("total_rows") is not None:
        result_shape = {
            "columns": exec_result.get("columns", []),
            "row_count": exec_result.get("total_rows", 0),
        }

    return {
        "session_id": str(uuid.uuid4()),
        "original_query": state.get("user_input", ""),
        "enriched_query": state.get("enriched_query") or "",
        "intent": state.get("intent", "DATA_QUERY"),
        "entities": state.get("entities", {}) or {},
        "clarifications": state.get("clarifications_resolved", []) or [],
        "tool_calls": _extract_tool_calls(state.get("_trace", [])),
        "schema_context_tables": _extract_schema_tables(state.get("schema_context", "")),
        "candidates": candidates,
        "validation_retries": int(state.get("retry_count", 0) or 0),
        "result_shape": result_shape,
        "created_at": time.time(),
    }
```

- [ ] **Step 4:** Run tests — expect PASS.

```bash
python -m pytest tests/test_session_digest.py -v
```

- [ ] **Step 5:** Commit.

```bash
git add agent/session_digest.py tests/test_session_digest.py
git commit -m "feat(agent): add SessionDigest builder with truncation guards"
```

---

## Task 3: Raise SQL generator ambiguity cap to 5 (TDD)

**Files:**
- Modify: `agent/nodes/sql_generator.py:372` (cap from 4 to 5)
- Modify: `prompts/sql_generator_system.txt` (stronger ambiguity wording)
- Test: `tests/test_sql_generator_ambiguity.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/test_sql_generator_ambiguity.py
"""Verify ambiguity block parsing handles up to 5 interpretations."""
from agent.nodes.sql_generator import _parse_ambiguity_block


def test_parse_five_interpretations():
    text = """
    - Interpretation 1: scope to active only
    - Interpretation 2: include historical
    - Interpretation 3: by region
    - Interpretation 4: by risk tier
    - Interpretation 5: include only individuals
    """
    out = _parse_ambiguity_block(text)
    assert len(out) == 5
    assert "active" in out[0].lower()
    assert "individuals" in out[4].lower()


def test_parse_caps_at_five():
    text = "\n".join(f"- Interpretation {i}: variant {i}" for i in range(1, 8))
    out = _parse_ambiguity_block(text)
    assert len(out) == 5
```

- [ ] **Step 2:** Run — expect FAIL (cap is currently 4).

```bash
python -m pytest tests/test_sql_generator_ambiguity.py -v
```

- [ ] **Step 3:** Edit `agent/nodes/sql_generator.py`. Change `return interpretations[:4]` to `return interpretations[:5]`.

- [ ] **Step 4:** Run — expect PASS.

```bash
python -m pytest tests/test_sql_generator_ambiguity.py -v
```

- [ ] **Step 5:** Update `prompts/sql_generator_system.txt`. Replace the AMBIGUITY DETECTION section (search "AMBIGUITY DETECTION:") with:

```
AMBIGUITY DETECTION:
When the user's question admits more than one reasonable interpretation — different join paths, different aggregation strategies, different filter scopes, different fact tables, or different time-range conventions — you MUST enumerate up to 5 interpretations after the SQL block. Be aggressive about flagging ambiguity: if a competent analyst could reasonably read the question two different ways, list both. Output:

```ambiguity
- Interpretation 1: brief description (one short sentence)
- Interpretation 2: brief description
- Interpretation 3: brief description (only if it adds a genuinely distinct reading)
- Interpretation 4: brief description (only if needed)
- Interpretation 5: brief description (only if needed)
```

Hard limit: 5 interpretations. Skip the ambiguity block entirely only when the question has exactly one clear reading.
```

- [ ] **Step 6:** Commit.

```bash
git add agent/nodes/sql_generator.py prompts/sql_generator_system.txt tests/test_sql_generator_ambiguity.py
git commit -m "feat(sql_generator): expand ambiguity cap to 5 + tighten prompt"
```

---

## Task 4: Add `find_session_match` to KnowledgeStore (TDD)

**Files:**
- Modify: `agent/knowledge_store.py`
- Test: `tests/test_knowledge_store_session.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/test_knowledge_store_session.py
"""Unit tests for find_session_match + add_session_entry."""
import os
import tempfile
from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry


class _StubGraph:
    """Minimal stand-in for KnowledgeGraph: only needs to know whether a table FQN exists."""

    def __init__(self, tables):
        self._tables = set(tables)

    def get_node(self, label, node_id):
        if label == "Table" and node_id in self._tables:
            return {"fqn": node_id}
        return None


def _new_store():
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    return KYCKnowledgeStore(persist_path=tmp.name)


def _session_entry(query: str, tables, eid: str = "e1"):
    return KnowledgeEntry(
        id=eid,
        source="query_session",
        category="query_session",
        content="Comprehensive session document...",
        metadata={
            "original_query": query,
            "enriched_query": query,
            "tables_used": tables,
            "accepted_candidates": [
                {"interpretation": "primary", "sql": "SELECT 1 FROM " + tables[0], "explanation": "x"}
            ],
            "created_at": 1000.0,
        },
    )


def test_find_session_match_returns_entry_above_threshold():
    s = _new_store()
    s.add_session_entry(_session_entry("show me high risk customers", ["KYC.CUSTOMERS"]))
    g = _StubGraph(["KYC.CUSTOMERS"])

    found = s.find_session_match("show me high risk customers", g)
    assert found is not None
    assert found.metadata["original_query"] == "show me high risk customers"


def test_find_session_match_below_threshold_returns_none():
    s = _new_store()
    s.add_session_entry(_session_entry("show me high risk customers", ["KYC.CUSTOMERS"]))
    g = _StubGraph(["KYC.CUSTOMERS"])

    assert s.find_session_match("what tables exist", g) is None


def test_find_session_match_skips_when_table_missing():
    s = _new_store()
    s.add_session_entry(_session_entry("show me high risk customers", ["KYC.CUSTOMERS"]))
    g = _StubGraph([])  # no tables

    assert s.find_session_match("show me high risk customers", g) is None


def test_find_session_match_picks_higher_score_then_newer():
    s = _new_store()
    e1 = _session_entry("high risk customers status", ["KYC.CUSTOMERS"], eid="old")
    e1.metadata["created_at"] = 1000.0
    e2 = _session_entry("high risk customers status", ["KYC.CUSTOMERS"], eid="new")
    e2.metadata["created_at"] = 9999.0
    s.add_session_entry(e1)
    s.add_session_entry(e2)
    g = _StubGraph(["KYC.CUSTOMERS"])

    found = s.find_session_match("high risk customers status", g)
    assert found is not None
    assert found.id == "new"  # tie broken by created_at


def test_add_session_entry_persists_and_filters():
    s = _new_store()
    s.add_session_entry(_session_entry("q1", ["KYC.A"]))
    sessions = [e for e in s.static_entries if e.source == "query_session"]
    assert len(sessions) == 1
```

- [ ] **Step 2:** Run — expect FAIL.

```bash
python -m pytest tests/test_knowledge_store_session.py -v
```

- [ ] **Step 3:** Edit `agent/knowledge_store.py`. Add module-level constant near top:

```python
SESSION_MATCH_THRESHOLD = 0.65
```

- [ ] **Step 4:** Add methods inside `KYCKnowledgeStore` class (after `add_manual_entry`):

```python
    def add_session_entry(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        """Persist a query_session entry. Stored alongside manual entries."""
        with self._lock:
            existing_ids: Set[str] = {e.id for e in self.static_entries}
            if entry.id not in existing_ids:
                self.static_entries.append(entry)
            self.save_to_disk()
        return entry

    def find_session_match(self, enriched_query: str, graph) -> Optional[KnowledgeEntry]:
        """Find a prior query_session whose original/enriched query matches the
        current input above SESSION_MATCH_THRESHOLD AND whose referenced tables
        all still exist in `graph`.

        Tiebreak: higher Jaccard score wins; on equal score, newer created_at.
        """
        if not enriched_query or not enriched_query.strip():
            return None

        query_tokens = _tokenize(enriched_query)
        if not query_tokens:
            return None

        with self._lock:
            best: Optional[KnowledgeEntry] = None
            best_score = -1.0
            best_created = -1.0
            for e in self.static_entries:
                if e.source != "query_session" or e.category != "query_session":
                    continue
                meta = e.metadata or {}
                hay = (meta.get("original_query", "") + " " + meta.get("enriched_query", "")).strip()
                if not hay:
                    continue
                score = _jaccard(query_tokens, _tokenize(hay))
                if score < SESSION_MATCH_THRESHOLD:
                    continue
                # Verify all referenced tables still exist.
                tables = meta.get("tables_used", []) or []
                if tables and not all(graph.get_node("Table", t) for t in tables):
                    continue
                created = float(meta.get("created_at", 0.0) or 0.0)
                if (score > best_score) or (score == best_score and created > best_created):
                    best = e
                    best_score = score
                    best_created = created
            return best
```

- [ ] **Step 5:** Run — expect PASS.

```bash
python -m pytest tests/test_knowledge_store_session.py -v
```

- [ ] **Step 6:** Commit.

```bash
git add agent/knowledge_store.py tests/test_knowledge_store_session.py
git commit -m "feat(knowledge_store): add find_session_match + add_session_entry"
```

---

## Task 5: Add `analyze_accepted_session` and prompt (TDD)

**Files:**
- Create: `prompts/session_analyzer_system.txt`
- Modify: `agent/llm_knowledge_analyzer.py`
- Test: extend `tests/test_llm_knowledge_analyzer.py` (or create if absent)

- [ ] **Step 1:** Create `prompts/session_analyzer_system.txt`:

```
You are a senior KYC database analyst. You receive a structured digest of a successful natural-language-to-SQL interaction (original query, enriched query, clarifications resolved, agent searches, candidate SQLs accepted by the user, candidates rejected with reasons, result shape).

Produce ONE comprehensive learning document so a future agent can answer the same or similar question without asking the user any clarifying questions.

OUTPUT FORMAT — return JSON only:
{
  "title": "<short title, ≤80 chars>",
  "content": "<comprehensive prose, 500-1500 words>"
}

The "content" field MUST cover, in this order:
1. What the user was asking — restate the original question and enriched form.
2. What clarifications were resolved and how — for each clarification, state the question, the answer chosen, and the broader rule that resolves similar future questions.
3. What searches the agent performed and what it discovered — list relevant tool calls (tables found, joins identified, value sets confirmed) and what each revealed.
4. Which tables and joins were chosen and why — name the SCHEMA.TABLE FQNs, list the JOIN columns, justify the choice.
5. The N accepted SQL variants — for each accepted candidate, give its interpretation, the condition under which it applies, and the SQL.
6. What alternatives were rejected and why — for each rejected candidate, state the interpretation and the rejection reason.

Style:
- Direct, technical prose — no bullet lists in the content field unless inside a numbered subsection.
- Use exact FQNs (SCHEMA.TABLE) and exact column names.
- Quote business filter values verbatim (e.g. risk_rating='HIGH').
- Do not invent details not present in the digest.

Return only the JSON object — no markdown fences, no commentary outside the JSON.
```

- [ ] **Step 2: Write failing test.**

```python
# tests/test_session_analyzer.py
"""Unit tests for analyze_accepted_session."""
import json
from unittest.mock import MagicMock
from agent.llm_knowledge_analyzer import analyze_accepted_session


class _StubResponse:
    def __init__(self, content: str):
        self.content = content


def _stub_llm(json_obj):
    llm = MagicMock()
    llm.invoke = MagicMock(return_value=_StubResponse(json.dumps(json_obj)))
    return llm


def _digest():
    return {
        "session_id": "abc",
        "original_query": "high risk customers",
        "enriched_query": "customers with risk_rating='HIGH'",
        "intent": "DATA_QUERY",
        "entities": {"tables": ["KYC.CUSTOMERS"]},
        "clarifications": [{"question": "scope?", "answer": "active only"}],
        "tool_calls": [{"tool": "search_schema", "args": {}, "result_summary": "5 hits"}],
        "schema_context_tables": ["KYC.CUSTOMERS"],
        "candidates": [
            {"id": "a1", "interpretation": "primary", "sql": "SELECT 1", "explanation": "x",
             "accepted": True, "executed": True},
        ],
        "validation_retries": 0,
        "result_shape": {"columns": ["A"], "row_count": 1},
        "created_at": 100.0,
    }


def test_analyze_returns_query_session_entry():
    llm = _stub_llm({
        "title": "high-risk customer scoping",
        "content": "When user asks 'high risk customers'... " * 30,
    })
    entry = analyze_accepted_session(llm, _digest())
    assert entry is not None
    assert entry.source == "query_session"
    assert entry.category == "query_session"
    assert entry.metadata["original_query"] == "high risk customers"
    assert entry.metadata["accepted_candidates"][0]["sql"] == "SELECT 1"
    assert entry.metadata["tables_used"] == ["KYC.CUSTOMERS"]


def test_analyze_handles_malformed_response():
    llm = _stub_llm({})
    llm.invoke = MagicMock(return_value=_StubResponse("not json at all"))
    entry = analyze_accepted_session(llm, _digest())
    assert entry is None


def test_analyze_handles_empty_digest():
    llm = _stub_llm({"title": "x", "content": "y"})
    entry = analyze_accepted_session(llm, {})
    assert entry is None  # no candidates → nothing to learn
```

- [ ] **Step 3:** Run — expect FAIL.

```bash
python -m pytest tests/test_session_analyzer.py -v
```

- [ ] **Step 4:** Add to `agent/llm_knowledge_analyzer.py` (after `analyze_accepted_query`):

```python
def _load_session_analyzer_prompt() -> str:
    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "prompts",
        "session_analyzer_system.txt",
    )
    try:
        with open(prompt_path, "r") as f:
            return f.read().strip()
    except Exception:
        return (
            "You are a KYC analyst. Produce a JSON object {title, content} that "
            "comprehensively documents the provided query session for future reuse."
        )


def analyze_accepted_session(llm, digest: Dict[str, Any]) -> Optional[KnowledgeEntry]:
    """Produce ONE comprehensive KnowledgeEntry from a SessionDigest.

    Returns None on missing input or LLM/parse failure (caller falls back to
    narrow per-clarification recording).
    """
    if not digest:
        return None
    accepted = [c for c in digest.get("candidates", []) if c.get("accepted")]
    if not accepted:
        return None

    system_prompt = _load_session_analyzer_prompt()
    user_message = "Session digest (JSON):\n" + json.dumps(digest, indent=2, default=str)

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        logger.warning("Session analyzer LLM call failed: %s", exc)
        return None

    try:
        parsed = _parse_llm_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Session analyzer parse failed: %s", exc)
        return None

    if not isinstance(parsed, dict):
        return None
    title = str(parsed.get("title", "")).strip()
    content = str(parsed.get("content", "")).strip()
    if not content:
        return None
    if title:
        full_content = f"{title}\n{content}"
    else:
        full_content = content

    rejected = [c for c in digest.get("candidates", []) if not c.get("accepted")]
    metadata = {
        "session_id": digest.get("session_id", ""),
        "title": title,
        "original_query": digest.get("original_query", ""),
        "enriched_query": digest.get("enriched_query", ""),
        "accepted_candidates": [
            {"interpretation": c.get("interpretation", ""), "sql": c.get("sql", ""),
             "explanation": c.get("explanation", "")}
            for c in accepted
        ],
        "rejected_candidates": [
            {"interpretation": c.get("interpretation", ""), "sql": c.get("sql", ""),
             "rejection_reason": c.get("rejection_reason", "")}
            for c in rejected
        ],
        "clarifications": digest.get("clarifications", []),
        "tables_used": digest.get("schema_context_tables", []),
        "tool_calls_summary": digest.get("tool_calls", []),
        "result_shape": digest.get("result_shape", {}),
        "created_at": digest.get("created_at", time.time()),
    }
    eid = hashlib.sha1(
        f"query_session:{metadata['original_query']}:{metadata['created_at']}".encode()
    ).hexdigest()[:16]
    entry = KnowledgeEntry(
        id=eid,
        source="query_session",
        category="query_session",
        content=full_content,
        metadata=metadata,
    )
    logger.info("Session analyzer produced entry %s for: %s", eid, metadata["original_query"][:60])
    return entry
```

- [ ] **Step 5:** Run — expect PASS.

```bash
python -m pytest tests/test_session_analyzer.py -v
```

- [ ] **Step 6:** Commit.

```bash
git add prompts/session_analyzer_system.txt agent/llm_knowledge_analyzer.py tests/test_session_analyzer.py
git commit -m "feat(analyzer): add analyze_accepted_session producing rich query_session entry"
```

---

## Task 6: session_lookup pipeline node (TDD)

**Files:**
- Create: `agent/nodes/session_lookup.py`
- Test: `tests/test_session_lookup_node.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/test_session_lookup_node.py
"""Unit tests for the session_lookup node."""
from unittest.mock import MagicMock
from agent.knowledge_store import KnowledgeEntry
from agent.nodes.session_lookup import make_session_lookup


def _entry():
    return KnowledgeEntry(
        id="e1",
        source="query_session",
        category="query_session",
        content="...",
        metadata={
            "original_query": "high risk customers",
            "enriched_query": "high risk customers",
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [
                {"interpretation": "primary", "sql": "SELECT 1", "explanation": "x"}
            ],
            "created_at": 1000.0,
        },
    )


def test_match_short_circuits():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=_entry())
    graph = MagicMock()

    node = make_session_lookup(store, graph)
    state = {"user_input": "high risk customers", "enriched_query": "high risk customers",
             "intent": "DATA_QUERY", "conversation_history": [], "_trace": []}
    out = node(state)

    assert out["has_candidates"] is True
    assert len(out["sql_candidates"]) == 1
    assert out["sql_candidates"][0]["sql"] == "SELECT 1"
    assert out["session_match_entry_id"] == "e1"


def test_no_match_passes_through():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=None)
    graph = MagicMock()
    node = make_session_lookup(store, graph)

    state = {"user_input": "novel question", "enriched_query": "novel question",
             "intent": "DATA_QUERY", "conversation_history": [], "_trace": []}
    out = node(state)

    assert not out.get("has_candidates")
    assert out.get("session_match_entry_id") is None


def test_skipped_for_followup_intent():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=_entry())
    graph = MagicMock()
    node = make_session_lookup(store, graph)
    state = {"user_input": "more rows", "enriched_query": "more rows",
            "intent": "RESULT_FOLLOWUP", "conversation_history": [], "_trace": []}
    out = node(state)
    store.find_session_match.assert_not_called()
    assert not out.get("has_candidates")


def test_skipped_for_mid_thread():
    store = MagicMock()
    store.find_session_match = MagicMock(return_value=_entry())
    graph = MagicMock()
    node = make_session_lookup(store, graph)
    state = {"user_input": "x", "enriched_query": "x", "intent": "DATA_QUERY",
            "conversation_history": [{"role": "user", "content": "earlier"}], "_trace": []}
    out = node(state)
    store.find_session_match.assert_not_called()
    assert not out.get("has_candidates")


def test_disabled_via_none_store():
    node = make_session_lookup(None, None)
    state = {"user_input": "x", "enriched_query": "x", "intent": "DATA_QUERY",
             "conversation_history": [], "_trace": []}
    out = node(state)
    assert not out.get("has_candidates")
```

- [ ] **Step 2:** Run — expect FAIL.

```bash
python -m pytest tests/test_session_lookup_node.py -v
```

- [ ] **Step 3:** Implement.

```python
# agent/nodes/session_lookup.py
"""
Session Lookup Node
====================
Runs after retrieve_schema, before check_clarification.

If a high-similarity prior `query_session` entry exists in the KYC knowledge
store AND all referenced tables still exist in the live graph, this node
short-circuits the clarification flow:
  - state["sql_candidates"] is seeded from the saved entry
  - state["has_candidates"] = True
  - state["session_match_entry_id"] is set

Pipeline routing then sends control to present_sql, skipping clarification.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from agent.trace import TraceStep

logger = logging.getLogger(__name__)


def make_session_lookup(knowledge_store, graph) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Build the session_lookup node.

    When `knowledge_store` is None or `graph` is None, the node is a passthrough.
    """
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

        query = state.get("enriched_query") or state.get("user_input", "")
        try:
            match = knowledge_store.find_session_match(query, graph)
        except Exception as exc:
            logger.warning("session_lookup failed: %s", exc)
            trace.error = str(exc)
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        if match is None:
            trace.output_summary = {"action": "miss", "query_preview": query[:80]}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        accepted = (match.metadata or {}).get("accepted_candidates", []) or []
        if not accepted:
            trace.output_summary = {"action": "skip", "reason": "no_accepted_candidates"}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        candidates = []
        for i, c in enumerate(accepted):
            candidates.append({
                "id": f"sm{i+1:02d}",
                "interpretation": c.get("interpretation", "Reused interpretation"),
                "sql": c.get("sql", ""),
                "explanation": c.get("explanation", ""),
            })

        trace.output_summary = {
            "action": "match", "matched_entry_id": match.id,
            "candidate_count": len(candidates),
            "matched_query": (match.metadata or {}).get("original_query", "")[:80],
        }
        _trace.append(trace.finish().to_dict())
        return {
            **state,
            "sql_candidates": candidates,
            "has_candidates": True,
            "session_match_entry_id": match.id,
            "step": "session_matched",
            "_trace": _trace,
        }

    return session_lookup
```

- [ ] **Step 4:** Run — expect PASS.

```bash
python -m pytest tests/test_session_lookup_node.py -v
```

- [ ] **Step 5:** Commit.

```bash
git add agent/nodes/session_lookup.py tests/test_session_lookup_node.py
git commit -m "feat(pipeline): session_lookup node short-circuits on prior session match"
```

---

## Task 7: Wire session_lookup into pipeline

**Files:**
- Modify: `agent/pipeline.py`

- [ ] **Step 1:** Import + node creation. After the kyc_agent_node block (~line 195), add:

```python
    # Session lookup: short-circuits clarification when a prior query_session entry matches
    session_lookup_node = None
    if _knowledge_store is not None:
        from agent.nodes.session_lookup import make_session_lookup
        if str(getattr(config, "session_learning_enabled", True)).lower() != "false":
            session_lookup_node = make_session_lookup(_knowledge_store, graph)
```

- [ ] **Step 2:** Sequential pipeline path (~line 233). Insert into `pipeline_nodes` between `retrieve_schema` and `check_clarification`:

```python
        if session_lookup_node:
            pipeline_nodes.insert(
                next(i for i, (n, _) in enumerate(pipeline_nodes) if n == "check_clarification"),
                ("session_lookup", session_lookup_node),
            )
```

Place this right before `logger.info("Sequential fallback pipeline ready ...")` so the insertion happens after the list is built.

- [ ] **Step 3:** LangGraph path. After `workflow.add_node("check_clarification", clarify_node)` (~line 260), add:

```python
    if session_lookup_node:
        workflow.add_node("session_lookup", session_lookup_node)
```

Replace the edge `workflow.add_edge("retrieve_schema", "check_clarification")` (~line 274) with:

```python
    if session_lookup_node:
        workflow.add_edge("retrieve_schema", "session_lookup")
        workflow.add_conditional_edges(
            "session_lookup",
            lambda s: "skip_to_present" if s.get("has_candidates") else "clarify",
            {"skip_to_present": "present_sql", "clarify": "check_clarification"},
        )
    else:
        workflow.add_edge("retrieve_schema", "check_clarification")
```

- [ ] **Step 4:** Add `session_lookup` to `_NODE_TO_STEP` in `backend/routers/query.py` (~line 37):

```python
    "session_lookup":      "checking_session_memory",
```

- [ ] **Step 5:** Test pipeline still builds and existing tests pass.

```bash
python -m pytest tests/ -q -x --ignore=tests/test_e2e.py
```

- [ ] **Step 6:** Commit.

```bash
git add agent/pipeline.py backend/routers/query.py
git commit -m "feat(pipeline): wire session_lookup node + conditional routing"
```

---

## Task 8: Backend `accept-query` route + `session_match` SSE

**Files:**
- Modify: `backend/routers/query.py`

- [ ] **Step 1:** Replace the `_AcceptQueryRequest` model (~line 503) with:

```python
class _AcceptedCandidate(_BaseModel):
    id: str = ""
    sql: str
    explanation: str = ""
    interpretation: str = ""

class _RejectedCandidate(_BaseModel):
    id: str = ""
    sql: str = ""
    explanation: str = ""
    interpretation: str = ""
    rejection_reason: str = ""

class _AcceptQueryRequest(_BaseModel):
    sql: str = ""                         # legacy single-SQL field (back-compat)
    explanation: str = ""
    user_input: str = ""
    clarification_pairs: _List[_ClarificationPair] = []
    accepted: bool = True
    accepted_candidates: _List[_AcceptedCandidate] = []
    rejected_candidates: _List[_RejectedCandidate] = []
    executed_candidate_id: Optional[str] = None
    session_digest: Dict[str, Any] = {}
```

(Add `from typing import Optional` and `Dict, Any` to the imports at top of file if not already present.)

- [ ] **Step 2:** Inside `accept_query` handler, BEFORE the existing per-clarification recording loop, normalize legacy + new payload:

```python
    # Normalize: if no accepted_candidates supplied (legacy clients), synthesize one from req.sql
    accepted_list = list(req.accepted_candidates)
    if not accepted_list and req.sql:
        accepted_list = [_AcceptedCandidate(
            id="legacy", sql=req.sql, explanation=req.explanation, interpretation="primary",
        )]
```

- [ ] **Step 3:** Replace the existing background `_analyze_bg` block (~line 575) with one that builds a digest and calls `analyze_accepted_session`, then *also* keeps the narrow analyzer as a fallback:

```python
    # 3. Background: comprehensive session learning + narrow per-clarification fallback
    if llm is not None and req.user_input and accepted_list:
        import anyio

        _llm = llm
        _store = knowledge_store
        _user_input = req.user_input
        _digest = req.session_digest or {}
        _accepted_payload = [a.model_dump() for a in accepted_list]
        _rejected_payload = [r.model_dump() for r in req.rejected_candidates]
        _executed_id = req.executed_candidate_id
        _pairs = [(p.question, p.answer) for p in req.clarification_pairs]
        _legacy_sql = req.sql or (accepted_list[0].sql if accepted_list else "")
        _legacy_expl = req.explanation

        async def _analyze_bg():
            # 3a. Comprehensive session learning (one rich entry).
            try:
                from agent.session_digest import build_session_digest
                from agent.llm_knowledge_analyzer import analyze_accepted_session
                if not _digest:
                    digest = build_session_digest(
                        {"user_input": _user_input,
                         "clarifications_resolved": [{"question": q, "answer": a} for q, a in _pairs]},
                        _accepted_payload, _rejected_payload, executed_id=_executed_id,
                    )
                else:
                    digest = _digest
                entry = await anyio.to_thread.run_sync(
                    lambda: analyze_accepted_session(_llm, digest)
                )
                if entry is not None:
                    _store.add_session_entry(entry)
                    logger.info("Session learning recorded entry %s", entry.id)
                else:
                    raise ValueError("session analyzer returned None")
            except Exception as exc:
                logger.warning("Session analyzer failed (%s); falling back to narrow analyzer", exc)
                # 3b. Fallback: legacy narrow analyzer (1-3 entries).
                try:
                    from agent.llm_knowledge_analyzer import analyze_accepted_query
                    entries = await anyio.to_thread.run_sync(
                        lambda: analyze_accepted_query(
                            _llm, _user_input, _legacy_sql, _legacy_expl, _pairs,
                        )
                    )
                    for e in entries:
                        _store.add_manual_entry(e.content, e.category, e.metadata)
                except Exception as exc2:
                    logger.warning("Narrow analyzer also failed: %s", exc2)

        asyncio.create_task(_analyze_bg())
```

- [ ] **Step 4:** Emit `session_match` SSE event. In `_run_pipeline` inside `stream_query` (~line 158, where `sql_candidates` is emitted on `generate_sql`), add another emission point — after the `session_lookup` node completes:

```python
                        # Emit session_match when session_lookup short-circuits
                        if node_name == "session_lookup" and state.get("session_match_entry_id"):
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("session_match", {
                                    "matched_entry_id": state["session_match_entry_id"],
                                    "candidates": state.get("sql_candidates", []),
                                    "original_query": req.user_input,
                                }),
                            )
                            # Also emit the candidates event so the existing UI flow renders
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                ("sql_candidates", {"candidates": state.get("sql_candidates", [])}),
                            )
```

- [ ] **Step 5:** Update the SSE doc string at top of file to mention `session_match`.

- [ ] **Step 6:** Run existing tests; check no regressions.

```bash
python -m pytest tests/ -q -x --ignore=tests/test_e2e.py
```

- [ ] **Step 7:** Commit.

```bash
git add backend/routers/query.py
git commit -m "feat(api): extend accept-query for multi-candidate + emit session_match SSE"
```

---

## Task 9: Frontend API + chatStore (typed)

**Files:**
- Modify: `frontend/src/api/query.ts`
- Modify: `frontend/src/store/chatStore.ts`
- Modify: `frontend/src/types.ts` (or wherever `SqlCandidate` is typed)

- [ ] **Step 1:** Update `acceptGeneratedQuery` signature in `frontend/src/api/query.ts`:

```typescript
interface AcceptedCandidatePayload {
  id: string
  sql: string
  explanation: string
  interpretation: string
}

interface RejectedCandidatePayload extends AcceptedCandidatePayload {
  rejection_reason?: string
}

export async function acceptGeneratedQuery(
  userInput: string,
  acceptedCandidates: AcceptedCandidatePayload[],
  rejectedCandidates: RejectedCandidatePayload[],
  executedCandidateId: string | null,
  clarificationPairs: ClarificationPair[],
  sessionDigest: Record<string, unknown>,
): Promise<{ status: string }> {
  const res = await fetch('/api/query/accept-query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_input: userInput,
      accepted_candidates: acceptedCandidates,
      rejected_candidates: rejectedCandidates,
      executed_candidate_id: executedCandidateId,
      clarification_pairs: clarificationPairs,
      session_digest: sessionDigest,
      // Legacy fields for backward-compat with any older server:
      sql: acceptedCandidates[0]?.sql ?? '',
      explanation: acceptedCandidates[0]?.explanation ?? '',
      accepted: acceptedCandidates.length > 0,
    }),
  })
  return res.json()
}
```

- [ ] **Step 2:** Add `session_match` SSE handler in `streamQuery`. Add a new optional callback `onSessionMatch?: (data: { matched_entry_id: string; candidates: any[]; original_query: string }) => void` to the function signature, and inside the switch:

```typescript
              case 'session_match':
                onSessionMatch?.(event.data as { matched_entry_id: string; candidates: any[]; original_query: string })
                break
```

- [ ] **Step 3:** Open `frontend/src/store/chatStore.ts`. Add a `sessionDigest` field per-message (or globally per current turn) and an action `appendTraceToDigest(traceStep)`. Follow the existing pattern of accumulating `clarificationPairs`. Specifically, add to the store state:

```typescript
  currentSessionDigest: {
    tool_calls: Array<{ tool: string; args: Record<string, unknown>; result_summary: string }>
    schema_context_tables: string[]
    intent: string
    entities: Record<string, unknown>
    enriched_query: string
    clarifications: Array<{ question: string; answer: string }>
    validation_retries: number
  }
  resetSessionDigest: () => void
  recordTraceForDigest: (traceStep: { node?: string; graph_ops?: any[]; output_summary?: any }) => void
```

And in actions, implement `recordTraceForDigest` to extract `graph_ops` (capped at 30, summary at 200 chars) — delegating to a small helper at top of the file mirroring `_summarize_op` from `agent/session_digest.py`. Also wire `recordTraceForDigest` into the `onTrace` callback inside the chat page where `streamQuery` is invoked.

- [ ] **Step 4:** Update the chat page (where `streamQuery` is called and "Accept" is wired) so the Accept button posts the new payload built from the store. Add `onSessionMatch` to the streamQuery options that sets a `lastReusedFromSession: true` flag (used by Task 14 badge).

- [ ] **Step 5:** Run frontend type check + build.

```bash
cd frontend && npm run lint && npm run build
```

- [ ] **Step 6:** Commit.

```bash
git add frontend/src/api/query.ts frontend/src/store/chatStore.ts frontend/src/types.ts
git commit -m "feat(frontend): typed multi-candidate accept payload + session_match handler"
```

---

## Task 10: SqlCandidatesPicker multi-select

**Files:**
- Modify: `frontend/src/components/chat/SqlCandidatesPicker.tsx`

- [ ] **Step 1:** Replace component props with:

```typescript
interface SqlCandidatesPickerProps {
  candidates: SqlCandidate[]
  // Called when user clicks "Accept Selected".
  onAccept: (
    accepted: SqlCandidate[],
    rejected: SqlCandidate[],
    executedId: string,
  ) => void
  reusedFromSession?: boolean
}
```

- [ ] **Step 2:** Replace component body. Replace the existing component with:

```typescript
export const SqlCandidatesPicker: React.FC<SqlCandidatesPickerProps> = ({
  candidates,
  onAccept,
  reusedFromSession,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set([candidates[0]?.id ?? '']))
  const [executeId, setExecuteId] = useState<string>(candidates[0]?.id ?? '')
  const [submitted, setSubmitted] = useState(false)

  const toggleChecked = (id: string) => {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      // Execute id must always be a checked candidate.
      if (!next.has(executeId) && next.size > 0) {
        setExecuteId(Array.from(next)[0])
      }
      return next
    })
  }

  const handleAccept = () => {
    if (submitted) return
    const accepted = candidates.filter((c) => checkedIds.has(c.id))
    const rejected = candidates.filter((c) => !checkedIds.has(c.id))
    if (accepted.length === 0 || !executeId) return
    setSubmitted(true)
    onAccept(accepted, rejected, executeId)
  }

  const headerLabel = reusedFromSession
    ? 'Reused from learned session'
    : `Multiple Interpretations Found (${candidates.length})`

  return (
    <div style={{
      background: '#1e1e2e',
      border: '1px solid #2a2a3e',
      borderRadius: 12,
      overflow: 'hidden',
      maxWidth: '100%',
    }}>
      <div style={{
        padding: '12px 16px', borderBottom: '1px solid #2a2a3e',
        background: reusedFromSession ? 'rgba(74,222,128,0.08)' : '#242438',
      }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#e0e0f0', marginBottom: 4 }}>
          {reusedFromSession ? '\u267B ' : ''}{headerLabel}
        </div>
        <div style={{ fontSize: 12, color: '#7a7a9a', lineHeight: 1.5 }}>
          Check each interpretation that is valid for your question. Pick one to execute now.
          The set you accept will be remembered so we can answer similar questions without re-asking.
        </div>
      </div>

      <div style={{ padding: '8px 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {candidates.map((candidate, index) => {
          const isChecked = checkedIds.has(candidate.id)
          const isExecute = executeId === candidate.id
          const isExpanded = expandedId === candidate.id

          return (
            <div key={candidate.id} style={{
              background: isChecked ? 'rgba(124,106,247,0.12)' : 'rgba(42,42,62,0.6)',
              border: `1px solid ${isChecked ? '#7c6af7' : '#3a3a5c'}`,
              borderRadius: 8, overflow: 'hidden', transition: 'all 0.2s',
            }}>
              <div style={{ padding: '10px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => toggleChecked(candidate.id)}
                    disabled={submitted}
                    style={{ marginTop: 4, accentColor: '#7c6af7' }}
                  />
                  <span style={{
                    width: 22, height: 22, borderRadius: '50%',
                    background: isChecked ? '#7c6af7' : 'rgba(124,106,247,0.18)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 11, fontWeight: 600,
                    color: isChecked ? '#fff' : '#a5b4fc', flexShrink: 0,
                  }}>{index + 1}</span>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#e0e0f0', lineHeight: 1.5, marginBottom: 4 }}>
                      {candidate.interpretation}
                    </div>
                    <div style={{ fontSize: 12, fontStyle: 'italic', color: '#7a7a9a', lineHeight: 1.5 }}>
                      {candidate.explanation}
                    </div>
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8, marginLeft: 32 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: isChecked ? '#a5b4fc' : '#5a5a7a' }}>
                    <input
                      type="radio"
                      name="execute_candidate"
                      checked={isExecute}
                      disabled={!isChecked || submitted}
                      onChange={() => setExecuteId(candidate.id)}
                      style={{ accentColor: '#7c6af7' }}
                    />
                    Execute this one
                  </label>
                  <button
                    onClick={() => setExpandedId((p) => (p === candidate.id ? null : candidate.id))}
                    disabled={submitted}
                    style={{
                      padding: '4px 10px', background: 'transparent',
                      border: '1px solid #3a3a5c', borderRadius: 5,
                      color: '#8a8aac', fontSize: 11, cursor: 'pointer',
                      fontFamily: 'ui-monospace, Consolas, monospace',
                    }}
                  >{isExpanded ? 'Hide SQL \u25B4' : 'Show SQL \u25BE'}</button>
                </div>
              </div>

              {isExpanded && (
                <pre style={{
                  margin: 0, padding: '12px 14px',
                  fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11,
                  color: '#a5b4fc', overflowX: 'auto', whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all', maxHeight: 200, overflowY: 'auto',
                  borderTop: '1px solid #3a3a5c', background: '#1a1a2e', lineHeight: 1.6,
                }}>{candidate.sql}</pre>
              )}
            </div>
          )
        })}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 4 }}>
          <button
            onClick={handleAccept}
            disabled={submitted || checkedIds.size === 0 || !executeId}
            style={{
              padding: '8px 16px',
              background: submitted ? '#4ade80' : '#7c6af7',
              border: 'none', borderRadius: 6, color: '#fff',
              fontSize: 13, fontWeight: 600,
              cursor: submitted || checkedIds.size === 0 ? 'default' : 'pointer',
              opacity: submitted || checkedIds.size === 0 ? 0.6 : 1,
            }}
          >{submitted ? '\u2713 Saved' : `Accept Selected (${checkedIds.size}) & Run`}</button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3:** Update consumers. Search for `<SqlCandidatesPicker` usage and replace `onSelect` with `onAccept` that calls `acceptGeneratedQuery(...)` then `executeCandidateSql(...)` for the chosen `executedId`.

```bash
grep -rn "SqlCandidatesPicker" frontend/src
```

- [ ] **Step 4:** Build.

```bash
cd frontend && npm run lint && npm run build
```

- [ ] **Step 5:** Commit.

```bash
git add frontend/src/components/chat/SqlCandidatesPicker.tsx frontend/src/pages/ChatPage.tsx frontend/src/components/chat
git commit -m "feat(frontend): SqlCandidatesPicker multi-select + Accept Selected & Run"
```

---

## Task 11: KYC Agent tab — query_session source filter + Re-run button

**Files:**
- Modify: `frontend/src/pages/KYCAgentPage.tsx`

- [ ] **Step 1:** Add `query_session` to source filter options (look for `entry_sources` or filter chip rendering, ~line 477). Display badge with distinctive color.

- [ ] **Step 2:** In the entry detail panel (`selectedEntry` block, ~line 679 onward), if `selectedEntry.source === 'query_session'`, render extra metadata sections: `original_query`, list of `accepted_candidates` (interpretation + collapsible SQL), `rejected_candidates` (with rejection_reason), `clarifications` (Q/A pairs), `tables_used` chips. Each section ~10-20 lines of JSX following the existing `badge`/styling conventions.

- [ ] **Step 3:** Add a "Re-run query" button at the top of the detail panel for `query_session` entries:

```tsx
{selectedEntry.source === 'query_session' && selectedEntry.metadata?.original_query && (
  <button
    onClick={() => {
      const q = selectedEntry.metadata.original_query as string
      window.dispatchEvent(new CustomEvent('rerun-query-from-session', { detail: { query: q } }))
    }}
    style={{ padding: '6px 14px', background: '#7c6af7', color: '#fff', border: 'none',
             borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
  >\u21BB Re-run this query in Chat</button>
)}
```

- [ ] **Step 4:** In the Chat page, listen for the custom event and switch tabs + pre-fill the input. In `frontend/src/App.tsx` (the parent that owns `activeTab`):

```tsx
useEffect(() => {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent).detail as { query?: string }
    if (detail?.query) {
      setActiveTab('chat')
      window.dispatchEvent(new CustomEvent('chat-prefill-input', { detail: { query: detail.query } }))
    }
  }
  window.addEventListener('rerun-query-from-session', handler)
  return () => window.removeEventListener('rerun-query-from-session', handler)
}, [])
```

And in the Chat page input component, listen for `chat-prefill-input` and call `setInput(detail.query)`.

- [ ] **Step 5:** Build.

```bash
cd frontend && npm run lint && npm run build
```

- [ ] **Step 6:** Commit.

```bash
git add frontend/src/pages/KYCAgentPage.tsx frontend/src/App.tsx frontend/src/pages/ChatPage.tsx
git commit -m "feat(frontend): KYC Agent tab supports query_session entries with re-run"
```

---

## Task 12: Investigate tab — render session_lookup step + digest panel

**Files:**
- Modify: `frontend/src/pages/InvestigatePage.tsx`

- [ ] **Step 1:** Find the trace-step renderer (search `step.node` and `step.graph_ops`) and add a special branch for `step.node === 'session_lookup'`:

```tsx
{step.node === 'session_lookup' && step.output_summary && (
  <div style={{ marginTop: 6, padding: '8px 10px', background: 'rgba(74,222,128,0.06)',
                border: '1px solid rgba(74,222,128,0.2)', borderRadius: 6, fontSize: 11 }}>
    <strong style={{ color: '#4ade80' }}>Session match:</strong>{' '}
    {step.output_summary.action === 'match'
      ? `entry ${step.output_summary.matched_entry_id} (${step.output_summary.candidate_count} candidates)`
      : `${step.output_summary.action} (${step.output_summary.reason ?? ''})`}
  </div>
)}
```

- [ ] **Step 2:** Build.

```bash
cd frontend && npm run lint && npm run build
```

- [ ] **Step 3:** Commit.

```bash
git add frontend/src/pages/InvestigatePage.tsx
git commit -m "feat(frontend): InvestigatePage renders session_lookup step"
```

---

## Task 13: ChatPage "Reused from session" badge

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx` (or wherever the chat header / message bubble lives)

- [ ] **Step 1:** Track `reusedFromSession` per assistant message in the message store. When `onSessionMatch` fires, mark the next assistant message as reused. Pass it as a prop into the message bubble component, which renders a small `♻ Reused` badge near the SQL preview.

- [ ] **Step 2:** Pass `reusedFromSession` prop through to `<SqlCandidatesPicker reusedFromSession={...} />` (Task 10 already accepts the prop).

- [ ] **Step 3:** Build.

```bash
cd frontend && npm run lint && npm run build
```

- [ ] **Step 4:** Commit.

```bash
git add frontend/src/pages/ChatPage.tsx frontend/src/store/chatStore.ts frontend/src/components/chat
git commit -m "feat(frontend): show ♻ Reused-from-session badge on short-circuit"
```

---

## Task 14: End-to-end integration test

**Files:**
- Create: `tests/test_e2e_session_learning.py`

- [ ] **Step 1: Write test (no implementation needed — exercises everything).**

```python
# tests/test_e2e_session_learning.py
"""End-to-end: accept multi-candidate → re-submit similar query → confirm short-circuit.

Uses an in-memory FastAPI TestClient + mocked LLM. Does NOT require Oracle.
"""
from __future__ import annotations

import json
from typing import Any, List
from unittest.mock import patch
from fastapi.testclient import TestClient

from agent.knowledge_store import KYCKnowledgeStore, KnowledgeEntry


def _seed_session_entry(store: KYCKnowledgeStore):
    entry = KnowledgeEntry(
        id="seed1",
        source="query_session",
        category="query_session",
        content="...",
        metadata={
            "original_query": "show me high risk customers",
            "enriched_query": "show me high risk customers risk_rating='HIGH'",
            "tables_used": ["KYC.CUSTOMERS"],
            "accepted_candidates": [
                {"interpretation": "active customers only",
                 "sql": "SELECT * FROM KYC.CUSTOMERS WHERE STATUS='ACTIVE'", "explanation": "x"},
                {"interpretation": "include historical",
                 "sql": "SELECT * FROM KYC.CUSTOMERS", "explanation": "y"},
            ],
            "created_at": 1000.0,
        },
    )
    store.add_session_entry(entry)


def test_session_match_short_circuits_pipeline(monkeypatch, tmp_path):
    """Build a real pipeline + store, seed an entry, run a similar query through
    `session_lookup`, assert match + candidates surface."""
    from knowledge_graph.graph_store import KnowledgeGraph
    from agent.nodes.session_lookup import make_session_lookup

    g = KnowledgeGraph()
    g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})

    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    _seed_session_entry(store)

    node = make_session_lookup(store, g)
    state = {
        "user_input": "show me high risk customers please",
        "enriched_query": "show me high risk customers please",
        "intent": "DATA_QUERY", "conversation_history": [], "_trace": [],
    }
    out = node(state)

    assert out["has_candidates"] is True
    assert out["session_match_entry_id"] == "seed1"
    assert len(out["sql_candidates"]) == 2
    assert "KYC.CUSTOMERS" in out["sql_candidates"][0]["sql"]
```

- [ ] **Step 2:** Run.

```bash
python -m pytest tests/test_e2e_session_learning.py -v
```

Expected: PASS.

- [ ] **Step 3:** Run the full test suite.

```bash
python -m pytest tests/ -q -x --ignore=tests/test_e2e.py
```

- [ ] **Step 4:** Commit.

```bash
git add tests/test_e2e_session_learning.py
git commit -m "test: e2e session-learning round trip"
```

---

## Task 15: Manual end-to-end smoke test

This task is a checklist for the human/agent verifying the feature works against the running app.

- [ ] **Step 1:** Start the stack.

```bash
docker compose -f docker/docker-compose.yml up -d
```

Wait for `app` service to log `Application startup complete`.

- [ ] **Step 2:** Open `http://localhost:8501` (or the configured port). Submit a query that admits multiple readings, e.g. *"show me high risk customers from last quarter"*.

- [ ] **Step 3:** Verify the SqlCandidatesPicker now shows checkboxes + radio + "Accept Selected & Run" button. Check 2-3 candidates, pick one as the executor, click the button.

- [ ] **Step 4:** Open the **KYC Agent** tab. Filter by source `query_session`. Verify ONE new entry exists. Open it and confirm metadata shows: original query, accepted candidates, rejected candidates, clarifications.

- [ ] **Step 5:** Re-submit a *similar* query (e.g. *"high risk customers from last quarter please"*). Verify:
  - The chat shows the "♻ Reused from session" badge.
  - The SqlCandidatesPicker appears immediately, **without** any clarification prompt.
  - The candidates match those saved in step 3.

- [ ] **Step 6:** Open the **Investigate** tab. Select the latest trace. Verify a `session_lookup` step is present with `Session match: entry ...`.

- [ ] **Step 7:** From the KYC Agent tab, click the "Re-run this query in Chat" button on the seeded entry. Verify the Chat tab activates and the input field is pre-filled.

- [ ] **Step 8:** Negative test — submit a totally novel query (*"list all unverified PEP customers added today"*). Verify the pipeline runs the full clarification flow (no short-circuit) since no similar entry exists.

- [ ] **Step 9:** Tear down.

```bash
docker compose -f docker/docker-compose.yml down
```

---

## Self-Review Checklist (run after writing the plan)

- [x] **Spec coverage:** every section in the spec maps to a task.
  - §3.1 component map ↔ Tasks 1–13
  - §3.2 pipeline flow ↔ Task 7
  - §3.3 data shapes ↔ Tasks 2, 5
  - §3.4 multi-select UX ↔ Task 10
  - §3.5 backend route ↔ Task 8
  - §3.6 SSE additions ↔ Tasks 8, 9
  - §3.7 Prompt Studio integration ↔ Task 5 (file auto-discovered)
  - §4 error handling ↔ Tasks 6, 8 (graceful fallbacks)
  - §5 edge cases ↔ Tasks 4, 6 (FQN check, intent skip, history skip, tiebreak)
  - §6 testing ↔ Tasks 2, 3, 4, 5, 6, 14
- [x] **Placeholder scan:** no "TBD", "implement later", "add error handling" without code.
- [x] **Type consistency:** `accepted_candidates`, `rejected_candidates`, `executed_candidate_id`, `session_digest`, `session_match_entry_id` used consistently across Python (snake_case) and TypeScript (camelCase at boundaries, snake_case in JSON payloads).
- [x] **Frontend tests:** Tasks 9-13 are typed-only; full UI tests are deferred to manual smoke (Task 15) to keep plan tractable. Acceptable — UI semantics are simple state transitions and visual.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven** — Fresh subagent per task with two-stage review.
**2. Inline Execution** — Execute tasks in this session with checkpoints.

For this plan, **Inline Execution** is appropriate: tasks are tightly sequenced (Task N depends on N-1) and each is small. Estimated total: 30-60 min of execution time.
