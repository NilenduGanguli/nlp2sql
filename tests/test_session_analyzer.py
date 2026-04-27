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
