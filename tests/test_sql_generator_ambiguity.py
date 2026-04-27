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
