"""
Tests for knowledge_graph.traversal
=====================================
Verifies that each traversal query function:
  1. Returns correctly structured Python dicts from the KnowledgeGraph
  2. Handles empty / missing nodes gracefully
  3. Applies uppercase normalisation on input FQNs
  4. Serializes DDL context correctly for LLM prompt injection

All tests use the pre-built kyc_graph fixture — no external database required.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import knowledge_graph.traversal as T
from knowledge_graph.graph_store import KnowledgeGraph
from knowledge_graph.traversal import serialize_context_to_ddl


# ---------------------------------------------------------------------------
# get_columns_for_table
# ---------------------------------------------------------------------------

class TestGetColumnsForTable:
    def test_returns_list(self, kyc_graph):
        result = T.get_columns_for_table(kyc_graph, "KYC.CUSTOMERS")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_lowercase_fqn_normalised(self, kyc_graph):
        result = T.get_columns_for_table(kyc_graph, "kyc.customers")
        assert len(result) > 0

    def test_columns_ordered_by_column_id(self, kyc_graph):
        result = T.get_columns_for_table(kyc_graph, "KYC.CUSTOMERS")
        ids = [c["column_id"] for c in result]
        assert ids == sorted(ids)

    def test_empty_table_returns_empty_list(self, kyc_graph):
        result = T.get_columns_for_table(kyc_graph, "KYC.NONEXISTENT_TABLE")
        assert result == []

    def test_expected_column_present(self, kyc_graph):
        result = T.get_columns_for_table(kyc_graph, "KYC.CUSTOMERS")
        names = {c["name"] for c in result}
        assert "CUSTOMER_ID" in names


# ---------------------------------------------------------------------------
# get_table_detail
# ---------------------------------------------------------------------------

class TestGetTableDetail:
    def test_returns_none_for_missing_table(self, kyc_graph):
        result = T.get_table_detail(kyc_graph, "KYC.NONEXISTENT")
        assert result is None

    def test_returns_dict_for_existing_table(self, kyc_graph):
        result = T.get_table_detail(kyc_graph, "KYC.CUSTOMERS")
        assert result is not None
        assert "table" in result
        assert "columns" in result
        assert "constraints" in result
        assert "foreign_keys" in result

    def test_table_has_correct_name(self, kyc_graph):
        result = T.get_table_detail(kyc_graph, "KYC.CUSTOMERS")
        assert result["table"]["name"] == "CUSTOMERS"

    def test_foreign_keys_present_for_accounts(self, kyc_graph):
        result = T.get_table_detail(kyc_graph, "KYC.ACCOUNTS")
        assert result is not None
        # ACCOUNTS.CUSTOMER_ID → CUSTOMERS.CUSTOMER_ID
        fk_cols = {fk["fk_col"] for fk in result["foreign_keys"]}
        assert "CUSTOMER_ID" in fk_cols


# ---------------------------------------------------------------------------
# find_join_path
# ---------------------------------------------------------------------------

class TestFindJoinPath:
    def test_returns_precomputed_path(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "KYC.ACCOUNTS", "KYC.CUSTOMERS")
        assert result is not None
        assert result["source"] == "precomputed"
        assert result["weight"] == 1

    def test_two_hop_path_found(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "KYC.TRANSACTIONS", "KYC.CUSTOMERS")
        assert result is not None
        assert result["weight"] == 2

    def test_returns_none_when_no_path(self, kyc_graph):
        # Build a small graph with isolated tables
        g = KnowledgeGraph()
        g.merge_node("Table", "S.TABLE_A", {"name": "TABLE_A", "schema": "S", "fqn": "S.TABLE_A"})
        g.merge_node("Table", "S.TABLE_B", {"name": "TABLE_B", "schema": "S", "fqn": "S.TABLE_B"})
        result = T.find_join_path(g, "S.TABLE_A", "S.TABLE_B")
        assert result is None

    def test_lowercase_fqn_normalised(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "kyc.accounts", "kyc.customers")
        assert result is not None


# ---------------------------------------------------------------------------
# resolve_business_term
# ---------------------------------------------------------------------------

class TestResolveBusinessTerm:
    def test_returns_list(self, kyc_graph):
        result = T.resolve_business_term(kyc_graph, "customer")
        assert isinstance(result, list)

    def test_fallback_name_search_works(self, kyc_graph):
        # "customer" appears in many table/column names
        result = T.resolve_business_term(kyc_graph, "customer")
        assert len(result) > 0

    def test_nonexistent_term_returns_empty_or_list(self, kyc_graph):
        result = T.resolve_business_term(kyc_graph, "xyzzy_nonexistent_term_abc")
        assert isinstance(result, list)

    def test_name_search_result_has_expected_keys(self, kyc_graph):
        results = T.resolve_business_term(kyc_graph, "customer")
        for r in results:
            assert "fqn" in r or "term" in r


# ---------------------------------------------------------------------------
# get_context_subgraph + serialize_context_to_ddl
# ---------------------------------------------------------------------------

class TestGetContextSubgraph:
    def test_returns_list(self, kyc_graph):
        result = T.get_context_subgraph(kyc_graph, ["KYC.CUSTOMERS"])
        assert isinstance(result, list)
        assert len(result) == 1

    def test_uppercase_fqns_normalised(self, kyc_graph):
        result = T.get_context_subgraph(kyc_graph, ["kyc.customers", "kyc.accounts"])
        assert len(result) == 2

    def test_context_contains_columns(self, kyc_graph):
        result = T.get_context_subgraph(kyc_graph, ["KYC.CUSTOMERS"])
        assert len(result[0]["columns"]) > 0

    def test_context_contains_foreign_keys(self, kyc_graph):
        result = T.get_context_subgraph(kyc_graph, ["KYC.ACCOUNTS"])
        fk_cols = {fk["fk_col"] for fk in result[0]["foreign_keys"]}
        assert "CUSTOMER_ID" in fk_cols

    def test_missing_table_omitted(self, kyc_graph):
        result = T.get_context_subgraph(kyc_graph, ["KYC.NONEXISTENT"])
        assert result == []


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
    def test_search_returns_list(self, kyc_graph):
        result = T.search_schema(kyc_graph, "customer")
        assert isinstance(result, list)

    def test_search_finds_table(self, kyc_graph):
        result = T.search_schema(kyc_graph, "CUSTOMERS")
        fqns = [r["fqn"] for r in result]
        assert "KYC.CUSTOMERS" in fqns

    def test_search_finds_column(self, kyc_graph):
        result = T.search_schema(kyc_graph, "RISK_RATING")
        names = [r["name"] for r in result]
        assert "RISK_RATING" in names

    def test_no_match_returns_empty(self, kyc_graph):
        result = T.search_schema(kyc_graph, "xyzzy_no_such_thing_abc123")
        assert result == []


# ---------------------------------------------------------------------------
# get_index_hints
# ---------------------------------------------------------------------------

class TestGetIndexHints:
    def test_uppercase_fqns(self, kyc_graph):
        result = T.get_index_hints(kyc_graph, ["kyc.customers.risk_rating"])
        assert isinstance(result, list)

    def test_returns_index_for_indexed_column(self, kyc_graph):
        result = T.get_index_hints(kyc_graph, ["KYC.CUSTOMERS.CUSTOMER_ID"])
        assert len(result) > 0
        assert any(r["column_fqn"] == "KYC.CUSTOMERS.CUSTOMER_ID" for r in result)

    def test_returns_empty_for_unindexed_fqn(self, kyc_graph):
        result = T.get_index_hints(kyc_graph, ["KYC.NONEXISTENT.COLUMN"])
        assert result == []


# ---------------------------------------------------------------------------
# list_all_tables
# ---------------------------------------------------------------------------

class TestListAllTables:
    def test_returns_all_kyc_tables(self, kyc_graph):
        result = T.list_all_tables(kyc_graph)
        assert len(result) == 8  # 8 KYC tables in fixture

    def test_schema_filter_applied(self, kyc_graph):
        result = T.list_all_tables(kyc_graph, schema="kyc")
        assert len(result) == 8

    def test_wrong_schema_returns_empty(self, kyc_graph):
        result = T.list_all_tables(kyc_graph, schema="NONEXISTENT")
        assert result == []

    def test_pagination_skip(self, kyc_graph):
        all_tables = T.list_all_tables(kyc_graph)
        page = T.list_all_tables(kyc_graph, skip=2, limit=3)
        assert len(page) == 3
        assert page[0]["fqn"] == all_tables[2]["fqn"]

    def test_results_sorted_by_name(self, kyc_graph):
        result = T.list_all_tables(kyc_graph)
        names = [r["name"] for r in result]
        assert names == sorted(names)
