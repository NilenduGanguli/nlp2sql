import pytest


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
    pass
