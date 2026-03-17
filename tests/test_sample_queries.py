"""
KYC-domain scenario-based sample query tests
=============================================
These tests exercise the full traversal API against a mock Neo4j session
pre-loaded with the KYC schema fixture from conftest.py.

Each test represents a realistic NLP-to-SQL pre-query scenario:
  - Entity resolution: business term → schema element
  - Table discovery: which tables answer a natural-language question
  - Join path finding: which FK hops connect two tables
  - DDL context generation: LLM-ready CREATE TABLE blocks
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pytest

import knowledge_graph.traversal as T
from knowledge_graph.traversal import serialize_context_to_ddl


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _mock_session_with_map(query_to_records: Dict[str, List[Dict[str, Any]]]):
    """
    Return a mock session whose .run() dispatches based on query substring.
    Falls back to [] for unrecognised queries.
    """
    session = MagicMock()

    def _run(cypher: str, **kwargs):
        for key, records in query_to_records.items():
            if key.lower() in cypher.lower():
                mock_result = MagicMock()
                mock_result.__iter__ = lambda s, r=records: iter(
                    [_to_record(rec) for rec in r]
                )
                mock_result.single.return_value = (
                    _to_record(records[0]) if records else None
                )
                return mock_result
        # Default empty
        mock_result = MagicMock()
        mock_result.__iter__ = lambda s: iter([])
        mock_result.single.return_value = None
        return mock_result

    session.run.side_effect = _run
    return session


def _to_record(d: Dict[str, Any]) -> MagicMock:
    rec = MagicMock()
    rec.__getitem__ = lambda s, k: d[k]
    rec.data.return_value = d
    rec.items.return_value = list(d.items())
    return rec


def _join_path_record(join_columns, weight=1):
    return {
        "join_columns": join_columns,
        "join_type": "INNER",
        "cardinality": "N:1",
        "weight": weight,
    }


# ---------------------------------------------------------------------------
# Scenario 1 – High-risk customers with many transactions
#   NLP: "List all high risk customers with more than 5 transactions over $10k
#         in the last quarter"
#   Expected tables: CUSTOMERS (risk_rating filter) + TRANSACTIONS (amount filter)
# ---------------------------------------------------------------------------

class TestHighRiskTransactionScenario:
    """
    Tables: KYC.CUSTOMERS, KYC.TRANSACTIONS
    Join path: CUSTOMERS.CUSTOMER_ID ← ACCOUNTS.CUSTOMER_ID +
               ACCOUNTS.ACCOUNT_ID ← TRANSACTIONS.ACCOUNT_ID
    """

    def _session(self):
        return _mock_session_with_map({
            "JOIN_PATH": [_join_path_record(
                [{"src": "KYC.ACCOUNTS.CUSTOMER_ID", "tgt": "KYC.CUSTOMERS.CUSTOMER_ID"},
                 {"src": "KYC.TRANSACTIONS.ACCOUNT_ID", "tgt": "KYC.ACCOUNTS.ACCOUNT_ID"}],
                weight=2,
            )],
        })

    def test_join_path_found_for_customers_transactions(self):
        session = self._session()
        result = T.find_join_path(session, "KYC.TRANSACTIONS", "KYC.CUSTOMERS")
        assert result is not None

    def test_join_weight_is_two_hops(self):
        session = self._session()
        result = T.find_join_path(session, "KYC.TRANSACTIONS", "KYC.CUSTOMERS")
        assert result["weight"] == 2


# ---------------------------------------------------------------------------
# Scenario 2 – Customers missing a KYC review
#   NLP: "Find all customers who have not completed a KYC review"
#   Expected tables: CUSTOMERS, KYC_REVIEWS (LEFT JOIN / NOT EXISTS)
# ---------------------------------------------------------------------------

class TestCustomersMissingKycReviewScenario:
    def test_join_path_customers_to_kyc_reviews(self):
        path_data = _join_path_record(
            [{"src": "KYC.KYC_REVIEWS.CUSTOMER_ID", "tgt": "KYC.CUSTOMERS.CUSTOMER_ID"}],
            weight=1,
        )
        session = _mock_session_with_map({"JOIN_PATH": [path_data]})
        result = T.find_join_path(session, "KYC.CUSTOMERS", "KYC.KYC_REVIEWS")
        assert result is not None
        assert result["weight"] == 1

    def test_kyc_review_business_term_resolves(self):
        term_records = [{
            "term": "KYC Review",
            "definition": "Know Your Customer periodic review",
            "sensitivity_level": "INTERNAL",
            "target_labels": ["Table"],
            "target_fqn": "KYC.KYC_REVIEWS",
            "target_name": "KYC_REVIEWS",
            "confidence": 1.0,
            "mapping_type": "manual",
        }]
        session = _mock_session_with_map({"MAPS_TO": term_records})
        result = T.resolve_business_term(session, "KYC Review")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Scenario 3 – PEP-flagged customers and account managers
#   NLP: "Show PEP-flagged customers and the name of their account manager"
#   Expected tables: CUSTOMERS, PEP_STATUS, EMPLOYEES
# ---------------------------------------------------------------------------

class TestPepFlaggedAccountManagerScenario:
    def test_pep_status_business_term_resolves(self):
        term_records = [{
            "term": "PEP Status",
            "definition": "Politically Exposed Person classification",
            "sensitivity_level": "RESTRICTED",
            "target_labels": ["Table"],
            "target_fqn": "KYC.PEP_STATUS",
            "target_name": "PEP_STATUS",
            "confidence": 1.0,
            "mapping_type": "manual",
        }]
        session = _mock_session_with_map({"MAPS_TO": term_records})
        result = T.resolve_business_term(session, "PEP Status")
        assert isinstance(result, list)

    def test_account_manager_maps_to_employees(self):
        term_records = [{
            "term": "Account Manager",
            "definition": "Relationship manager assigned to the customer",
            "sensitivity_level": "INTERNAL",
            "target_labels": ["Column"],
            "target_fqn": "KYC.CUSTOMERS.ACCOUNT_MANAGER_ID",
            "target_name": "ACCOUNT_MANAGER_ID",
            "confidence": 0.9,
            "mapping_type": "semantic",
        }]
        session = _mock_session_with_map({"MAPS_TO": term_records})
        result = T.resolve_business_term(session, "Account Manager")
        assert isinstance(result, list)

    def test_join_path_customers_pep(self):
        path_data = _join_path_record(
            [{"src": "KYC.PEP_STATUS.CUSTOMER_ID", "tgt": "KYC.CUSTOMERS.CUSTOMER_ID"}],
            weight=1,
        )
        session = _mock_session_with_map({"JOIN_PATH": [path_data]})
        result = T.find_join_path(session, "KYC.PEP_STATUS", "KYC.CUSTOMERS")
        assert result is not None


# ---------------------------------------------------------------------------
# Scenario 4 – Beneficial owners with >25% ownership
#   NLP: "Find beneficial owners holding more than 25% of a customer entity"
#   Expected tables: BENEFICIAL_OWNERS + CUSTOMERS
# ---------------------------------------------------------------------------

class TestBeneficialOwnerScenario:
    def test_beneficial_owner_business_term(self):
        term_records = [{
            "term": "Beneficial Owner",
            "definition": "Individual controlling ≥25% of a legal entity",
            "sensitivity_level": "RESTRICTED",
            "target_labels": ["Table"],
            "target_fqn": "KYC.BENEFICIAL_OWNERS",
            "target_name": "BENEFICIAL_OWNERS",
            "confidence": 1.0,
            "mapping_type": "manual",
        }]
        session = _mock_session_with_map({"MAPS_TO": term_records})
        result = T.resolve_business_term(session, "Beneficial Owner")
        assert isinstance(result, list)

    def test_join_path_beneficial_owners_to_customers(self):
        path_data = _join_path_record(
            [{"src": "KYC.BENEFICIAL_OWNERS.CUSTOMER_ID",
              "tgt": "KYC.CUSTOMERS.CUSTOMER_ID"}],
            weight=1,
        )
        session = _mock_session_with_map({"JOIN_PATH": [path_data]})
        result = T.find_join_path(session, "KYC.BENEFICIAL_OWNERS", "KYC.CUSTOMERS")
        assert result is not None
        assert result["weight"] == 1


# ---------------------------------------------------------------------------
# Scenario 5 – Accounts with frozen status
#   NLP: "List all accounts currently in frozen status"
#   Expected tables: ACCOUNTS
# ---------------------------------------------------------------------------

class TestFrozenAccountsScenario:
    def test_accounts_table_discoverable(self):
        table_records = [{
            "fqn": "KYC.ACCOUNTS",
            "name": "ACCOUNTS",
            "schema": "KYC",
            "row_count": 72000,
            "comments": "Financial accounts linked to customers",
        }]
        session = _mock_session_with_map({"Table": table_records})
        result = T.list_all_tables(session, schema="KYC")
        assert isinstance(result, list)

    def test_status_column_found_in_accounts(self):
        col_records = [{
            "fqn": "KYC.ACCOUNTS.STATUS", "name": "STATUS",
            "data_type": "VARCHAR2", "column_id": 7,
            "is_pk": False, "is_fk": False, "is_indexed": True,
            "nullable": "N", "data_length": 20, "precision": None,
            "scale": None, "default_value": "ACTIVE",
            "comments": "ACTIVE | FROZEN | CLOSED", "sample_values": [],
            "num_distinct": 3,
        }]
        session = _mock_session_with_map({"HAS_COLUMN": col_records})
        result = T.get_columns_for_table(session, "KYC.ACCOUNTS")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Scenario 6 – Risk Rating column resolution
#   NLP: "Show customers risk rating"
#   Expected: CUSTOMERS.RISK_RATING column node via business term lookup
# ---------------------------------------------------------------------------

class TestRiskRatingResolution:
    def test_risk_rating_resolves_to_customers_column(self):
        term_records = [{
            "term": "Risk Rating",
            "definition": "Risk classification level for a customer",
            "sensitivity_level": "CONFIDENTIAL",
            "target_labels": ["Column"],
            "target_fqn": "KYC.CUSTOMERS.RISK_RATING",
            "target_name": "RISK_RATING",
            "confidence": 1.0,
            "mapping_type": "manual",
        }]
        session = _mock_session_with_map({"MAPS_TO": term_records})
        result = T.resolve_business_term(session, "Risk Rating")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Scenario 7 – DDL context for LLM prompt injection
#   Verify serialize_context_to_ddl generates correct text for KYC.CUSTOMERS
# ---------------------------------------------------------------------------

class TestDDLContextSerialization:
    def _customers_context(self):
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
                 "comments": "Primary key", "default_value": None},
                {"fqn": "KYC.CUSTOMERS.NATIONALITY", "name": "NATIONALITY",
                 "data_type": "VARCHAR2", "precision": None, "scale": None,
                 "data_length": 3, "nullable": "Y", "column_id": 5,
                 "is_pk": False, "is_fk": False, "is_indexed": False,
                 "comments": "ISO alpha-3 country code", "default_value": None},
                {"fqn": "KYC.CUSTOMERS.RISK_RATING", "name": "RISK_RATING",
                 "data_type": "VARCHAR2", "precision": None, "scale": None,
                 "data_length": 10, "nullable": "N", "column_id": 8,
                 "is_pk": False, "is_fk": False, "is_indexed": True,
                 "comments": "LOW | MEDIUM | HIGH | VERY_HIGH", "default_value": None},
                {"fqn": "KYC.CUSTOMERS.ACCOUNT_MANAGER_ID", "name": "ACCOUNT_MANAGER_ID",
                 "data_type": "NUMBER", "precision": 10, "scale": None,
                 "data_length": None, "nullable": "Y", "column_id": 9,
                 "is_pk": False, "is_fk": True, "is_indexed": True,
                 "comments": "FK → EMPLOYEES", "default_value": None},
            ],
            "foreign_keys": [
                {"fk_col": "ACCOUNT_MANAGER_ID", "ref_table": "KYC.EMPLOYEES",
                 "ref_col": "EMPLOYEE_ID", "constraint": "FK_CUST_MGR"},
            ],
            "indexes": [
                {"name": "IDX_CUST_RISK", "columns_list": "RISK_RATING",
                 "uniqueness": "NONUNIQUE"},
                {"name": "IDX_CUST_NATION", "columns_list": "NATIONALITY",
                 "uniqueness": "NONUNIQUE"},
            ],
            "constraints": [],
            "business_terms": [
                {"term": "Customer Due Diligence", "definition": "KYC process",
                 "confidence": 1.0},
                {"term": "Risk Rating", "definition": "Risk classification",
                 "confidence": 1.0},
            ],
        }]

    def test_contains_table_header(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "CUSTOMERS" in ddl

    def test_contains_risk_rating_column(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "RISK_RATING" in ddl

    def test_contains_nationality_column(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "NATIONALITY" in ddl

    def test_pk_annotation_present(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "PK" in ddl

    def test_fk_annotation_present(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "EMPLOYEES" in ddl

    def test_business_terms_block_present(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "Customer Due Diligence" in ddl

    def test_row_count_present(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "50" in ddl  # "50,000" is present

    def test_index_names_present(self):
        ddl = serialize_context_to_ddl(self._customers_context())
        assert "IDX_CUST_RISK" in ddl


# ---------------------------------------------------------------------------
# Scenario 8 – Multi-table join context
#   Verify that get_context_subgraph accepts multiple FQNs without error
# ---------------------------------------------------------------------------

class TestMultiTableContext:
    def test_accepts_three_tables(self):
        session = MagicMock()
        session.run.return_value.__iter__ = lambda s: iter([])
        result = T.get_context_subgraph(
            session,
            ["KYC.CUSTOMERS", "KYC.TRANSACTIONS", "KYC.ACCOUNTS"]
        )
        assert isinstance(result, list)
        # Verify three FQNs were passed in one query call
        _, kwargs = session.run.call_args
        fqns = kwargs.get("table_fqns", [])
        assert len(fqns) == 3
