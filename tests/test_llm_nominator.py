"""Tests for nominate_filter_candidates_llm — LLM pass over heuristic-missed columns."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from knowledge_graph.llm_enhancer import nominate_filter_candidates_llm
from knowledge_graph.value_cache_builder import mark_filter_candidates_heuristic


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


def _fake_llm(response_content: str):
    fake = MagicMock()
    fake.invoke = MagicMock(return_value=_FakeLLMResponse(response_content))
    return fake


def test_nominate_skips_already_flagged_columns(kyc_graph):
    mark_filter_candidates_heuristic(kyc_graph)
    seen_columns_in_prompts = []

    def capture_invoke(messages):
        for m in messages:
            seen_columns_in_prompts.append(getattr(m, "content", str(m)))
        return _FakeLLMResponse(json.dumps({"candidates": []}))

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(side_effect=capture_invoke)

    nominate_filter_candidates_llm(kyc_graph, fake_llm, batch_size=50)

    flagged_fqns = {
        col["fqn"]
        for col in kyc_graph.get_all_nodes("Column")
        if col.get("filter_reason", "").startswith("heuristic:")
    }
    full_text = "\n".join(seen_columns_in_prompts)
    for fqn in flagged_fqns:
        assert fqn not in full_text, f"Heuristic-flagged {fqn} sent to LLM"


def test_nominate_flags_llm_accepted_columns(kyc_graph):
    # Clear all flags first
    for col in kyc_graph.get_all_nodes("Column"):
        if col.get("is_filter_candidate"):
            kyc_graph.merge_node("Column", col["fqn"], {
                "is_filter_candidate": False,
                "filter_reason": None,
            })

    fake_llm = _fake_llm(json.dumps({
        "candidates": [
            {"col_fqn": "KYC.EMPLOYEES.DEPARTMENT",
             "is_filter_candidate": True,
             "confidence": "HIGH",
             "reason": "department list is small and bounded"},
        ]
    }))

    n = nominate_filter_candidates_llm(kyc_graph, fake_llm, batch_size=50)
    assert n >= 1
    node = kyc_graph.get_node("Column", "KYC.EMPLOYEES.DEPARTMENT")
    assert node.get("is_filter_candidate") is True
    assert node.get("filter_reason", "").startswith("llm:")


def test_nominate_handles_llm_error_gracefully(kyc_graph):
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(side_effect=RuntimeError("LLM down"))
    n = nominate_filter_candidates_llm(kyc_graph, fake_llm, batch_size=50)
    assert n == 0


def test_nominate_returns_zero_when_llm_is_none(kyc_graph):
    n = nominate_filter_candidates_llm(kyc_graph, None, batch_size=50)
    assert n == 0
