"""
Tests for knowledge_graph.glossary_loader.InferredGlossaryBuilder
==================================================================
Verifies:
  - BusinessTerm labels are humanized correctly from column/table names
  - Definitions come from DBA_COL_COMMENTS / DBA_TAB_COMMENTS where present
  - Sample values are embedded in the definition for categorical columns
  - Sensitivity is inferred correctly from column name tokens
  - Structural columns are not turned into business terms
  - MAPS_TO edges are created for every column / table
  - Deduplication: only the highest-confidence definition is kept per term
  - Table-level terms are generated when a table has a comment
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from knowledge_graph.glossary_loader import (
    InferredGlossaryBuilder,
    _humanize,
    _infer_sensitivity,
    _build_definition,
)
from knowledge_graph.graph_store import KnowledgeGraph
from knowledge_graph.oracle_extractor import OracleMetadata
from knowledge_graph.models import ColumnNode, TableNode


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestHumanize:
    def test_simple_snake_case(self):
        assert _humanize("RISK_RATING") == "Risk Rating"

    def test_abbreviation_preserved(self):
        # ID is in _ABBREVIATIONS
        assert _humanize("CUSTOMER_ID") == "Customer ID"

    def test_kyc_abbreviation(self):
        assert _humanize("KYC_STATUS") == "KYC Status"

    def test_single_word(self):
        assert _humanize("NATIONALITY") == "Nationality"

    def test_pep_abbreviation(self):
        assert _humanize("PEP_FLAG") == "PEP Flag"

    def test_doc_expiry(self):
        assert _humanize("DOC_EXPIRY_DATE") == "DOC Expiry Date"


class TestInferSensitivity:
    def test_password_is_restricted(self):
        assert _infer_sensitivity("USER_PASSWORD") == "RESTRICTED"

    def test_dob_is_restricted(self):
        assert _infer_sensitivity("DATE_OF_BIRTH") == "RESTRICTED"

    def test_balance_is_confidential(self):
        assert _infer_sensitivity("ACCOUNT_BALANCE") == "CONFIDENTIAL"

    def test_risk_is_confidential(self):
        assert _infer_sensitivity("RISK_RATING") == "CONFIDENTIAL"

    def test_name_is_internal(self):
        assert _infer_sensitivity("FIRST_NAME") == "INTERNAL"

    def test_pep_is_confidential(self):
        assert _infer_sensitivity("IS_PEP") == "CONFIDENTIAL"


class TestBuildDefinition:
    def test_uses_comment_when_present(self):
        defn, confidence = _build_definition(
            col_name="RISK_RATING",
            comment="Customer risk classification",
            sample_values=None,
            num_distinct=None,
            table_comment=None,
            table_fqn="KYC.CUSTOMERS",
        )
        assert defn == "Customer risk classification"
        assert confidence == 0.95

    def test_uses_table_comment_fallback(self):
        defn, confidence = _build_definition(
            col_name="STATUS",
            comment=None,
            sample_values=None,
            num_distinct=None,
            table_comment="Core customer entity",
            table_fqn="KYC.CUSTOMERS",
        )
        assert "core customer entity" in defn.lower()
        assert confidence == 0.65

    def test_falls_back_to_table_fqn(self):
        defn, confidence = _build_definition(
            col_name="STATUS",
            comment=None,
            sample_values=None,
            num_distinct=None,
            table_comment=None,
            table_fqn="KYC.CUSTOMERS",
        )
        assert "KYC.CUSTOMERS" in defn
        assert confidence == 0.50

    def test_categorical_values_appended(self):
        defn, confidence = _build_definition(
            col_name="RISK_RATING",
            comment="Risk level",
            sample_values=["LOW", "MEDIUM", "HIGH", "VERY_HIGH"],
            num_distinct=4,
            table_comment=None,
            table_fqn="KYC.CUSTOMERS",
        )
        assert "LOW" in defn
        assert "VERY_HIGH" in defn
        assert "Valid values:" in defn

    def test_high_cardinality_not_enumerated(self):
        defn, _ = _build_definition(
            col_name="CUSTOMER_ID",
            comment="Unique customer identifier",
            sample_values=["1001", "1002", "1003"],
            num_distinct=50000,  # >> _CATEGORICAL_THRESHOLD
            table_comment=None,
            table_fqn="KYC.CUSTOMERS",
        )
        assert "Valid values:" not in defn


# ---------------------------------------------------------------------------
# InferredGlossaryBuilder integration tests
# ---------------------------------------------------------------------------

def _make_metadata(
    columns: List[ColumnNode] = None,
    tables: List[TableNode] = None,
) -> OracleMetadata:
    meta = OracleMetadata()
    meta.tables = tables or []
    meta.columns = columns or []
    return meta


def _make_graph() -> KnowledgeGraph:
    return KnowledgeGraph()


class TestInferredGlossaryBuilderTerms:
    def test_column_with_comment_becomes_term(self):
        col = ColumnNode("KYC", "CUSTOMERS", "RISK_RATING", "VARCHAR2",
                         comments="Customer risk classification",
                         num_distinct=4,
                         sample_values=["LOW", "MEDIUM", "HIGH", "VERY_HIGH"])
        meta = _make_metadata(columns=[col])
        graph = _make_graph()

        stats = InferredGlossaryBuilder(graph).build(meta)

        assert stats["terms"] == 1
        assert stats["mappings"] == 1

        term = graph.get_node("BusinessTerm", "Risk Rating")
        assert term is not None
        assert "risk classification" in term["definition"].lower()

    def test_categorical_values_in_definition(self):
        col = ColumnNode("KYC", "CUSTOMERS", "RISK_RATING", "VARCHAR2",
                         comments="Risk level",
                         num_distinct=4,
                         sample_values=["LOW", "MEDIUM", "HIGH", "VERY_HIGH"])
        meta = _make_metadata(columns=[col])
        graph = _make_graph()

        InferredGlossaryBuilder(graph).build(meta)

        term = graph.get_node("BusinessTerm", "Risk Rating")
        assert "LOW" in term["definition"]

    def test_structural_column_skipped(self):
        col = ColumnNode("KYC", "CUSTOMERS", "ID", "NUMBER")  # in _SKIP_PURE_NAMES
        meta = _make_metadata(columns=[col])
        graph = _make_graph()

        stats = InferredGlossaryBuilder(graph).build(meta)

        assert stats["terms"] == 0
        assert stats["mappings"] == 0
        assert graph.count_nodes("BusinessTerm") == 0

    def test_sensitivity_written_for_confidential_column(self):
        col = ColumnNode("KYC", "CUSTOMERS", "ACCOUNT_BALANCE", "NUMBER",
                         comments="Balance of the account")
        meta = _make_metadata(columns=[col])
        graph = _make_graph()

        InferredGlossaryBuilder(graph).build(meta)

        term = graph.get_node("BusinessTerm", "Account Balance")
        assert term["sensitivity_level"] == "CONFIDENTIAL"

    def test_domain_inferred_from_schema(self):
        col = ColumnNode("FINANCE", "ACCOUNTS", "BALANCE", "NUMBER",
                         comments="Account balance")
        meta = _make_metadata(columns=[col])
        graph = _make_graph()

        InferredGlossaryBuilder(graph).build(meta)

        term = graph.get_node("BusinessTerm", "Balance")
        assert term["domain"] == "FINANCE"

    def test_table_level_term_created_when_comment_present(self):
        table = TableNode("KYC", "CUSTOMERS",
                          comments="Core customer entity for KYC compliance")
        meta = _make_metadata(tables=[table])
        graph = _make_graph()

        stats = InferredGlossaryBuilder(graph).build(meta)

        assert stats["terms"] == 1
        assert stats["mappings"] == 1
        term = graph.get_node("BusinessTerm", "Customers")
        assert term is not None
        assert "KYC compliance" in term["definition"]

    def test_table_without_comment_not_indexed(self):
        table = TableNode("KYC", "STAGING_TMP")  # no comment
        meta = _make_metadata(tables=[table])
        graph = _make_graph()

        stats = InferredGlossaryBuilder(graph).build(meta)

        assert stats["terms"] == 0
        assert graph.count_nodes("BusinessTerm") == 0

    def test_deduplication_keeps_highest_confidence(self):
        """Same humanized term from two columns: only one term node, two MAPS_TO."""
        col1 = ColumnNode("KYC", "CUSTOMERS", "CUSTOMER_ID", "NUMBER",
                          comments="Primary customer identifier")  # confidence 0.95
        col2 = ColumnNode("KYC", "ACCOUNTS", "CUSTOMER_ID", "NUMBER")  # no comment → 0.50
        meta = _make_metadata(columns=[col1, col2])
        graph = _make_graph()

        stats = InferredGlossaryBuilder(graph).build(meta)

        assert stats["terms"] == 1     # one distinct term "Customer ID"
        assert stats["mappings"] == 2  # one edge per column

        term = graph.get_node("BusinessTerm", "Customer ID")
        # Best definition is from col1 (higher confidence)
        assert "Primary customer identifier" in term["definition"]

    def test_maps_to_edges_contain_correct_fqns(self):
        col = ColumnNode("KYC", "CUSTOMERS", "RISK_RATING", "VARCHAR2",
                         comments="Risk class")
        meta = _make_metadata(columns=[col])
        graph = _make_graph()

        InferredGlossaryBuilder(graph).build(meta)

        edges = graph.get_out_edges("MAPS_TO", "Risk Rating")
        assert len(edges) == 1
        assert edges[0]["_to"] == "KYC.CUSTOMERS.RISK_RATING"
        assert edges[0]["mapping_type"] == "inferred"

    def test_aliases_include_snake_and_upper(self):
        col = ColumnNode("KYC", "CUSTOMERS", "RISK_RATING", "VARCHAR2",
                         comments="Risk class")
        meta = _make_metadata(columns=[col])
        graph = _make_graph()

        InferredGlossaryBuilder(graph).build(meta)

        term = graph.get_node("BusinessTerm", "Risk Rating")
        aliases = term["aliases"]
        assert "risk_rating" in aliases
        assert "RISK_RATING" in aliases

    def test_returns_correct_counts(self):
        cols = [
            ColumnNode("KYC", "CUSTOMERS", "RISK_RATING", "VARCHAR2",
                       comments="Risk"),
            ColumnNode("KYC", "CUSTOMERS", "NATIONALITY", "VARCHAR2",
                       comments="Nationality code"),
            ColumnNode("KYC", "ACCOUNTS", "RISK_RATING", "VARCHAR2"),  # duplicate term
        ]
        meta = _make_metadata(columns=cols)
        graph = _make_graph()

        stats = InferredGlossaryBuilder(graph).build(meta)

        assert stats["terms"] == 2     # RISK_RATING + NATIONALITY (deduplicated)
        assert stats["mappings"] == 3  # one edge per column occurrence
