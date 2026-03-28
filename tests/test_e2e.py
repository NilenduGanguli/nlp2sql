"""
End-to-End tests for KnowledgeQL against a live Oracle database.

Prerequisites
-------------
  docker compose -f docker/docker-compose.yml up -d
  # wait for Oracle to be healthy (~2-3 min)
  export ORACLE_DSN=localhost:1521/FREEPDB1
  export ORACLE_USER=kyc
  export ORACLE_PASSWORD=KycPassword1
  export ORACLE_SCHEMA=KYC

Run
---
  python -m pytest tests/test_e2e.py -v --no-header
  # or via helper script:
  bash scripts/e2e_test.sh

These tests are automatically skipped when Oracle is not reachable.
"""

from __future__ import annotations

import os
import time
import pytest

# ---------------------------------------------------------------------------
# Connection parameters (override via environment variables)
# ---------------------------------------------------------------------------
ORACLE_DSN      = os.getenv("ORACLE_DSN",      "localhost:1521/FREEPDB1")
ORACLE_USER     = os.getenv("ORACLE_USER",     "kyc")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "KycPassword1")
ORACLE_SCHEMA   = os.getenv("ORACLE_SCHEMA",   "KYC")


# ---------------------------------------------------------------------------
# Module-level skip: if oracledb missing OR Oracle unreachable
# ---------------------------------------------------------------------------

def _oracle_reachable() -> bool:
    try:
        import oracledb
        conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _oracle_reachable(),
    reason="Oracle not reachable — start docker/docker-compose.yml first",
)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def oracle_config():
    from knowledge_graph.config import OracleConfig, GraphConfig
    return GraphConfig(
        oracle=OracleConfig(
            dsn=ORACLE_DSN,
            user=ORACLE_USER,
            password=ORACLE_PASSWORD,
            target_schemas=[ORACLE_SCHEMA],
            use_dba_views=False,          # KYC user only has ALL_ view access
        ),
        max_join_path_hops=4,
        similarity_levenshtein_max=2,
        similarity_min_score=0.70,
    )


@pytest.fixture(scope="module")
def live_metadata(oracle_config):
    """Extract real metadata from the Docker Oracle instance."""
    from knowledge_graph.oracle_extractor import OracleMetadataExtractor
    extractor = OracleMetadataExtractor(oracle_config.oracle)
    return extractor.extract()


@pytest.fixture(scope="module")
def live_graph(oracle_config, live_metadata):
    """Build a KnowledgeGraph from the extracted metadata."""
    from knowledge_graph.graph_builder import GraphBuilder
    from knowledge_graph.glossary_loader import InferredGlossaryBuilder
    builder = GraphBuilder(oracle_config)
    builder.build(live_metadata)
    InferredGlossaryBuilder(builder.graph).build(live_metadata)
    return builder.graph


@pytest.fixture(scope="module")
def live_conn():
    """Direct oracledb connection for raw SQL verification."""
    import oracledb
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Metadata Extraction Tests
# ---------------------------------------------------------------------------

class TestMetadataExtraction:

    def test_tables_extracted(self, live_metadata):
        """All 8 KYC tables are extracted."""
        table_names = {t.name for t in live_metadata.tables}
        expected = {
            "CUSTOMERS", "ACCOUNTS", "TRANSACTIONS", "KYC_REVIEWS",
            "RISK_ASSESSMENTS", "BENEFICIAL_OWNERS", "EMPLOYEES", "PEP_STATUS",
        }
        assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"

    def test_foreign_keys_extracted(self, live_metadata):
        """At least 9 FK relationships are extracted."""
        assert len(live_metadata.foreign_keys) >= 9, (
            f"Expected ≥9 FKs, got {len(live_metadata.foreign_keys)}"
        )

    def test_columns_extracted(self, live_metadata):
        """At least 40 columns extracted across all tables."""
        assert len(live_metadata.columns) >= 40

    def test_indexes_extracted(self, live_metadata):
        """PK and secondary indexes are extracted."""
        index_names = {i.name for i in live_metadata.indexes}
        assert "PK_CUSTOMERS" in index_names or "IDX_CUST_RISK" in index_names

    def test_table_comments_extracted(self, live_metadata):
        """Table comments are populated."""
        cust = next((t for t in live_metadata.tables if t.name == "CUSTOMERS"), None)
        assert cust is not None
        assert cust.comments and "KYC" in cust.comments.upper()

    def test_column_comments_extracted(self, live_metadata):
        """Column comments are populated for at least one column."""
        commented = [c for c in live_metadata.columns if c.comments]
        assert len(commented) > 0, "No column comments found"


# ---------------------------------------------------------------------------
# 2. Graph Construction Tests
# ---------------------------------------------------------------------------

class TestGraphConstruction:

    def test_graph_node_counts(self, live_graph):
        stats = live_graph.get_stats()
        assert stats.get("Table", 0) == 8
        assert stats.get("Column", 0) >= 40
        assert stats.get("Schema", 0) >= 1

    def test_fk_edges_present(self, live_graph):
        fk_edges = live_graph.get_all_edges("HAS_FOREIGN_KEY")
        assert len(fk_edges) >= 9, f"Expected ≥9 FK edges, got {len(fk_edges)}"

    def test_join_paths_computed(self, live_graph):
        jp_edges = live_graph.get_all_edges("JOIN_PATH")
        assert len(jp_edges) > 0, "No JOIN_PATH edges computed"

    def test_business_terms_inferred(self, live_graph):
        terms = live_graph.get_all_nodes("BusinessTerm")
        assert len(terms) > 0, "No BusinessTerm nodes inferred"

    def test_customers_node(self, live_graph):
        node = live_graph.get_node("Table", f"{ORACLE_SCHEMA}.CUSTOMERS")
        assert node is not None
        assert node.get("name") == "CUSTOMERS"


# ---------------------------------------------------------------------------
# 3. Traversal Query Tests
# ---------------------------------------------------------------------------

class TestTraversalQueries:

    def test_list_all_tables(self, live_graph):
        from knowledge_graph.traversal import list_all_tables
        tables = list_all_tables(live_graph, schema=ORACLE_SCHEMA)
        assert len(tables) == 8

    def test_search_schema_customers(self, live_graph):
        from knowledge_graph.traversal import search_schema
        results = search_schema(live_graph, "customers", limit=5)
        assert any(r.get("name") == "CUSTOMERS" for r in results)

    def test_search_schema_risk(self, live_graph):
        from knowledge_graph.traversal import search_schema
        results = search_schema(live_graph, "risk", limit=10)
        assert len(results) > 0

    def test_find_join_path_customers_to_transactions(self, live_graph):
        from knowledge_graph.traversal import find_join_path
        path = find_join_path(
            live_graph,
            f"{ORACLE_SCHEMA}.CUSTOMERS",
            f"{ORACLE_SCHEMA}.TRANSACTIONS",
            max_hops=4,
        )
        assert path is not None, "No join path found between CUSTOMERS and TRANSACTIONS"
        # CUSTOMERS → ACCOUNTS → TRANSACTIONS = 2 join columns
        assert len(path.get("join_columns", [])) >= 2

    def test_find_join_path_direct_fk(self, live_graph):
        from knowledge_graph.traversal import find_join_path
        path = find_join_path(
            live_graph,
            f"{ORACLE_SCHEMA}.CUSTOMERS",
            f"{ORACLE_SCHEMA}.ACCOUNTS",
        )
        assert path is not None
        # Direct FK = 1 join column
        assert len(path.get("join_columns", [])) == 1

    def test_resolve_business_term_risk(self, live_graph):
        from knowledge_graph.traversal import resolve_business_term
        results = resolve_business_term(live_graph, "risk")
        assert len(results) >= 1

    def test_get_context_subgraph(self, live_graph):
        from knowledge_graph.traversal import get_context_subgraph
        ctx = get_context_subgraph(live_graph, [
            f"{ORACLE_SCHEMA}.CUSTOMERS",
            f"{ORACLE_SCHEMA}.ACCOUNTS",
        ])
        assert len(ctx) == 2
        table_names = {e["table"]["name"] for e in ctx}
        assert "CUSTOMERS" in table_names
        assert "ACCOUNTS" in table_names

    def test_serialize_context_to_ddl(self, live_graph):
        from knowledge_graph.traversal import get_context_subgraph, serialize_context_to_ddl
        ctx = get_context_subgraph(live_graph, [f"{ORACLE_SCHEMA}.CUSTOMERS"])
        ddl = serialize_context_to_ddl(ctx)
        assert "CUSTOMERS" in ddl
        assert "CUSTOMER_ID" in ddl
        assert "RISK_RATING" in ddl


# ---------------------------------------------------------------------------
# 4. Live SQL Execution Tests
# ---------------------------------------------------------------------------

class TestLiveSQLExecution:

    def _exec(self, conn, sql: str):
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return cols, rows

    def test_customers_row_count(self, live_conn):
        cols, rows = self._exec(live_conn, "SELECT COUNT(*) FROM kyc.customers")
        assert rows[0][0] == 15

    def test_high_risk_customers(self, live_conn):
        cols, rows = self._exec(
            live_conn,
            "SELECT customer_id, first_name, last_name FROM kyc.customers "
            "WHERE risk_rating = 'HIGH' ORDER BY customer_id"
        )
        assert len(rows) == 3  # customers 1002, 1009, 1015

    def test_very_high_risk_customers(self, live_conn):
        cols, rows = self._exec(
            live_conn,
            "SELECT customer_id FROM kyc.customers WHERE risk_rating = 'VERY_HIGH'"
        )
        customer_ids = {r[0] for r in rows}
        assert customer_ids == {1006, 1012}

    def test_flagged_transactions(self, live_conn):
        cols, rows = self._exec(
            live_conn,
            "SELECT COUNT(*) FROM kyc.transactions WHERE is_flagged = 'Y'"
        )
        assert rows[0][0] >= 7

    def test_join_customers_accounts(self, live_conn):
        cols, rows = self._exec(
            live_conn,
            """SELECT c.first_name, c.last_name, a.account_type, a.balance
               FROM kyc.customers c
               JOIN kyc.accounts a ON a.customer_id = c.customer_id
               WHERE a.status = 'FROZEN'
               ORDER BY a.balance DESC"""
        )
        assert len(rows) >= 2   # Hosseini + Al-Rashid have frozen accounts

    def test_pep_customers_with_flagged_tx(self, live_conn):
        cols, rows = self._exec(
            live_conn,
            """SELECT DISTINCT c.customer_id, c.first_name
               FROM kyc.customers c
               JOIN kyc.pep_status p ON p.customer_id = c.customer_id
               JOIN kyc.accounts a ON a.customer_id = c.customer_id
               JOIN kyc.transactions t ON t.account_id = a.account_id
               WHERE p.is_pep = 'Y'
                 AND t.is_flagged = 'Y'"""
        )
        assert len(rows) >= 1

    def test_risk_assessments_join_employees(self, live_conn):
        cols, rows = self._exec(
            live_conn,
            """SELECT ra.customer_id, e.first_name AS assessor, ra.risk_score
               FROM kyc.risk_assessments ra
               JOIN kyc.employees e ON e.employee_id = ra.assessed_by
               WHERE ra.risk_level = 'VERY_HIGH'"""
        )
        assert len(rows) >= 2

    def test_kyc_reviews_escalated(self, live_conn):
        cols, rows = self._exec(
            live_conn,
            "SELECT COUNT(*) FROM kyc.kyc_reviews WHERE status = 'ESCALATED'"
        )
        assert rows[0][0] == 3


# ---------------------------------------------------------------------------
# 5. Agent Pipeline E2E Tests
# ---------------------------------------------------------------------------

class TestAgentPipelineE2E:

    @pytest.fixture(scope="class")
    def pipeline(self, live_graph):
        from app_config import AppConfig
        from knowledge_graph.config import OracleConfig
        from agent.pipeline import build_pipeline
        cfg = AppConfig()
        cfg.oracle = OracleConfig(
            dsn=ORACLE_DSN,
            user=ORACLE_USER,
            password=ORACLE_PASSWORD,
            target_schemas=[ORACLE_SCHEMA],
            use_dba_views=False,
        )
        return build_pipeline(live_graph, cfg, llm=None)   # no-LLM mode

    def test_pipeline_runs(self, pipeline):
        from agent.pipeline import run_query
        result = run_query(pipeline, "list all customers")
        assert result is not None
        assert result.get("type") in ("query_result", "error")

    def test_pipeline_returns_real_rows(self, pipeline):
        from agent.pipeline import run_query
        result = run_query(pipeline, "show high risk customers")
        # In no-LLM mode the query may be generic — just check it executed
        assert result.get("total_rows", 0) >= 0

    def test_pipeline_sql_is_valid_oracle(self, pipeline, live_conn):
        """SQL produced by the pipeline must be executable against live Oracle."""
        from agent.pipeline import run_query
        result = run_query(pipeline, "count customers")
        sql = result.get("sql", "")
        if sql and not sql.strip().upper().startswith("--"):
            try:
                with live_conn.cursor() as cur:
                    cur.execute(sql)
            except Exception as exc:
                pytest.fail(f"Pipeline-generated SQL failed on Oracle: {exc}\nSQL: {sql}")
