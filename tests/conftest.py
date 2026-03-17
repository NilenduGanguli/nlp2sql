"""
Test fixtures for KnowledgeQL knowledge graph tests.

Provides:
  - A complete KYC schema snapshot (OracleMetadata) built from in-memory
    fixture data — no live Oracle database required.
  - A mock Neo4j driver / session that captures executed Cypher statements.
  - A mock oracledb connection for unit-testing the extractor.
  - Convenience helpers for asserting Cypher calls.

KYC Schema used in all tests
-----------------------------
  KYC.CUSTOMERS          – core customer entity
  KYC.ACCOUNTS           – accounts owned by customers
  KYC.TRANSACTIONS       – transactions on accounts
  KYC.KYC_REVIEWS        – periodic KYC review records
  KYC.RISK_ASSESSMENTS   – risk scoring records per customer
  KYC.BENEFICIAL_OWNERS  – UBO records
  KYC.EMPLOYEES          – employee / account manager records
  KYC.PEP_STATUS         – politically exposed person flags
"""

from __future__ import annotations

from typing import Any, Generator, List
from unittest.mock import MagicMock, patch
import pytest

from knowledge_graph.config import GraphConfig, OracleConfig, Neo4jConfig
from knowledge_graph.models import (
    SchemaNode, TableNode, ColumnNode, ViewNode, IndexNode,
    ConstraintNode, ProcedureNode,
    HasForeignKeyRel, HasPrimaryKeyRel,
)
from knowledge_graph.oracle_extractor import OracleMetadata


# ---------------------------------------------------------------------------
# KYC fixture schema definition
# ---------------------------------------------------------------------------

KYC_SCHEMA = "KYC"


def _col(schema, table, name, dtype, length=None, precision=None,
         scale=None, nullable="Y", column_id=1,
         is_pk=False, is_fk=False, is_indexed=False, comments=None):
    return ColumnNode(
        schema=schema, table_name=table, name=name,
        data_type=dtype, data_length=length,
        precision=precision, scale=scale,
        nullable=nullable, column_id=column_id,
        is_pk=is_pk, is_fk=is_fk, is_indexed=is_indexed,
        comments=comments,
        sample_values=[],
    )


@pytest.fixture(scope="session")
def kyc_tables() -> List[TableNode]:
    return [
        TableNode(KYC_SCHEMA, "CUSTOMERS",        row_count=50000,  comments="Core customer entity for KYC compliance"),
        TableNode(KYC_SCHEMA, "ACCOUNTS",          row_count=120000, comments="Customer accounts"),
        TableNode(KYC_SCHEMA, "TRANSACTIONS",      row_count=5000000,comments="Financial transactions"),
        TableNode(KYC_SCHEMA, "KYC_REVIEWS",       row_count=200000, comments="Periodic KYC review records"),
        TableNode(KYC_SCHEMA, "RISK_ASSESSMENTS",  row_count=75000,  comments="Customer risk scores"),
        TableNode(KYC_SCHEMA, "BENEFICIAL_OWNERS", row_count=30000,  comments="Ultimate beneficial owner records"),
        TableNode(KYC_SCHEMA, "EMPLOYEES",         row_count=1500,   comments="Employee directory"),
        TableNode(KYC_SCHEMA, "PEP_STATUS",        row_count=8000,   comments="Politically exposed person flags"),
    ]


@pytest.fixture(scope="session")
def kyc_columns() -> List[ColumnNode]:
    S = KYC_SCHEMA
    return [
        # CUSTOMERS
        _col(S, "CUSTOMERS", "CUSTOMER_ID",        "NUMBER",   precision=10, nullable="N", column_id=1,  is_pk=True,  is_indexed=True,  comments="Unique customer identifier"),
        _col(S, "CUSTOMERS", "FIRST_NAME",          "VARCHAR2", length=100,   nullable="N", column_id=2,  comments="Customer first name"),
        _col(S, "CUSTOMERS", "LAST_NAME",           "VARCHAR2", length=100,   nullable="N", column_id=3,  comments="Customer last name"),
        _col(S, "CUSTOMERS", "DATE_OF_BIRTH",       "DATE",                   nullable="Y", column_id=4,  comments="Date of birth"),
        _col(S, "CUSTOMERS", "NATIONALITY",         "VARCHAR2", length=3,     nullable="Y", column_id=5,  comments="ISO 3166-1 alpha-3 country code"),
        _col(S, "CUSTOMERS", "SSN",                 "VARCHAR2", length=20,    nullable="Y", column_id=6,  comments="Social security number (masked)"),
        _col(S, "CUSTOMERS", "PASSPORT_NO",         "VARCHAR2", length=30,    nullable="Y", column_id=7,  comments="Passport number (masked)"),
        _col(S, "CUSTOMERS", "RISK_RATING",         "VARCHAR2", length=10,    nullable="N", column_id=8,  is_indexed=True, comments="Risk level: LOW | MEDIUM | HIGH | VERY_HIGH"),
        _col(S, "CUSTOMERS", "ACCOUNT_MANAGER_ID",  "NUMBER",   precision=10, nullable="Y", column_id=9,  is_fk=True,  is_indexed=True, comments="FK → EMPLOYEES.EMPLOYEE_ID"),
        _col(S, "CUSTOMERS", "CREATED_DATE",        "DATE",                   nullable="N", column_id=10, comments="Customer onboarding date"),

        # ACCOUNTS
        _col(S, "ACCOUNTS", "ACCOUNT_ID",   "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True,  is_indexed=True),
        _col(S, "ACCOUNTS", "CUSTOMER_ID",  "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True,  is_indexed=True, comments="FK → CUSTOMERS.CUSTOMER_ID"),
        _col(S, "ACCOUNTS", "ACCOUNT_TYPE", "VARCHAR2", length=20,    nullable="N", column_id=3, comments="SAVINGS | CURRENT | INVESTMENT"),
        _col(S, "ACCOUNTS", "BALANCE",      "NUMBER",   precision=18, scale=2, nullable="N", column_id=4, comments="Current account balance"),
        _col(S, "ACCOUNTS", "CURRENCY",     "VARCHAR2", length=3,     nullable="N", column_id=5, comments="ISO 4217 currency code"),
        _col(S, "ACCOUNTS", "STATUS",       "VARCHAR2", length=20,    nullable="N", column_id=6, comments="ACTIVE | DORMANT | CLOSED | FROZEN"),
        _col(S, "ACCOUNTS", "OPENED_DATE",  "DATE",                   nullable="N", column_id=7, comments="Account opening date"),

        # TRANSACTIONS
        _col(S, "TRANSACTIONS", "TRANSACTION_ID",   "NUMBER",   precision=15, nullable="N", column_id=1, is_pk=True,  is_indexed=True),
        _col(S, "TRANSACTIONS", "ACCOUNT_ID",       "NUMBER",   precision=12, nullable="N", column_id=2, is_fk=True,  is_indexed=True, comments="FK → ACCOUNTS.ACCOUNT_ID"),
        _col(S, "TRANSACTIONS", "AMOUNT",           "NUMBER",   precision=18, scale=2, nullable="N", column_id=3, comments="Transaction amount"),
        _col(S, "TRANSACTIONS", "CURRENCY",         "VARCHAR2", length=3,     nullable="N", column_id=4),
        _col(S, "TRANSACTIONS", "TRANSACTION_DATE", "DATE",                   nullable="N", column_id=5, is_indexed=True),
        _col(S, "TRANSACTIONS", "DESCRIPTION",      "VARCHAR2", length=500,   nullable="Y", column_id=6),
        _col(S, "TRANSACTIONS", "TRANSACTION_TYPE", "VARCHAR2", length=30,    nullable="N", column_id=7, comments="DEBIT | CREDIT | WIRE | INTERNAL"),
        _col(S, "TRANSACTIONS", "IS_FLAGGED",       "CHAR",     length=1,     nullable="N", column_id=8, comments="Y = flagged for investigation"),

        # KYC_REVIEWS
        _col(S, "KYC_REVIEWS", "REVIEW_ID",       "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True,  is_indexed=True),
        _col(S, "KYC_REVIEWS", "CUSTOMER_ID",     "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True,  is_indexed=True, comments="FK → CUSTOMERS.CUSTOMER_ID"),
        _col(S, "KYC_REVIEWS", "REVIEW_DATE",     "DATE",                   nullable="N", column_id=3, is_indexed=True),
        _col(S, "KYC_REVIEWS", "REVIEWER_ID",     "NUMBER",   precision=10, nullable="N", column_id=4, is_fk=True,  comments="FK → EMPLOYEES.EMPLOYEE_ID"),
        _col(S, "KYC_REVIEWS", "STATUS",          "VARCHAR2", length=20,    nullable="N", column_id=5, comments="PENDING | COMPLETED | FAILED | ESCALATED"),
        _col(S, "KYC_REVIEWS", "NEXT_REVIEW_DATE","DATE",                   nullable="Y", column_id=6),
        _col(S, "KYC_REVIEWS", "NOTES",           "CLOB",                   nullable="Y", column_id=7),

        # RISK_ASSESSMENTS
        _col(S, "RISK_ASSESSMENTS", "ASSESSMENT_ID", "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True),
        _col(S, "RISK_ASSESSMENTS", "CUSTOMER_ID",   "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True, is_indexed=True, comments="FK → CUSTOMERS.CUSTOMER_ID"),
        _col(S, "RISK_ASSESSMENTS", "RISK_SCORE",    "NUMBER",   precision=5,  scale=2, nullable="N", column_id=3),
        _col(S, "RISK_ASSESSMENTS", "RISK_LEVEL",    "VARCHAR2", length=10,    nullable="N", column_id=4),
        _col(S, "RISK_ASSESSMENTS", "ASSESSED_DATE", "DATE",                   nullable="N", column_id=5),
        _col(S, "RISK_ASSESSMENTS", "ASSESSED_BY",   "NUMBER",   precision=10, nullable="Y", column_id=6, is_fk=True, comments="FK → EMPLOYEES.EMPLOYEE_ID"),

        # BENEFICIAL_OWNERS
        _col(S, "BENEFICIAL_OWNERS", "OWNER_ID",      "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True),
        _col(S, "BENEFICIAL_OWNERS", "CUSTOMER_ID",   "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True, is_indexed=True),
        _col(S, "BENEFICIAL_OWNERS", "OWNER_NAME",    "VARCHAR2", length=200,   nullable="N", column_id=3),
        _col(S, "BENEFICIAL_OWNERS", "OWNERSHIP_PCT", "NUMBER",   precision=5,  scale=2, nullable="N", column_id=4, comments="Ownership percentage (25-100)"),
        _col(S, "BENEFICIAL_OWNERS", "RELATIONSHIP",  "VARCHAR2", length=50,    nullable="N", column_id=5),

        # EMPLOYEES
        _col(S, "EMPLOYEES", "EMPLOYEE_ID", "NUMBER",   precision=10, nullable="N", column_id=1, is_pk=True,  is_indexed=True),
        _col(S, "EMPLOYEES", "FIRST_NAME",  "VARCHAR2", length=100,   nullable="N", column_id=2),
        _col(S, "EMPLOYEES", "LAST_NAME",   "VARCHAR2", length=100,   nullable="N", column_id=3),
        _col(S, "EMPLOYEES", "DEPARTMENT",  "VARCHAR2", length=100,   nullable="Y", column_id=4),
        _col(S, "EMPLOYEES", "ROLE",        "VARCHAR2", length=100,   nullable="Y", column_id=5),
        _col(S, "EMPLOYEES", "EMAIL",       "VARCHAR2", length=200,   nullable="Y", column_id=6),

        # PEP_STATUS
        _col(S, "PEP_STATUS", "PEP_ID",      "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True),
        _col(S, "PEP_STATUS", "CUSTOMER_ID", "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True, is_indexed=True),
        _col(S, "PEP_STATUS", "IS_PEP",      "CHAR",     length=1,     nullable="N", column_id=3, comments="Y | N"),
        _col(S, "PEP_STATUS", "PEP_TYPE",    "VARCHAR2", length=50,    nullable="Y", column_id=4, comments="HEAD_OF_STATE | SENIOR_OFFICIAL | JUDGE | MILITARY"),
        _col(S, "PEP_STATUS", "LISTED_DATE", "DATE",                   nullable="Y", column_id=5),
    ]


@pytest.fixture(scope="session")
def kyc_foreign_keys() -> List[HasForeignKeyRel]:
    S = KYC_SCHEMA
    return [
        HasForeignKeyRel(f"{S}.ACCOUNTS.CUSTOMER_ID",          f"{S}.CUSTOMERS.CUSTOMER_ID",           "FK_ACCOUNTS_CUSTOMER",       "NO ACTION"),
        HasForeignKeyRel(f"{S}.TRANSACTIONS.ACCOUNT_ID",       f"{S}.ACCOUNTS.ACCOUNT_ID",             "FK_TRANSACTIONS_ACCOUNT",    "NO ACTION"),
        HasForeignKeyRel(f"{S}.KYC_REVIEWS.CUSTOMER_ID",       f"{S}.CUSTOMERS.CUSTOMER_ID",           "FK_REVIEWS_CUSTOMER",        "NO ACTION"),
        HasForeignKeyRel(f"{S}.KYC_REVIEWS.REVIEWER_ID",       f"{S}.EMPLOYEES.EMPLOYEE_ID",           "FK_REVIEWS_REVIEWER",        "NO ACTION"),
        HasForeignKeyRel(f"{S}.RISK_ASSESSMENTS.CUSTOMER_ID",  f"{S}.CUSTOMERS.CUSTOMER_ID",           "FK_RISK_CUSTOMER",           "NO ACTION"),
        HasForeignKeyRel(f"{S}.RISK_ASSESSMENTS.ASSESSED_BY",  f"{S}.EMPLOYEES.EMPLOYEE_ID",           "FK_RISK_ASSESSOR",           "NO ACTION"),
        HasForeignKeyRel(f"{S}.BENEFICIAL_OWNERS.CUSTOMER_ID", f"{S}.CUSTOMERS.CUSTOMER_ID",           "FK_BENE_CUSTOMER",           "CASCADE"),
        HasForeignKeyRel(f"{S}.CUSTOMERS.ACCOUNT_MANAGER_ID",  f"{S}.EMPLOYEES.EMPLOYEE_ID",           "FK_CUST_MANAGER",            "NO ACTION"),
        HasForeignKeyRel(f"{S}.PEP_STATUS.CUSTOMER_ID",        f"{S}.CUSTOMERS.CUSTOMER_ID",           "FK_PEP_CUSTOMER",            "CASCADE"),
    ]


@pytest.fixture(scope="session")
def kyc_primary_keys(kyc_tables) -> List[HasPrimaryKeyRel]:
    S = KYC_SCHEMA
    pk_map = {
        "CUSTOMERS":        ("CUSTOMER_ID",    "PK_CUSTOMERS"),
        "ACCOUNTS":         ("ACCOUNT_ID",     "PK_ACCOUNTS"),
        "TRANSACTIONS":     ("TRANSACTION_ID", "PK_TRANSACTIONS"),
        "KYC_REVIEWS":      ("REVIEW_ID",      "PK_KYC_REVIEWS"),
        "RISK_ASSESSMENTS": ("ASSESSMENT_ID",  "PK_RISK_ASSESSMENTS"),
        "BENEFICIAL_OWNERS":("OWNER_ID",       "PK_BENEFICIAL_OWNERS"),
        "EMPLOYEES":        ("EMPLOYEE_ID",    "PK_EMPLOYEES"),
        "PEP_STATUS":       ("PEP_ID",         "PK_PEP_STATUS"),
    }
    return [
        HasPrimaryKeyRel(
            table_fqn=f"{S}.{table}",
            column_fqn=f"{S}.{table}.{col}",
            constraint_name=con_name,
        )
        for table, (col, con_name) in pk_map.items()
    ]


@pytest.fixture(scope="session")
def kyc_indexes() -> List[IndexNode]:
    S = KYC_SCHEMA
    return [
        IndexNode("PK_CUSTOMERS",   S, "CUSTOMERS",    "NORMAL", "UNIQUE",    "CUSTOMER_ID"),
        IndexNode("IDX_CUST_RISK",  S, "CUSTOMERS",    "NORMAL", "NONUNIQUE", "RISK_RATING"),
        IndexNode("IDX_CUST_MGR",   S, "CUSTOMERS",    "NORMAL", "NONUNIQUE", "ACCOUNT_MANAGER_ID"),
        IndexNode("PK_ACCOUNTS",    S, "ACCOUNTS",     "NORMAL", "UNIQUE",    "ACCOUNT_ID"),
        IndexNode("IDX_ACCT_CUST",  S, "ACCOUNTS",     "NORMAL", "NONUNIQUE", "CUSTOMER_ID"),
        IndexNode("PK_TXN",         S, "TRANSACTIONS", "NORMAL", "UNIQUE",    "TRANSACTION_ID"),
        IndexNode("IDX_TXN_ACCT",   S, "TRANSACTIONS", "NORMAL", "NONUNIQUE", "ACCOUNT_ID"),
        IndexNode("IDX_TXN_DATE",   S, "TRANSACTIONS", "NORMAL", "NONUNIQUE", "TRANSACTION_DATE"),
        IndexNode("PK_KYC_REVIEWS", S, "KYC_REVIEWS",  "NORMAL", "UNIQUE",    "REVIEW_ID"),
        IndexNode("IDX_KYC_CUST",   S, "KYC_REVIEWS",  "NORMAL", "NONUNIQUE", "CUSTOMER_ID"),
    ]


@pytest.fixture(scope="session")
def kyc_metadata(
    kyc_tables, kyc_columns, kyc_foreign_keys, kyc_primary_keys, kyc_indexes
) -> OracleMetadata:
    """Complete OracleMetadata fixture representing the KYC schema."""
    meta = OracleMetadata()
    meta.schemas = [SchemaNode(name=KYC_SCHEMA)]
    meta.tables = kyc_tables
    meta.columns = kyc_columns
    meta.foreign_keys = kyc_foreign_keys
    meta.primary_keys = kyc_primary_keys
    meta.indexes = kyc_indexes
    meta.views = []
    meta.constraints = []
    meta.procedures = []
    meta.synonyms = []
    meta.sequences = []
    meta.view_dependencies = {}
    meta.sample_data = {}
    return meta


# ---------------------------------------------------------------------------
# GraphConfig fixture (no real connections needed)
# ---------------------------------------------------------------------------

@pytest.fixture
def graph_config() -> GraphConfig:
    return GraphConfig(
        oracle=OracleConfig(
            dsn="localhost:1521/XEPDB1",
            user="test_user",
            password="test_pass",
            target_schemas=["KYC"],
        ),
        neo4j=Neo4jConfig(
            uri="bolt://localhost:7687",
            user="neo4j",
            password="test_pass",
            batch_size=100,
        ),
        max_join_path_hops=4,
        similarity_levenshtein_max=2,
        similarity_min_score=0.75,
        glossary_path="data/kyc_glossary.json",
    )


# ---------------------------------------------------------------------------
# Mock Neo4j session that records Cypher calls
# ---------------------------------------------------------------------------

class CypherCapture:
    """Captures all (cypher, params) calls made on a mock Neo4j session."""

    def __init__(self):
        self.calls: List[tuple] = []

    def run(self, cypher: str, **kwargs) -> MagicMock:
        self.calls.append((cypher, kwargs))
        mock_result = MagicMock()
        mock_result.single.return_value = {"cnt": 0}
        mock_result.__iter__ = lambda s: iter([])
        return mock_result

    def cypher_texts(self) -> List[str]:
        return [c[0] for c in self.calls]

    def was_called_with(self, substring: str) -> bool:
        return any(substring.upper() in c[0].upper() for c in self.calls)


@pytest.fixture
def cypher_capture() -> CypherCapture:
    return CypherCapture()


@pytest.fixture
def mock_neo4j_session(cypher_capture: CypherCapture) -> MagicMock:
    session = MagicMock()
    session.run.side_effect = cypher_capture.run
    return session


# ---------------------------------------------------------------------------
# Mock oracledb connection
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_oracle_conn(kyc_metadata: OracleMetadata) -> MagicMock:
    """
    A mock oracledb.Connection whose cursors return fixture data
    for the most commonly tested queries.
    """
    conn = MagicMock()

    def _make_cursor(rows, col_descriptions=None):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = rows
        cur.description = col_descriptions or []
        cur.__iter__ = lambda s: iter(rows)
        return cur

    # Return a generic cursor by default
    conn.cursor.return_value = _make_cursor([])
    return conn
