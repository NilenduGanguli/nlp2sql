"""Tests for is_likely_enum_column heuristic — widened to catch KYC abbreviations."""
from __future__ import annotations

import pytest

from knowledge_graph.column_value_cache import is_likely_enum_column


@pytest.mark.parametrize("name, dtype, length", [
    # English enum words (existing behaviour)
    ("STATUS",        "VARCHAR2", 20),
    ("ACCOUNT_STATUS","VARCHAR2", 20),
    ("RISK_RATING",   "VARCHAR2", 10),
    ("CURRENCY",      "VARCHAR2", 3),
    # Abbreviation suffixes — NEW
    ("STS_CD",        "VARCHAR2", 5),
    ("RSK_LVL",       "VARCHAR2", 3),
    ("ACCT_TYP",      "VARCHAR2", 5),
    ("PAY_FLG",       "CHAR",     1),
    ("REASON_CD",     "VARCHAR2", 8),
    # Short string types — existing
    ("CODE",          "VARCHAR2", 5),
    ("FLAG",          "CHAR",     1),
])
def test_is_enum_positive(name, dtype, length):
    assert is_likely_enum_column(name, dtype, length) is True


@pytest.mark.parametrize("name, dtype, length, precision", [
    # Tiny numeric flags — NEW
    ("IS_ACTIVE",     "NUMBER", 0,  1),
    ("HAS_PEP",       "NUMBER", 0,  1),
    ("CAN_TRADE",     "NUMBER", 0,  1),
    ("PRIORITY_LVL",  "NUMBER", 0,  2),
])
def test_is_enum_positive_numeric(name, dtype, length, precision):
    assert is_likely_enum_column(name, dtype, length, precision) is True


@pytest.mark.parametrize("name, dtype, length, precision", [
    # High-cardinality identifiers
    ("CUSTOMER_ID",      "NUMBER",   0,   10),
    ("ACCOUNT_ID",       "NUMBER",   0,   12),
    ("FIRST_NAME",       "VARCHAR2", 100, 0),
    ("DESCRIPTION",      "VARCHAR2", 500, 0),
    ("EMAIL",            "VARCHAR2", 200, 0),
    # Long string columns
    ("REVIEW_NOTES",     "CLOB",     0,   0),
    # Numeric metrics
    ("AMOUNT",           "NUMBER",   0,   18),
    ("BALANCE",          "NUMBER",   0,   18),
    ("RISK_SCORE",       "NUMBER",   0,   5),
])
def test_is_enum_negative(name, dtype, length, precision):
    assert is_likely_enum_column(name, dtype, length, precision) is False
