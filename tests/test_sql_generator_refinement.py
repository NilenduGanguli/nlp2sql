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
        captured_prompts.append(messages[-1].content if hasattr(messages[-1], "content") else str(messages))
        m = MagicMock()
        m.content = "```sql\nSELECT * FROM CUSTOMERS WHERE STATUS = 'ACTIVE' AND created_at > SYSDATE - 90\n```\n```explanation\nfilter recent\n```"
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

    joined = " ".join(captured_prompts)
    assert "SELECT * FROM CUSTOMERS WHERE STATUS = 'ACTIVE'" in joined
    assert "modify" in joined.lower() or "refine" in joined.lower()
    assert out["generated_sql"]
    assert any(t.get("output_summary", {}).get("refinement_mode") for t in out["_trace"])
