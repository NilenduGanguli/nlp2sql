"""
Tests for knowledge_graph.traversal
=====================================
Verifies that each Cypher query function:
  1. Passes the correct parameters to the Neo4j session
  2. Returns well-structured Python dicts
  3. Handles empty results gracefully
  4. Serializes DDL context correctly for LLM prompt injection

All tests use Mock Neo4j sessions — no live database required.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

import knowledge_graph.traversal as T
from knowledge_graph.traversal import serialize_context_to_ddl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_session(records: List[Dict[str, Any]] = None, single: Any = None):
    """
    Return a mock neo4j.Session.
    - records: iterable returned by result.__iter__
    - single: value returned by result.single()
    """
    session = MagicMock()

    mock_result = MagicMock()

    if records is not None:
        mock_result.__iter__ = lambda s: iter(
            [_dict_to_record(r) for r in records]
        )
    else:
        mock_result.__iter__ = lambda s: iter([])

    mock_result.single.return_value = (
        _dict_to_record(single) if single else None
    )

    session.run.return_value = mock_result
    return session


def _dict_to_record(d: Dict[str, Any]) -> MagicMock:
    """Simulate a neo4j Record as a MagicMock that supports dict()."""
    record = MagicMock()
    record.__iter__ = lambda s: iter(d.items())
    record.keys.return_value = list(d.keys())
    record.__getitem__ = lambda s, k: d[k]
    record.data.return_value = d
    # Make dict(record) work by implementing items()
    record.items.return_value = list(d.items())
    return record


# ---------------------------------------------------------------------------
# get_columns_for_table
# ---------------------------------------------------------------------------

class TestGetColumnsForTable:
    def test_passes_fqn_uppercase(self):
        session = _mock_session(records=[])
        T.get_columns_for_table(session, "kyc.customers")
        call_kwargs = session.run.call_args
        assert call_kwargs[1]["table_fqn"] == "KYC.CUSTOMERS"

    def test_returns_list(self):
        records = [
            {"fqn": "KYC.CUSTOMERS.CUSTOMER_ID", "name": "CUSTOMER_ID",
             "data_type": "NUMBER", "column_id": 1, "is_pk": True,
             "is_fk": False, "is_indexed": True, "nullable": "N",
             "data_length": None, "precision": 10, "scale": None,
             "default_value": None, "comments": "PK",
             "sample_values": [], "num_distinct": 50000},
        ]
        session = _mock_session(records=records)
        result = T.get_columns_for_table(session, "KYC.CUSTOMERS")
        assert isinstance(result, list)

    def test_empty_table_returns_empty_list(self):
        session = _mock_session(records=[])
        result = T.get_columns_for_table(session, "KYC.UNKNOWN_TABLE")
        assert result == []


# ---------------------------------------------------------------------------
# get_table_detail
# ---------------------------------------------------------------------------

class TestGetTableDetail:
    def test_returns_none_for_missing_table(self):
        session = _mock_session()
        session.run.return_value.single.return_value = None
        result = T.get_table_detail(session, "KYC.NONEXISTENT")
        assert result is None

    def test_returns_dict_for_existing_table(self):
        table_mock = MagicMock()
        table_mock.__iter__ = lambda s: iter({
            "fqn": "KYC.CUSTOMERS", "name": "CUSTOMERS", "schema": "KYC",
            "row_count": 50000, "comments": "Core customer entity"
        }.items())
        record = MagicMock()
        record.__getitem__ = lambda s, k: {
            "table": table_mock,
            "columns": [],
            "constraints": [],
            "foreign_keys": [],
        }[k]
        session = MagicMock()
        session.run.return_value.single.return_value = record
        result = T.get_table_detail(session, "KYC.CUSTOMERS")
        assert result is not None
        assert "table" in result
        assert "columns" in result


# ---------------------------------------------------------------------------
# find_join_path
# ---------------------------------------------------------------------------

class TestFindJoinPath:
    def test_returns_precomputed_path(self):
        session = MagicMock()
        precomputed = MagicMock()
        precomputed.__getitem__ = lambda s, k: {
            "join_columns": [{"src": "KYC.ACCOUNTS.CUSTOMER_ID",
                                "tgt": "KYC.CUSTOMERS.CUSTOMER_ID"}],
            "join_type": "INNER",
            "cardinality": "N:1",
            "weight": 1,
        }[k]
        session.run.return_value.single.return_value = precomputed
        result = T.find_join_path(session, "KYC.ACCOUNTS", "KYC.CUSTOMERS")
        assert result is not None
        assert result["weight"] == 1
        assert result["source"] == "precomputed"

    def test_returns_none_when_no_path(self):
        session = MagicMock()
        session.run.return_value.single.return_value = None
        result = T.find_join_path(session, "KYC.TABLE_X", "KYC.TABLE_Y")
        assert result is None

    def test_uppercase_fqn_passed(self):
        session = MagicMock()
        session.run.return_value.single.return_value = None
        T.find_join_path(session, "kyc.accounts", "kyc.customers")
        # Both queries should be called with upper-case FQNs
        for call in session.run.call_args_list:
            args, kwargs = call
            if "table1_fqn" in kwargs:
                assert kwargs["table1_fqn"] == "KYC.ACCOUNTS"
                assert kwargs["table2_fqn"] == "KYC.CUSTOMERS"


# ---------------------------------------------------------------------------
# resolve_business_term
# ---------------------------------------------------------------------------

class TestResolveBusinessTerm:
    def test_glossary_match_returned_first(self):
        records = [
            {"term": "Risk Rating", "definition": "Risk classification",
             "sensitivity_level": "CONFIDENTIAL",
             "target_labels": ["Column"], "target_fqn": "KYC.CUSTOMERS.RISK_RATING",
             "target_name": "RISK_RATING", "confidence": 1.0, "mapping_type": "manual"},
        ]
        session = _mock_session(records=records)
        result = T.resolve_business_term(session, "Risk Rating")
        assert len(result) >= 1
        assert result[0]["term"] == "Risk Rating"

    def test_empty_glossary_falls_back_to_name_search(self):
        session = MagicMock()
        session.run.return_value.__iter__ = lambda s: iter([])
        session.run.return_value.single.return_value = None
        # Second call to fallback name search
        result = T.resolve_business_term(session, "obscure_term")
        # Should not raise; returns list (possibly empty)
        assert isinstance(result, list)

    def test_regex_pattern_built_from_term(self):
        session = _mock_session(records=[])
        T.resolve_business_term(session, "Customer")
        # The pattern should contain the original term
        call_args = session.run.call_args_list[0]
        _, kwargs = call_args
        assert "customer" in kwargs.get("search_pattern", "").lower()


# ---------------------------------------------------------------------------
# get_context_subgraph + serialize_context_to_ddl
# ---------------------------------------------------------------------------

class TestGetContextSubgraph:
    def _make_context_record(self):
        table_node = MagicMock()
        table_node.__iter__ = lambda s: iter({
            "fqn": "KYC.CUSTOMERS",
            "name": "CUSTOMERS",
            "schema": "KYC",
            "row_count": 50000,
            "comments": "Core customer entity",
        }.items())

        col_node = MagicMock()
        col_node.__iter__ = lambda s: iter({
            "fqn": "KYC.CUSTOMERS.CUSTOMER_ID",
            "name": "CUSTOMER_ID",
            "data_type": "NUMBER",
            "data_length": None,
            "precision": 10,
            "scale": None,
            "nullable": "N",
            "column_id": 1,
            "is_pk": True,
            "is_fk": False,
            "is_indexed": True,
            "comments": "Primary key",
            "default_value": None,
            "sample_values": ["1001", "1002"],
        }.items())

        record = MagicMock()
        record.__getitem__ = lambda s, k: {
            "table_node": table_node,
            "columns": [col_node],
            "foreign_keys": [],
            "indexes": [],
            "constraints": [],
            "business_terms": [],
        }[k]
        return record

    def test_returns_list(self):
        session = MagicMock()
        session.run.return_value.__iter__ = lambda s: iter(
            [self._make_context_record()]
        )
        result = T.get_context_subgraph(session, ["KYC.CUSTOMERS"])
        assert isinstance(result, list)

    def test_uppercase_fqns_passed(self):
        session = MagicMock()
        session.run.return_value.__iter__ = lambda s: iter([])
        T.get_context_subgraph(session, ["kyc.customers", "kyc.accounts"])
        _, kwargs = session.run.call_args
        fqns = kwargs.get("table_fqns", [])
        assert "KYC.CUSTOMERS" in fqns
        assert "KYC.ACCOUNTS" in fqns


class TestSerializeContextToDDL:
    def _sample_context(self):
        return [{
            "table": {
                "fqn": "KYC.CUSTOMERS",
                "name": "CUSTOMERS",
                "schema": "KYC",
                "row_count": 50000,
                "comments": "Core customer entity for KYC compliance",
            },
            "columns": [
                {"fqn": "KYC.CUSTOMERS.CUSTOMER_ID", "name": "CUSTOMER_ID",
                 "data_type": "NUMBER", "precision": 10, "scale": None,
                 "data_length": None, "nullable": "N", "column_id": 1,
                 "is_pk": True, "is_fk": False, "is_indexed": True,
                 "comments": "Unique customer identifier", "default_value": None},
                {"fqn": "KYC.CUSTOMERS.RISK_RATING", "name": "RISK_RATING",
                 "data_type": "VARCHAR2", "precision": None, "scale": None,
                 "data_length": 10, "nullable": "N", "column_id": 8,
                 "is_pk": False, "is_fk": False, "is_indexed": True,
                 "comments": "LOW | MEDIUM | HIGH | VERY_HIGH", "default_value": None},
            ],
            "foreign_keys": [
                {"fk_col": "ACCOUNT_MANAGER_ID", "ref_table": "EMPLOYEES",
                 "ref_col": "EMPLOYEE_ID", "constraint": "FK_CUST_MGR"}
            ],
            "indexes": [
                {"name": "IDX_CUST_RISK", "columns_list": "RISK_RATING", "uniqueness": "NONUNIQUE"}
            ],
            "constraints": [],
            "business_terms": [
                {"term": "Customer Due Diligence", "definition": "KYC verification process",
                 "confidence": 1.0}
            ],
        }]

    def test_ddl_contains_table_name(self):
        ddl = serialize_context_to_ddl(self._sample_context())
        assert "KYC.CUSTOMERS" in ddl

    def test_ddl_contains_create_table(self):
        ddl = serialize_context_to_ddl(self._sample_context())
        assert "CREATE TABLE" in ddl

    def test_ddl_contains_pk_annotation(self):
        ddl = serialize_context_to_ddl(self._sample_context())
        assert "PK" in ddl

    def test_ddl_contains_fk_annotation(self):
        ddl = serialize_context_to_ddl(self._sample_context())
        assert "FK:" in ddl

    def test_ddl_contains_index(self):
        ddl = serialize_context_to_ddl(self._sample_context())
        assert "INDEX" in ddl

    def test_ddl_contains_business_term(self):
        ddl = serialize_context_to_ddl(self._sample_context())
        assert "Customer Due Diligence" in ddl

    def test_ddl_contains_row_count(self):
        ddl = serialize_context_to_ddl(self._sample_context())
        assert "50,000" in ddl

    def test_empty_context_returns_empty_string(self):
        ddl = serialize_context_to_ddl([])
        assert ddl.strip() == ""

    def test_varchar2_with_length(self):
        from knowledge_graph.traversal import _format_data_type
        result = _format_data_type({"data_type": "VARCHAR2", "data_length": 100})
        assert result == "VARCHAR2(100)"

    def test_number_with_precision_and_scale(self):
        from knowledge_graph.traversal import _format_data_type
        result = _format_data_type({"data_type": "NUMBER", "precision": 18, "scale": 2})
        assert result == "NUMBER(18,2)"


# ---------------------------------------------------------------------------
# search_schema
# ---------------------------------------------------------------------------

class TestSearchSchema:
    def test_search_returns_list(self):
        session = _mock_session(records=[])
        result = T.search_schema(session, "customer")
        assert isinstance(result, list)

    def test_search_falls_back_on_fulltext_error(self):
        session = MagicMock()
        # First call raises (fulltext index not available), second works
        fallback_result = MagicMock()
        fallback_result.__iter__ = lambda s: iter([])
        session.run.side_effect = [Exception("No fulltext index"), fallback_result]
        result = T.search_schema(session, "customer")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_index_hints
# ---------------------------------------------------------------------------

class TestGetIndexHints:
    def test_uppercase_fqns(self):
        session = _mock_session(records=[])
        T.get_index_hints(session, ["kyc.customers.risk_rating"])
        _, kwargs = session.run.call_args
        assert "KYC.CUSTOMERS.RISK_RATING" in kwargs.get("column_fqns", [])

    def test_returns_list(self):
        session = _mock_session(records=[])
        result = T.get_index_hints(session, ["KYC.CUSTOMERS.RISK_RATING"])
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# list_all_tables
# ---------------------------------------------------------------------------

class TestListAllTables:
    def test_schema_filter_applied(self):
        session = _mock_session(records=[])
        T.list_all_tables(session, schema="kyc", skip=0, limit=50)
        _, kwargs = session.run.call_args
        assert kwargs.get("schema") == "KYC"

    def test_no_schema_filter_passes_none(self):
        session = _mock_session(records=[])
        T.list_all_tables(session)
        _, kwargs = session.run.call_args
        assert kwargs.get("schema") is None
