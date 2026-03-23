"""
KYC-domain scenario-based sample query tests
=============================================
These tests exercise the full traversal API against the pre-built KyC
KnowledgeGraph from conftest.py.

Each test represents a realistic NLP-to-SQL pre-query scenario:
  - Entity resolution: business term → schema element
  - Table discovery: which tables answer a natural-language question
  - Join path finding: which FK hops connect two tables
  - DDL context generation: LLM-ready CREATE TABLE blocks
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import knowledge_graph.traversal as T
from knowledge_graph.graph_store import KnowledgeGraph
from knowledge_graph.traversal import serialize_context_to_ddl


# ---------------------------------------------------------------------------
# Helpers: build a KnowledgeGraph with specific BusinessTerm nodes
# ---------------------------------------------------------------------------

def _graph_with_term(term: str, definition: str, target_fqn: str,
                     sensitivity: str = "INTERNAL", confidence: float = 1.0,
                     mapping_type: str = "manual") -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.merge_node("BusinessTerm", term, {
        "term": term,
        "definition": definition,
        "aliases": [],
        "domain": "KYC",
        "sensitivity_level": sensitivity,
    })
    g.merge_edge("MAPS_TO", term, target_fqn,
                 confidence=confidence, mapping_type=mapping_type)
    return g


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

    def test_join_path_found_for_customers_transactions(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "KYC.TRANSACTIONS", "KYC.CUSTOMERS")
        assert result is not None

    def test_join_weight_is_two_hops(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "KYC.TRANSACTIONS", "KYC.CUSTOMERS")
        assert result["weight"] == 2


# ---------------------------------------------------------------------------
# Scenario 2 – Customers missing a KYC review
#   NLP: "Find all customers who have not completed a KYC review"
#   Expected tables: CUSTOMERS, KYC_REVIEWS (LEFT JOIN / NOT EXISTS)
# ---------------------------------------------------------------------------

class TestCustomersMissingKycReviewScenario:
    def test_join_path_customers_to_kyc_reviews(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "KYC.CUSTOMERS", "KYC.KYC_REVIEWS")
        assert result is not None
        assert result["weight"] == 1

    def test_kyc_review_business_term_resolves(self):
        g = _graph_with_term(
            "KYC Review", "Know Your Customer periodic review",
            "KYC.KYC_REVIEWS", mapping_type="manual",
        )
        result = T.resolve_business_term(g, "KYC Review")
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["term"] == "KYC Review"


# ---------------------------------------------------------------------------
# Scenario 3 – PEP-flagged customers and account managers
#   NLP: "Show PEP-flagged customers and the name of their account manager"
#   Expected tables: CUSTOMERS, PEP_STATUS, EMPLOYEES
# ---------------------------------------------------------------------------

class TestPepFlaggedAccountManagerScenario:
    def test_pep_status_business_term_resolves(self):
        g = _graph_with_term(
            "PEP Status", "Politically Exposed Person classification",
            "KYC.PEP_STATUS", sensitivity="RESTRICTED",
        )
        result = T.resolve_business_term(g, "PEP Status")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_account_manager_maps_to_employees(self):
        g = _graph_with_term(
            "Account Manager", "Relationship manager assigned to the customer",
            "KYC.CUSTOMERS.ACCOUNT_MANAGER_ID", confidence=0.9, mapping_type="semantic",
        )
        result = T.resolve_business_term(g, "Account Manager")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_join_path_customers_pep(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "KYC.PEP_STATUS", "KYC.CUSTOMERS")
        assert result is not None


# ---------------------------------------------------------------------------
# Scenario 4 – Beneficial owners with >25% ownership
#   NLP: "Find beneficial owners holding more than 25% of a customer entity"
#   Expected tables: BENEFICIAL_OWNERS + CUSTOMERS
# ---------------------------------------------------------------------------

class TestBeneficialOwnerScenario:
    def test_beneficial_owner_business_term(self):
        g = _graph_with_term(
            "Beneficial Owner", "Individual controlling ≥25% of a legal entity",
            "KYC.BENEFICIAL_OWNERS", sensitivity="RESTRICTED",
        )
        result = T.resolve_business_term(g, "Beneficial Owner")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_join_path_beneficial_owners_to_customers(self, kyc_graph):
        result = T.find_join_path(kyc_graph, "KYC.BENEFICIAL_OWNERS", "KYC.CUSTOMERS")
        assert result is not None
        assert result["weight"] == 1


# ---------------------------------------------------------------------------
# Scenario 5 – Accounts with frozen status
#   NLP: "List all accounts currently in frozen status"
#   Expected tables: ACCOUNTS
# ---------------------------------------------------------------------------

class TestFrozenAccountsScenario:
    def test_accounts_table_discoverable(self, kyc_graph):
        result = T.list_all_tables(kyc_graph, schema="KYC")
        fqns = {r["fqn"] for r in result}
        assert "KYC.ACCOUNTS" in fqns

    def test_status_column_found_in_accounts(self, kyc_graph):
        result = T.get_columns_for_table(kyc_graph, "KYC.ACCOUNTS")
        names = {c["name"] for c in result}
        assert "STATUS" in names


# ---------------------------------------------------------------------------
# Scenario 6 – Risk Rating column resolution
#   NLP: "Show customers risk rating"
#   Expected: CUSTOMERS.RISK_RATING column node via business term lookup
# ---------------------------------------------------------------------------

class TestRiskRatingResolution:
    def test_risk_rating_resolves_to_customers_column(self):
        g = _graph_with_term(
            "Risk Rating", "Risk classification level for a customer",
            "KYC.CUSTOMERS.RISK_RATING", sensitivity="CONFIDENTIAL",
        )
        result = T.resolve_business_term(g, "Risk Rating")
        assert isinstance(result, list)
        assert result[0]["target_fqn"] == "KYC.CUSTOMERS.RISK_RATING"


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
    def test_accepts_three_tables(self, kyc_graph):
        result = T.get_context_subgraph(
            kyc_graph,
            ["KYC.CUSTOMERS", "KYC.TRANSACTIONS", "KYC.ACCOUNTS"]
        )
        assert isinstance(result, list)
        assert len(result) == 3

    def test_fqns_normalised_to_uppercase(self, kyc_graph):
        result = T.get_context_subgraph(
            kyc_graph,
            ["kyc.customers", "kyc.transactions", "kyc.accounts"]
        )
        assert len(result) == 3
