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
