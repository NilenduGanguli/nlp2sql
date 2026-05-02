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


def test_three_examples_all_appear_sorted():
    captured = {}

    def fake_invoke(messages):
        captured["messages"] = messages
        return _FakeResp("```sql\nSELECT 1 FROM DUAL\n```\n```explanation\nx\n```")

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(side_effect=fake_invoke)
    node = make_sql_generator(fake_llm)

    examples = [
        {"score": 0.66, "description": "TOP example", "why_this_sql": "",
         "sql": "SELECT 1", "key_concepts": [], "tags": []},
        {"score": 0.55, "description": "MID example", "why_this_sql": "",
         "sql": "SELECT 2", "key_concepts": [], "tags": []},
        {"score": 0.45, "description": "BOT example", "why_this_sql": "",
         "sql": "SELECT 3", "key_concepts": [], "tags": []},
    ]
    state = {
        "user_input": "x",
        "schema_context": "x",
        "conversation_history": [],
        "validation_errors": [],
        "retry_count": 0,
        "intent": "DATA_QUERY",
        "_trace": [],
        "accepted_examples": examples,
    }
    node(state)
    msg = captured["messages"][1].content
    # All three present
    assert "TOP example" in msg
    assert "MID example" in msg
    assert "BOT example" in msg
    # Sort order — TOP appears before MID appears before BOT
    assert msg.index("TOP example") < msg.index("MID example") < msg.index("BOT example")
