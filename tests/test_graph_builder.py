"""
Tests for knowledge_graph.graph_builder
=========================================
Verifies that the GraphBuilder:
  1. Creates the correct Neo4j schema constraints and indexes
  2. Upserts nodes and edges in the right order
  3. Correctly computes JOIN_PATH edges via the FK graph
  4. Correctly infers SIMILAR_TO edges using name-based heuristics
  5. Handles empty metadata gracefully

All tests use the KYC fixture metadata (no live Neo4j required).
The mock session captures all Cypher calls for assertion.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock, patch

import pytest

from knowledge_graph.graph_builder import GraphBuilder
from knowledge_graph.models import (
    ColumnNode, TableNode, HasForeignKeyRel, JoinPathRel, SimilarToRel
)
from knowledge_graph.oracle_extractor import OracleMetadata


# ---------------------------------------------------------------------------
# JOIN_PATH computation tests (pure Python — no Neo4j needed)
# ---------------------------------------------------------------------------

class TestJoinPathComputation:
    """Test the BFS/NetworkX join path computation logic in isolation."""

    def test_direct_fk_produces_path(self, graph_config, kyc_metadata):
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        path_pairs = {(p.source_table_fqn, p.target_table_fqn) for p in paths}

        # ACCOUNTS → CUSTOMERS must exist (direct FK)
        assert ("KYC.ACCOUNTS", "KYC.CUSTOMERS") in path_pairs

    def test_two_hop_path_exists(self, graph_config, kyc_metadata):
        """TRANSACTIONS → CUSTOMERS requires 2 hops (TRANSACTIONS→ACCOUNTS→CUSTOMERS)."""
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        path_pairs = {(p.source_table_fqn, p.target_table_fqn) for p in paths}
        assert ("KYC.TRANSACTIONS", "KYC.CUSTOMERS") in path_pairs

    def test_path_weight_direct_eq_1(self, graph_config, kyc_metadata):
        """Direct FK should have weight=1."""
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        direct = [
            p for p in paths
            if p.source_table_fqn == "KYC.ACCOUNTS"
            and p.target_table_fqn == "KYC.CUSTOMERS"
        ]
        assert direct, "Expected a direct ACCOUNTS→CUSTOMERS path"
        assert direct[0].weight == 1

    def test_path_weight_two_hop_eq_2(self, graph_config, kyc_metadata):
        """Two-hop path should have weight=2."""
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        two_hop = [
            p for p in paths
            if p.source_table_fqn == "KYC.TRANSACTIONS"
            and p.target_table_fqn == "KYC.CUSTOMERS"
        ]
        assert two_hop, "Expected a TRANSACTIONS→CUSTOMERS path"
        assert two_hop[0].weight == 2

    def test_bidirectional_paths(self, graph_config, kyc_metadata):
        """Paths should be generated in both directions."""
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        path_pairs = {(p.source_table_fqn, p.target_table_fqn) for p in paths}
        assert ("KYC.CUSTOMERS", "KYC.ACCOUNTS") in path_pairs

    def test_path_contains_join_columns(self, graph_config, kyc_metadata):
        """Every path must include at least one join column pair."""
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        for path in paths:
            assert len(path.join_columns) > 0, (
                f"Path {path.source_table_fqn}→{path.target_table_fqn} has no join columns"
            )

    def test_beyond_max_hops_excluded(self, graph_config, kyc_metadata):
        """No path longer than max_join_path_hops should appear."""
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        max_hops = graph_config.max_join_path_hops
        for path in paths:
            assert path.weight <= max_hops, (
                f"Path weight {path.weight} exceeds max_hops={max_hops}"
            )

    def test_no_self_paths(self, graph_config, kyc_metadata):
        """No path should exist from a table to itself."""
        builder = GraphBuilder(graph_config)
        paths = builder._compute_join_paths(kyc_metadata)
        for path in paths:
            assert path.source_table_fqn != path.target_table_fqn

    def test_empty_metadata_produces_no_paths(self, graph_config):
        builder = GraphBuilder(graph_config)
        empty = OracleMetadata()
        paths = builder._compute_join_paths(empty)
        assert paths == []


# ---------------------------------------------------------------------------
# SIMILAR_TO edge inference tests
# ---------------------------------------------------------------------------

class TestSimilarToComputation:
    def test_exact_name_match_across_tables(self, graph_config):
        """CUSTOMER_ID in CUSTOMERS and ACCOUNTS yields a SIMILAR_TO edge (type=exact)."""
        builder = GraphBuilder(graph_config)
        cols = [
            ColumnNode("KYC", "CUSTOMERS",   "CUSTOMER_ID", "NUMBER"),
            ColumnNode("KYC", "ACCOUNTS",    "CUSTOMER_ID", "NUMBER"),
            ColumnNode("KYC", "KYC_REVIEWS", "CUSTOMER_ID", "NUMBER"),
        ]
        meta = OracleMetadata()
        meta.columns = cols
        results = builder._compute_similar_to(meta)
        exact_pairs = {(r.source_col_fqn, r.target_col_fqn) for r in results if r.match_type == "exact"}
        assert len(exact_pairs) >= 1

    def test_exact_match_score_is_1(self, graph_config):
        builder = GraphBuilder(graph_config)
        cols = [
            ColumnNode("KYC", "CUSTOMERS", "CUSTOMER_ID", "NUMBER"),
            ColumnNode("KYC", "ACCOUNTS",  "CUSTOMER_ID", "NUMBER"),
        ]
        meta = OracleMetadata()
        meta.columns = cols
        results = builder._compute_similar_to(meta)
        exact = [r for r in results if r.match_type == "exact"]
        assert exact
        assert exact[0].similarity_score == 1.0

    def test_same_table_columns_not_linked(self, graph_config):
        """Columns in the same table should never produce a SIMILAR_TO edge."""
        builder = GraphBuilder(graph_config)
        cols = [
            ColumnNode("KYC", "CUSTOMERS", "FIRST_NAME", "VARCHAR2"),
            ColumnNode("KYC", "CUSTOMERS", "LAST_NAME",  "VARCHAR2"),
        ]
        meta = OracleMetadata()
        meta.columns = cols
        results = builder._compute_similar_to(meta)
        assert results == []

    def test_suffix_pattern_match(self, graph_config):
        """Columns with the same _ID suffix across tables should get suffix-type edge."""
        builder = GraphBuilder(graph_config)
        cols = [
            ColumnNode("KYC", "ORDERS",  "CUSTOMER_ID", "NUMBER"),
            ColumnNode("KYC", "RETURNS", "CUSTOMER_ID", "NUMBER"),
        ]
        meta = OracleMetadata()
        meta.columns = cols
        results = builder._compute_similar_to(meta)
        assert any(r.match_type in ("exact", "suffix") for r in results)

    def test_levenshtein_close_names(self, graph_config):
        """Column names within edit distance 2 should produce levenshtein edges."""
        builder = GraphBuilder(graph_config)
        # 'CUST_ID' vs 'CUST_ID2' — edit distance 1
        cols = [
            ColumnNode("KYC", "TABLE_A", "CUST_ID",  "NUMBER"),
            ColumnNode("KYC", "TABLE_B", "CUST_ID2", "NUMBER"),
        ]
        meta = OracleMetadata()
        meta.columns = cols
        results = builder._compute_similar_to(meta)
        assert any(r.match_type == "levenshtein" for r in results)

    def test_dissimilar_names_not_linked(self, graph_config):
        """Very different column names should not produce SIMILAR_TO edges."""
        builder = GraphBuilder(graph_config)
        cols = [
            ColumnNode("KYC", "TABLE_A", "CREATED_AT",   "DATE"),
            ColumnNode("KYC", "TABLE_B", "ACCOUNT_BALANCE", "NUMBER"),
        ]
        meta = OracleMetadata()
        meta.columns = cols
        results = builder._compute_similar_to(meta)
        # These should not produce any relationship
        assert not results

    def test_empty_columns_produces_no_edges(self, graph_config):
        builder = GraphBuilder(graph_config)
        meta = OracleMetadata()
        results = builder._compute_similar_to(meta)
        assert results == []


# ---------------------------------------------------------------------------
# GraphBuilder.build() — Cypher statement verification via mock session
# ---------------------------------------------------------------------------

class TestGraphBuilderCypherCalls:
    """
    Verify that builder.build() issues the expected MERGE/SET Cypher statements
    against a mock Neo4j session.
    """

    def _run_build(self, graph_config, kyc_metadata):
        """Run builder.build() with a mock Neo4j driver and return the capture."""
        from tests.conftest import CypherCapture
        capture = CypherCapture()

        mock_session = MagicMock()
        mock_session.run.side_effect = capture.run
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = lambda s: mock_session
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_driver.verify_connectivity = MagicMock()

        builder = GraphBuilder(graph_config)
        builder._driver = mock_driver

        builder.build(kyc_metadata)
        return capture

    def test_schema_constraints_created(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("CREATE CONSTRAINT")

    def test_schema_nodes_merged(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("MERGE (s:Schema")

    def test_table_nodes_merged(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("MERGE (t:Table {fqn")

    def test_column_nodes_merged(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("MERGE (c:Column {fqn")

    def test_pk_edges_created(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("HAS_PRIMARY_KEY")

    def test_fk_edges_created(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("HAS_FOREIGN_KEY")

    def test_index_nodes_merged(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("MERGE (idx:Index")

    def test_join_path_edges_created(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("JOIN_PATH")

    def test_similar_to_edges_created(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("SIMILAR_TO")

    def test_belongs_to_edges_created(self, graph_config, kyc_metadata):
        capture = self._run_build(graph_config, kyc_metadata)
        assert capture.was_called_with("BELONGS_TO")


# ---------------------------------------------------------------------------
# Node model serialisation (to_cypher_params)
# ---------------------------------------------------------------------------

class TestNodeSerialization:
    def test_table_node_params_contains_fqn(self):
        t = TableNode("KYC", "CUSTOMERS", row_count=50000)
        params = t.to_cypher_params()
        assert params["fqn"] == "KYC.CUSTOMERS"
        assert params["name"] == "CUSTOMERS"
        assert params["schema"] == "KYC"

    def test_column_node_params_contains_fqn(self):
        c = ColumnNode("KYC", "CUSTOMERS", "CUSTOMER_ID", "NUMBER")
        params = c.to_cypher_params()
        assert params["fqn"] == "KYC.CUSTOMERS.CUSTOMER_ID"
        assert params["table_fqn"] == "KYC.CUSTOMERS"

    def test_fk_rel_params(self):
        fk = HasForeignKeyRel("KYC.ACCOUNTS.CUSTOMER_ID", "KYC.CUSTOMERS.CUSTOMER_ID",
                              "FK_ACCT_CUST", "NO ACTION")
        params = fk.to_cypher_params()
        assert params["source_col_fqn"] == "KYC.ACCOUNTS.CUSTOMER_ID"
        assert params["target_col_fqn"] == "KYC.CUSTOMERS.CUSTOMER_ID"
        assert params["constraint_name"] == "FK_ACCT_CUST"

    def test_join_path_rel_params(self):
        jp = JoinPathRel(
            source_table_fqn="KYC.TRANSACTIONS",
            target_table_fqn="KYC.CUSTOMERS",
            join_columns=[{"src": "KYC.TRANSACTIONS.ACCOUNT_ID", "tgt": "KYC.ACCOUNTS.ACCOUNT_ID"}],
            weight=2,
        )
        params = jp.to_cypher_params()
        assert params["path_key"] == "KYC.TRANSACTIONS>>KYC.CUSTOMERS"
        assert params["weight"] == 2

    def test_similar_to_rel_params(self):
        st = SimilarToRel("KYC.CUSTOMERS.CUSTOMER_ID", "KYC.ACCOUNTS.CUSTOMER_ID", 1.0, "exact")
        params = st.to_cypher_params()
        assert params["similarity_score"] == 1.0
        assert params["match_type"] == "exact"
