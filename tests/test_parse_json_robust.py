"""Regression tests for the LLM JSON parser.

Triggered by Gemini 2.0 Flash emitting two JSON objects back-to-back
("Extra data: line N column M" — the parser must take only the first).
"""
from knowledge_graph.llm_enhancer import _parse_json_robust


def test_parses_plain_object():
    assert _parse_json_robust('{"a": 1}') == {"a": 1}


def test_parses_plain_array():
    assert _parse_json_robust('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_strips_thinking_tags():
    assert _parse_json_robust('<thinking>plan</thinking>{"x": 2}') == {"x": 2}


def test_strips_code_fence():
    assert _parse_json_robust('text\n```json\n{"x": 3}\n```\nmore') == {"x": 3}


def test_handles_trailing_commas():
    assert _parse_json_robust('{"a": [1, 2,], "b": 3,}') == {"a": [1, 2], "b": 3}


def test_handles_back_to_back_objects():
    """Gemini 2.0 Flash sometimes emits JSON+JSON. Take the first."""
    raw = '{"entries": [{"title": "A"}]}\n{"entries": [{"title": "B"}]}'
    parsed = _parse_json_robust(raw)
    assert parsed == {"entries": [{"title": "A"}]}


def test_handles_back_to_back_arrays():
    raw = '[{"a": 1}]\n[{"b": 2}]'
    assert _parse_json_robust(raw) == [{"a": 1}]


def test_handles_prose_before_and_after():
    raw = "Here is the data:\n{\"a\": 1}\nHope that helps!"
    assert _parse_json_robust(raw) == {"a": 1}


def test_raises_when_no_json():
    import pytest
    with pytest.raises(ValueError):
        _parse_json_robust("no json here at all")
