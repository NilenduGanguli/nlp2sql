"""SQL skeleton normalizer — strips literals + normalizes whitespace + case-folds.

Used by the pattern aggregator to cluster queries that share structure but differ
in concrete values (e.g. WHERE risk='HIGH' vs WHERE risk='LOW').
"""
from __future__ import annotations

import re

_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_NUMERIC_LITERAL = re.compile(r"\b\d+(?:\.\d+)?\b")
_OPERATOR = re.compile(r"\s*(<>|!=|<=|>=|=|<|>)\s*")
_WHITESPACE = re.compile(r"\s+")


def sql_skeleton(sql: str) -> str:
    if not sql:
        return ""
    s = _STRING_LITERAL.sub("?", sql)
    s = _NUMERIC_LITERAL.sub("?", s)
    s = _OPERATOR.sub(r" \1 ", s)
    s = _WHITESPACE.sub(" ", s).strip()
    return s.lower()
