"""
KnowledgeQL — NLP-to-SQL Chat Interface
=========================================
Powered by an in-memory Knowledge Graph of Oracle KYC schema metadata.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

# ---------------------------------------------------------------------------
# Page configuration — MUST be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="NLP2SQL",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
    /* Header */
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        background: linear-gradient(90deg, #1f4e79, #2e86de);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0;
    }
    .main-subtitle {
        color: #6c757d;
        font-size: 0.95rem;
        margin-top: 0;
        margin-bottom: 1.5rem;
    }

    /* Status pills */
    .status-pill {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-left: 4px;
    }
    .pill-green  { background: #d4edda; color: #155724; }
    .pill-orange { background: #fff3cd; color: #856404; }
    .pill-red    { background: #f8d7da; color: #721c24; }
    .pill-blue   { background: #cce5ff; color: #004085; }

    /* Metric cards */
    .metric-card {
        background: #f8f9fa;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        border-left: 3px solid #2e86de;
        margin-bottom: 0.5rem;
    }
    .metric-label { font-size: 0.75rem; color: #6c757d; text-transform: uppercase; }
    .metric-value { font-size: 1.1rem; font-weight: 600; color: #212529; }

    /* SQL block */
    .sql-block {
        background: #1e1e1e;
        border-radius: 8px;
        padding: 1rem;
        font-family: 'Courier New', monospace;
        font-size: 0.85rem;
        color: #d4d4d4;
        overflow-x: auto;
        margin: 0.5rem 0;
    }

    /* Chat messages */
    [data-testid="stChatMessage"] { margin-bottom: 0.5rem; }

    /* Suggested query chips */
    .query-chip-container { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0; }

    /* Sidebar section headers */
    .sidebar-section-header {
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #6c757d;
        margin-top: 1rem;
        margin-bottom: 0.25rem;
    }

    /* Table row in schema explorer */
    .schema-table-row {
        padding: 0.3rem 0;
        border-bottom: 1px solid #f0f0f0;
        font-size: 0.85rem;
    }

    /* Demo mode banner */
    .demo-banner {
        background: linear-gradient(135deg, #fff3cd, #ffeaa7);
        border: 1px solid #ffc107;
        border-radius: 8px;
        padding: 0.5rem 1rem;
        font-size: 0.85rem;
        color: #856404;
        margin-bottom: 1rem;
    }

    /* Footer */
    .footer {
        text-align: center;
        color: #adb5bd;
        font-size: 0.75rem;
        margin-top: 2rem;
        padding-top: 1rem;
        border-top: 1px solid #e9ecef;
    }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Suggested queries
# ---------------------------------------------------------------------------
_SUGGESTED_QUERIES = [
    "Show all high-risk customers with their account managers",
    "How many transactions over $10,000 occurred last month?",
    "List customers who haven't had a KYC review in the past year",
    "Find all PEP-flagged customers and their beneficial owners",
]

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------

def init_session_state() -> None:
    """Initialize all Streamlit session state variables."""
    if "config" not in st.session_state:
        try:
            from app_config import AppConfig
            st.session_state.config = AppConfig()
        except Exception as exc:
            st.error(f"Failed to load AppConfig: {exc}")
            st.stop()

    if "graph" not in st.session_state:
        st.session_state.graph = None

    if "pipeline" not in st.session_state:
        st.session_state.pipeline = None

    if "messages" not in st.session_state:
        # Each message: {"role": str, "content": str, "timestamp": str, "result": dict|None}
        st.session_state.messages = []

    if "query_history" not in st.session_state:
        # Each entry: {"query": str, "timestamp": str, "row_count": int}
        st.session_state.query_history = []

    if "graph_initialized" not in st.session_state:
        st.session_state.graph_initialized = False

    if "selected_sql" not in st.session_state:
        st.session_state.selected_sql = ""

    if "editor_result" not in st.session_state:
        st.session_state.editor_result = None

    if "pending_query" not in st.session_state:
        st.session_state.pending_query = None


# ---------------------------------------------------------------------------
# Knowledge graph builder (cached)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Building knowledge graph...")
def get_knowledge_graph(_config_hash: str):
    """Build and cache the KnowledgeGraph.

    When DEMO_MODE=false the graph is built from live Oracle metadata via
    initialize_graph().  Falls back to the hardcoded demo schema on error or
    when DEMO_MODE=true.
    """
    from knowledge_graph.graph_builder import GraphBuilder
    from knowledge_graph.glossary_loader import InferredGlossaryBuilder
    from knowledge_graph.models import (
        ColumnNode,
        HasForeignKeyRel,
        HasPrimaryKeyRel,
        IndexNode,
        SchemaNode,
        TableNode,
    )
    from knowledge_graph.oracle_extractor import OracleMetadata
    from app_config import AppConfig

    config = AppConfig()

    # ── Live Oracle graph ─────────────────────────────────────────────────────
    if not config.demo_mode:
        try:
            from knowledge_graph.init_graph import initialize_graph
            graph, report = initialize_graph(config.graph)
            if report.get("success"):
                return graph
            st.warning(
                "Oracle graph initialisation had issues — falling back to demo schema. "
                "Check the app logs for details."
            )
        except Exception as _exc:
            st.warning(
                f"Could not build graph from Oracle ({_exc}) — using demo schema."
            )

    # ── Demo / fallback: hardcoded static schema ──────────────────────────────
    meta = OracleMetadata()
    meta.schemas = [SchemaNode(name="KYC")]

    S = "KYC"

    meta.tables = [
        TableNode(S, "CUSTOMERS",         row_count=50000,   comments="Core customer entity for KYC compliance"),
        TableNode(S, "ACCOUNTS",          row_count=120000,  comments="Customer accounts"),
        TableNode(S, "TRANSACTIONS",      row_count=5000000, comments="Financial transactions"),
        TableNode(S, "KYC_REVIEWS",       row_count=200000,  comments="Periodic KYC review records"),
        TableNode(S, "RISK_ASSESSMENTS",  row_count=75000,   comments="Customer risk scores"),
        TableNode(S, "BENEFICIAL_OWNERS", row_count=30000,   comments="Ultimate beneficial owner records"),
        TableNode(S, "EMPLOYEES",         row_count=1500,    comments="Employee directory"),
        TableNode(S, "PEP_STATUS",        row_count=8000,    comments="Politically exposed person flags"),
    ]

    def col(table: str, name: str, dtype: str, **kw) -> ColumnNode:
        return ColumnNode(S, table, name, dtype, **kw)

    meta.columns = [
        # CUSTOMERS
        col("CUSTOMERS", "CUSTOMER_ID",        "NUMBER",   precision=10, nullable="N", column_id=1,  is_pk=True,  is_indexed=True,  comments="Unique customer identifier"),
        col("CUSTOMERS", "FIRST_NAME",         "VARCHAR2", data_length=100, nullable="N", column_id=2, comments="Customer first name"),
        col("CUSTOMERS", "LAST_NAME",          "VARCHAR2", data_length=100, nullable="N", column_id=3, comments="Customer last name"),
        col("CUSTOMERS", "DATE_OF_BIRTH",      "DATE",     nullable="Y",  column_id=4,  comments="Date of birth"),
        col("CUSTOMERS", "NATIONALITY",        "VARCHAR2", data_length=3,   nullable="Y", column_id=5,  comments="ISO 3166-1 alpha-3 country code"),
        col("CUSTOMERS", "SSN",                "VARCHAR2", data_length=20,  nullable="Y", column_id=6,  comments="Social security number (masked)"),
        col("CUSTOMERS", "RISK_RATING",        "VARCHAR2", data_length=10,  nullable="N", column_id=8,  is_indexed=True, comments="Risk level: LOW | MEDIUM | HIGH | VERY_HIGH", sample_values=["LOW","MEDIUM","HIGH","VERY_HIGH"], num_distinct=4),
        col("CUSTOMERS", "ACCOUNT_MANAGER_ID", "NUMBER",   precision=10, nullable="Y",  column_id=9,  is_fk=True,  is_indexed=True, comments="FK to EMPLOYEES"),
        col("CUSTOMERS", "CREATED_DATE",       "DATE",     nullable="N",  column_id=10, comments="Onboarding date"),

        # ACCOUNTS
        col("ACCOUNTS", "ACCOUNT_ID",    "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True, is_indexed=True),
        col("ACCOUNTS", "CUSTOMER_ID",   "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True, is_indexed=True, comments="FK to CUSTOMERS"),
        col("ACCOUNTS", "ACCOUNT_TYPE",  "VARCHAR2", data_length=20,  nullable="N", column_id=3, comments="SAVINGS | CURRENT | INVESTMENT", sample_values=["SAVINGS","CURRENT","INVESTMENT"], num_distinct=3),
        col("ACCOUNTS", "BALANCE",       "NUMBER",   precision=18, scale=2, nullable="N", column_id=4, comments="Current balance"),
        col("ACCOUNTS", "CURRENCY",      "VARCHAR2", data_length=3,   nullable="N", column_id=5, comments="ISO 4217 currency code"),
        col("ACCOUNTS", "STATUS",        "VARCHAR2", data_length=20,  nullable="N", column_id=6, comments="ACTIVE | DORMANT | CLOSED | FROZEN", sample_values=["ACTIVE","DORMANT","CLOSED","FROZEN"], num_distinct=4),
        col("ACCOUNTS", "OPENED_DATE",   "DATE",     nullable="N", column_id=7, comments="Account opening date"),

        # TRANSACTIONS
        col("TRANSACTIONS", "TRANSACTION_ID",   "NUMBER",   precision=15, nullable="N", column_id=1, is_pk=True, is_indexed=True),
        col("TRANSACTIONS", "ACCOUNT_ID",       "NUMBER",   precision=12, nullable="N", column_id=2, is_fk=True, is_indexed=True, comments="FK to ACCOUNTS"),
        col("TRANSACTIONS", "AMOUNT",           "NUMBER",   precision=18, scale=2, nullable="N", column_id=3, comments="Transaction amount"),
        col("TRANSACTIONS", "CURRENCY",         "VARCHAR2", data_length=3,  nullable="N", column_id=4),
        col("TRANSACTIONS", "TRANSACTION_DATE", "DATE",     nullable="N", column_id=5, is_indexed=True),
        col("TRANSACTIONS", "TRANSACTION_TYPE", "VARCHAR2", data_length=30, nullable="N", column_id=7, comments="DEBIT | CREDIT | WIRE | INTERNAL", sample_values=["DEBIT","CREDIT","WIRE","INTERNAL"], num_distinct=4),
        col("TRANSACTIONS", "IS_FLAGGED",       "CHAR",     data_length=1,  nullable="N", column_id=8, comments="Y = flagged for investigation"),

        # KYC_REVIEWS
        col("KYC_REVIEWS", "REVIEW_ID",       "NUMBER", precision=12, nullable="N", column_id=1, is_pk=True,  is_indexed=True),
        col("KYC_REVIEWS", "CUSTOMER_ID",     "NUMBER", precision=10, nullable="N", column_id=2, is_fk=True,  is_indexed=True, comments="FK to CUSTOMERS"),
        col("KYC_REVIEWS", "REVIEW_DATE",     "DATE",   nullable="N", column_id=3, is_indexed=True),
        col("KYC_REVIEWS", "REVIEWER_ID",     "NUMBER", precision=10, nullable="N", column_id=4, is_fk=True,  comments="FK to EMPLOYEES"),
        col("KYC_REVIEWS", "STATUS",          "VARCHAR2", data_length=20, nullable="N", column_id=5, comments="PENDING | COMPLETED | FAILED | ESCALATED"),
        col("KYC_REVIEWS", "NEXT_REVIEW_DATE","DATE",   nullable="Y", column_id=6),

        # RISK_ASSESSMENTS
        col("RISK_ASSESSMENTS", "ASSESSMENT_ID", "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True),
        col("RISK_ASSESSMENTS", "CUSTOMER_ID",   "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True, is_indexed=True, comments="FK to CUSTOMERS"),
        col("RISK_ASSESSMENTS", "RISK_SCORE",    "NUMBER",   precision=5, scale=2, nullable="N", column_id=3),
        col("RISK_ASSESSMENTS", "RISK_LEVEL",    "VARCHAR2", data_length=10, nullable="N", column_id=4, comments="LOW | MEDIUM | HIGH | VERY_HIGH"),
        col("RISK_ASSESSMENTS", "ASSESSED_DATE", "DATE",     nullable="N", column_id=5),
        col("RISK_ASSESSMENTS", "ASSESSED_BY",   "NUMBER",   precision=10, nullable="Y", column_id=6, is_fk=True, comments="FK to EMPLOYEES"),

        # BENEFICIAL_OWNERS
        col("BENEFICIAL_OWNERS", "OWNER_ID",      "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True),
        col("BENEFICIAL_OWNERS", "CUSTOMER_ID",   "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True, is_indexed=True),
        col("BENEFICIAL_OWNERS", "OWNER_NAME",    "VARCHAR2", data_length=200, nullable="N", column_id=3),
        col("BENEFICIAL_OWNERS", "OWNERSHIP_PCT", "NUMBER",   precision=5, scale=2, nullable="N", column_id=4, comments="Ownership percentage"),
        col("BENEFICIAL_OWNERS", "RELATIONSHIP",  "VARCHAR2", data_length=50, nullable="N", column_id=5),

        # EMPLOYEES
        col("EMPLOYEES", "EMPLOYEE_ID", "NUMBER",   precision=10, nullable="N", column_id=1, is_pk=True, is_indexed=True),
        col("EMPLOYEES", "FIRST_NAME",  "VARCHAR2", data_length=100, nullable="N", column_id=2),
        col("EMPLOYEES", "LAST_NAME",   "VARCHAR2", data_length=100, nullable="N", column_id=3),
        col("EMPLOYEES", "DEPARTMENT",  "VARCHAR2", data_length=100, nullable="Y", column_id=4),
        col("EMPLOYEES", "ROLE",        "VARCHAR2", data_length=100, nullable="Y", column_id=5),
        col("EMPLOYEES", "EMAIL",       "VARCHAR2", data_length=200, nullable="Y", column_id=6),

        # PEP_STATUS
        col("PEP_STATUS", "PEP_ID",      "NUMBER",   precision=12, nullable="N", column_id=1, is_pk=True),
        col("PEP_STATUS", "CUSTOMER_ID", "NUMBER",   precision=10, nullable="N", column_id=2, is_fk=True, is_indexed=True),
        col("PEP_STATUS", "IS_PEP",      "CHAR",     data_length=1, nullable="N", column_id=3, comments="Y | N"),
        col("PEP_STATUS", "PEP_TYPE",    "VARCHAR2", data_length=50, nullable="Y", column_id=4, comments="HEAD_OF_STATE | SENIOR_OFFICIAL | JUDGE | MILITARY"),
        col("PEP_STATUS", "LISTED_DATE", "DATE",     nullable="Y", column_id=5),
    ]

    meta.foreign_keys = [
        HasForeignKeyRel("KYC.ACCOUNTS.CUSTOMER_ID",         "KYC.CUSTOMERS.CUSTOMER_ID",  "FK_ACCOUNTS_CUSTOMER",  "NO ACTION"),
        HasForeignKeyRel("KYC.TRANSACTIONS.ACCOUNT_ID",       "KYC.ACCOUNTS.ACCOUNT_ID",    "FK_TRANSACTIONS_ACCOUNT","NO ACTION"),
        HasForeignKeyRel("KYC.KYC_REVIEWS.CUSTOMER_ID",       "KYC.CUSTOMERS.CUSTOMER_ID",  "FK_REVIEWS_CUSTOMER",   "NO ACTION"),
        HasForeignKeyRel("KYC.KYC_REVIEWS.REVIEWER_ID",       "KYC.EMPLOYEES.EMPLOYEE_ID",  "FK_REVIEWS_REVIEWER",   "NO ACTION"),
        HasForeignKeyRel("KYC.RISK_ASSESSMENTS.CUSTOMER_ID",  "KYC.CUSTOMERS.CUSTOMER_ID",  "FK_RISK_CUSTOMER",      "NO ACTION"),
        HasForeignKeyRel("KYC.RISK_ASSESSMENTS.ASSESSED_BY",  "KYC.EMPLOYEES.EMPLOYEE_ID",  "FK_RISK_ASSESSOR",      "NO ACTION"),
        HasForeignKeyRel("KYC.BENEFICIAL_OWNERS.CUSTOMER_ID", "KYC.CUSTOMERS.CUSTOMER_ID",  "FK_BENE_CUSTOMER",      "CASCADE"),
        HasForeignKeyRel("KYC.CUSTOMERS.ACCOUNT_MANAGER_ID",  "KYC.EMPLOYEES.EMPLOYEE_ID",  "FK_CUST_MANAGER",       "NO ACTION"),
        HasForeignKeyRel("KYC.PEP_STATUS.CUSTOMER_ID",        "KYC.CUSTOMERS.CUSTOMER_ID",  "FK_PEP_CUSTOMER",       "CASCADE"),
    ]

    meta.primary_keys = [
        HasPrimaryKeyRel("KYC.CUSTOMERS",         "KYC.CUSTOMERS.CUSTOMER_ID",              "PK_CUSTOMERS"),
        HasPrimaryKeyRel("KYC.ACCOUNTS",          "KYC.ACCOUNTS.ACCOUNT_ID",                "PK_ACCOUNTS"),
        HasPrimaryKeyRel("KYC.TRANSACTIONS",      "KYC.TRANSACTIONS.TRANSACTION_ID",        "PK_TRANSACTIONS"),
        HasPrimaryKeyRel("KYC.KYC_REVIEWS",       "KYC.KYC_REVIEWS.REVIEW_ID",              "PK_KYC_REVIEWS"),
        HasPrimaryKeyRel("KYC.RISK_ASSESSMENTS",  "KYC.RISK_ASSESSMENTS.ASSESSMENT_ID",     "PK_RISK_ASSESSMENTS"),
        HasPrimaryKeyRel("KYC.BENEFICIAL_OWNERS", "KYC.BENEFICIAL_OWNERS.OWNER_ID",         "PK_BENEFICIAL_OWNERS"),
        HasPrimaryKeyRel("KYC.EMPLOYEES",         "KYC.EMPLOYEES.EMPLOYEE_ID",              "PK_EMPLOYEES"),
        HasPrimaryKeyRel("KYC.PEP_STATUS",        "KYC.PEP_STATUS.PEP_ID",                 "PK_PEP_STATUS"),
    ]

    meta.indexes = [
        IndexNode("PK_CUSTOMERS",   S, "CUSTOMERS",    "NORMAL", "UNIQUE",    "CUSTOMER_ID"),
        IndexNode("IDX_CUST_RISK",  S, "CUSTOMERS",    "NORMAL", "NONUNIQUE", "RISK_RATING"),
        IndexNode("PK_ACCOUNTS",    S, "ACCOUNTS",     "NORMAL", "UNIQUE",    "ACCOUNT_ID"),
        IndexNode("IDX_ACCT_CUST",  S, "ACCOUNTS",     "NORMAL", "NONUNIQUE", "CUSTOMER_ID"),
        IndexNode("PK_TXN",         S, "TRANSACTIONS", "NORMAL", "UNIQUE",    "TRANSACTION_ID"),
        IndexNode("IDX_TXN_ACCT",   S, "TRANSACTIONS", "NORMAL", "NONUNIQUE", "ACCOUNT_ID"),
        IndexNode("IDX_TXN_DATE",   S, "TRANSACTIONS", "NORMAL", "NONUNIQUE", "TRANSACTION_DATE"),
        IndexNode("PK_KYC_REVIEWS", S, "KYC_REVIEWS",  "NORMAL", "UNIQUE",    "REVIEW_ID"),
    ]

    meta.views         = []
    meta.constraints   = []
    meta.procedures    = []
    meta.synonyms      = []
    meta.sequences     = []
    meta.view_dependencies = {}
    meta.sample_data   = {}

    graph_cfg = config.graph
    builder = GraphBuilder(graph_cfg)
    builder.build(meta)

    # Enrich with inferred business glossary
    glossary = InferredGlossaryBuilder(builder.graph)
    glossary.build(meta)

    return builder.graph


@st.cache_resource(show_spinner="Initializing pipeline...")
def get_pipeline(_config_hash: str, _api_key: str):
    """Build and cache the LangGraph pipeline."""
    from app_config import AppConfig
    from agent.pipeline import build_pipeline

    config = AppConfig()
    # Override API key if supplied from session state
    if _api_key:
        config.llm_api_key = _api_key

    graph = get_knowledge_graph(_config_hash)

    try:
        pipeline = build_pipeline(graph, config)
        return pipeline
    except Exception as exc:
        st.warning(f"Pipeline initialization warning: {exc}")
        return build_pipeline(graph, config, llm=None)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    """Render the left sidebar: logo, status, settings, schema explorer, history."""
    with st.sidebar:
        # Logo / title
        st.markdown("## KnowledgeQL")
        st.markdown(
            "<div style='color:#6c757d;font-size:0.8rem;margin-top:-0.5rem;'>"
            "NLP-to-SQL for Oracle KYC</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        config = st.session_state.config

        # ----------------------------------------------------------- Status
        st.markdown(
            "<div class='sidebar-section-header'>System Status</div>",
            unsafe_allow_html=True,
        )

        # LLM status
        is_vertex = config.llm_provider.lower() == "vertex"
        has_key = bool(config.llm_api_key) or is_vertex  # Vertex uses ADC, no key needed
        demo = config.demo_mode
        if has_key:
            llm_pill = f"<span class='status-pill pill-green'>{config.llm_provider.upper()} READY</span>"
        elif demo:
            llm_pill = "<span class='status-pill pill-orange'>DEMO MODE</span>"
        else:
            llm_pill = "<span class='status-pill pill-red'>NO API KEY</span>"
        st.markdown(f"**LLM** {llm_pill}", unsafe_allow_html=True)

        # Oracle status
        oracle_connected = _check_oracle_connectivity(config)
        if oracle_connected:
            oracle_pill = "<span class='status-pill pill-green'>CONNECTED</span>"
        elif demo:
            oracle_pill = "<span class='status-pill pill-orange'>MOCK DATA</span>"
        else:
            oracle_pill = "<span class='status-pill pill-red'>DISCONNECTED</span>"
        st.markdown(f"**Oracle** {oracle_pill}", unsafe_allow_html=True)

        # Graph status
        if st.session_state.graph is not None:
            stats = st.session_state.graph.get_stats()
            n_tables = stats.get("Table", 0)
            n_cols = stats.get("Column", 0)
            graph_pill = f"<span class='status-pill pill-blue'>{n_tables} tables, {n_cols} cols</span>"
        else:
            graph_pill = "<span class='status-pill pill-orange'>NOT LOADED</span>"
        st.markdown(f"**Graph** {graph_pill}", unsafe_allow_html=True)

        st.divider()

        # --------------------------------------------------------- Settings
        with st.expander("Settings", expanded=False):
            _providers = ["openai", "anthropic", "vertex"]
            provider = st.selectbox(
                "LLM Provider",
                options=_providers,
                index=_providers.index(config.llm_provider.lower())
                if config.llm_provider.lower() in _providers else 0,
                key="settings_provider",
            )
            model = st.text_input(
                "Model",
                value=config.llm_model,
                placeholder="gpt-4o / claude-sonnet-4-6 / gemini-1.5-pro",
                key="settings_model",
            )
            _is_vertex = (provider == "vertex")
            api_key = st.text_input(
                "API Key",
                value="" if _is_vertex else config.llm_api_key,
                type="password",
                placeholder="Not required for Vertex AI (uses ADC)" if _is_vertex else "sk-... or ant-...",
                disabled=_is_vertex,
                key="settings_api_key",
            )
            demo_mode = st.toggle(
                "Demo Mode (mock Oracle data)",
                value=config.demo_mode,
                key="settings_demo_mode",
            )

            if st.button("Apply Settings", use_container_width=True):
                config.llm_provider = provider
                config.llm_model = model
                config.llm_api_key = api_key
                config.demo_mode = demo_mode
                st.session_state.config = config
                # Clear cached pipeline so it rebuilds with new settings
                get_pipeline.clear()
                st.session_state.pipeline = None
                st.success("Settings applied.")
                st.rerun()

        # ------------------------------------------------------ Schema Explorer
        st.markdown(
            "<div class='sidebar-section-header'>Schema Explorer</div>",
            unsafe_allow_html=True,
        )

        if st.session_state.graph is not None:
            _render_schema_explorer(st.session_state.graph)
        else:
            st.caption("Graph not yet loaded. Submit a query to initialize.")

        # ------------------------------------------------------ Query History
        st.divider()
        st.markdown(
            "<div class='sidebar-section-header'>Query History</div>",
            unsafe_allow_html=True,
        )
        history = st.session_state.query_history[-10:][::-1]  # last 10, newest first
        if history:
            for entry in history:
                ts = entry.get("timestamp", "")
                q = entry.get("query", "")
                rows = entry.get("row_count", 0)
                short_q = q[:45] + "..." if len(q) > 45 else q
                if st.button(
                    f"{short_q}",
                    help=f"Rows: {rows} | {ts}",
                    key=f"hist_{ts}_{q[:10]}",
                    use_container_width=True,
                ):
                    st.session_state.pending_query = q
                    st.rerun()
        else:
            st.caption("No queries yet.")


def _check_oracle_connectivity(config) -> bool:
    """Non-blocking Oracle connectivity check (returns False if unreachable)."""
    try:
        import oracledb
        if not config.oracle.dsn or not config.oracle.user:
            return False
        # Very short timeout check
        conn = oracledb.connect(
            user=config.oracle.user,
            password=config.oracle.password,
            dsn=config.oracle.dsn,
        )
        conn.close()
        return True
    except Exception:
        return False


def _render_schema_explorer(graph) -> None:
    """Render expandable table/column list in the sidebar."""
    from knowledge_graph.traversal import list_all_tables, get_columns_for_table

    tables = list_all_tables(graph, schema="KYC", skip=0, limit=50)

    for table in tables:
        table_name = table.get("name", "")
        row_count = table.get("row_count")
        row_str = f" (~{row_count:,} rows)" if row_count else ""
        with st.expander(f"{table_name}{row_str}", expanded=False):
            cols = get_columns_for_table(graph, table.get("fqn", ""))
            for col in cols:
                col_name = col.get("name", "")
                dtype = col.get("data_type", "")
                flags = []
                if col.get("is_pk"):
                    flags.append("PK")
                if col.get("is_fk"):
                    flags.append("FK")
                if col.get("is_indexed"):
                    flags.append("IDX")
                flag_str = " ".join(f"[{f}]" for f in flags)
                nullable_str = "" if col.get("nullable") == "Y" else " NOT NULL"
                st.markdown(
                    f"<div class='schema-table-row'>"
                    f"<span style='color:#2e86de;font-weight:600;'>{col_name}</span> "
                    f"<span style='color:#6c757d;font-size:0.8rem;'>{dtype}{nullable_str} {flag_str}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            if table.get("comments"):
                st.caption(table["comments"])


# ---------------------------------------------------------------------------
# Chat tab
# ---------------------------------------------------------------------------

def render_chat_tab() -> None:
    """Render the main chat interface."""
    config = st.session_state.config

    # Demo mode banner
    if config.demo_mode:
        st.markdown(
            "<div class='demo-banner'>"
            "Demo mode is ON — SQL is generated but executed against synthetic mock data. "
            "Set a real API key and disable Demo Mode in Settings to use live Oracle."
            "</div>",
            unsafe_allow_html=True,
        )

    # Suggested query chips
    st.markdown("**Try a question:**")
    chip_cols = st.columns(2)
    for i, query in enumerate(_SUGGESTED_QUERIES):
        with chip_cols[i % 2]:
            if st.button(
                query,
                key=f"chip_{i}",
                use_container_width=True,
                help="Click to run this example query",
            ):
                st.session_state.pending_query = query
                st.rerun()

    st.divider()

    # Chat message history
    for msg in st.session_state.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        result = msg.get("result")

        with st.chat_message(role):
            if role == "user":
                st.markdown(content)
            else:
                # Assistant message
                _render_assistant_message(content, result)

    # Handle pending_query (from history or chips)
    if st.session_state.pending_query:
        query = st.session_state.pending_query
        st.session_state.pending_query = None
        _process_query(query)
        return

    # Chat input
    user_input = st.chat_input(
        "Ask a question about your KYC data...",
        key="chat_input",
    )
    if user_input:
        _process_query(user_input)


def _render_assistant_message(content: str, result: Optional[Dict[str, Any]]) -> None:
    """Render an assistant message with optional structured result."""
    st.markdown(content)

    if result is None:
        return

    result_type = result.get("type", "query_result")

    if result_type == "error":
        st.error(f"Error: {result.get('summary', 'Unknown error')}")
        errors = result.get("validation_errors", [])
        if errors:
            with st.expander("Validation errors"):
                for e in errors:
                    st.markdown(f"- {e}")
        sql = result.get("sql", "")
        if sql:
            with st.expander("Generated SQL (with errors)"):
                st.code(sql, language="sql")
        return

    # SQL display
    sql = result.get("sql", "")
    explanation = result.get("explanation", "")
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    total_rows = result.get("total_rows", 0)
    exec_ms = result.get("execution_time_ms", 0)
    source = result.get("data_source", "mock")
    tables_used = result.get("schema_context_tables", [])

    if sql:
        with st.expander("SQL Query", expanded=True):
            st.code(sql, language="sql")
            btn_cols = st.columns([1, 1, 3])
            with btn_cols[0]:
                if st.button("Open in Editor", key=f"edit_{hash(sql)}", help="Open this SQL in the editor tab"):
                    st.session_state.selected_sql = sql
                    st.info("SQL copied to editor tab.")
            if explanation:
                st.caption(f"Explanation: {explanation}")

    # Metrics row
    if total_rows > 0 or source:
        metric_cols = st.columns(4)
        with metric_cols[0]:
            st.metric("Rows", f"{total_rows:,}")
        with metric_cols[1]:
            st.metric("Time", f"{exec_ms / 1000:.2f}s")
        with metric_cols[2]:
            st.metric("Source", source.upper())
        with metric_cols[3]:
            if tables_used:
                st.metric("Tables", ", ".join(tables_used[:2]))

    # Data table
    if columns and rows:
        import pandas as pd
        try:
            df = pd.DataFrame(rows, columns=columns)
            st.dataframe(df, width="stretch", height=300)
        except Exception as exc:
            st.warning(f"Could not render dataframe: {exc}")
            st.json({"columns": columns, "rows": rows[:5]})
    elif total_rows == 0:
        st.info("Query returned no results.")


def _process_query(user_input: str) -> None:
    """Process a user query through the pipeline and update message history."""
    config = st.session_state.config

    # Ensure graph and pipeline are initialized
    config_hash = f"{config.llm_provider}:{config.llm_model}:{config.demo_mode}"
    if st.session_state.graph is None:
        st.session_state.graph = get_knowledge_graph(config_hash)

    if st.session_state.pipeline is None:
        st.session_state.pipeline = get_pipeline(config_hash, config.llm_api_key)

    pipeline = st.session_state.pipeline

    # Add user message
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.messages.append({
        "role": "user",
        "content": user_input,
        "timestamp": ts,
        "result": None,
    })

    # Display user message immediately
    with st.chat_message("user"):
        st.markdown(user_input)

    # Build conversation history for context
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]  # exclude current user message
        if m.get("role") in ("user", "assistant")
    ]

    # Run pipeline
    with st.chat_message("assistant"):
        with st.status("Processing your question...", expanded=True) as status:
            st.write("Classifying intent...")
            t0 = time.time()

            try:
                from agent.pipeline import run_query
                result = run_query(pipeline, user_input, history)
            except Exception as exc:
                result = {
                    "type": "error",
                    "summary": str(exc),
                    "sql": "",
                    "explanation": "",
                    "columns": [],
                    "rows": [],
                    "total_rows": 0,
                    "execution_time_ms": 0,
                    "data_source": "none",
                    "schema_context_tables": [],
                    "validation_errors": [],
                }

            elapsed = time.time() - t0
            status.update(label=f"Done in {elapsed:.1f}s", state="complete")

        # Build summary message
        summary = result.get("summary", "Query completed.")
        _render_assistant_message(summary, result)

    # Save assistant message
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.messages.append({
        "role": "assistant",
        "content": result.get("summary", ""),
        "timestamp": ts,
        "result": result,
    })

    # Add to query history
    st.session_state.query_history.append({
        "query": user_input,
        "timestamp": ts,
        "row_count": result.get("total_rows", 0),
    })

    st.rerun()


# ---------------------------------------------------------------------------
# SQL Editor tab
# ---------------------------------------------------------------------------

def render_sql_editor_tab() -> None:
    """Render the standalone SQL editor tab."""
    st.markdown("### SQL Editor")
    st.markdown("Write or paste Oracle SQL below and run it against the KYC database.")

    config = st.session_state.config
    config_hash = f"{config.llm_provider}:{config.llm_model}:{config.demo_mode}"

    # Initialize graph if needed
    if st.session_state.graph is None:
        st.session_state.graph = get_knowledge_graph(config_hash)

    # SQL text area — pre-populate from chat if "Open in Editor" was clicked
    default_sql = st.session_state.selected_sql or _default_editor_sql()
    sql_input = st.text_area(
        "Oracle SQL",
        value=default_sql,
        height=250,
        key="editor_sql_input",
        help="Enter a SELECT statement. Oracle 12c+ syntax supported.",
    )

    col1, col2, col3 = st.columns([1, 1, 4])
    with col1:
        run_clicked = st.button("Run SQL", type="primary", use_container_width=True)
    with col2:
        format_clicked = st.button("Format SQL", use_container_width=True)

    if format_clicked and sql_input.strip():
        formatted = _format_sql(sql_input)
        st.session_state.selected_sql = formatted
        st.rerun()

    if run_clicked and sql_input.strip():
        _run_editor_sql(sql_input.strip(), config)

    # Show result
    if st.session_state.editor_result:
        result = st.session_state.editor_result
        st.divider()

        if result.get("type") == "error":
            st.error(result.get("summary", "Execution error"))
        else:
            summary = result.get("summary", "")
            if summary:
                st.success(summary)

            columns = result.get("columns", [])
            rows = result.get("rows", [])
            if columns and rows:
                import pandas as pd
                try:
                    df = pd.DataFrame(rows, columns=columns)
                    st.dataframe(df, width="stretch")

                    # Download button
                    csv = df.to_csv(index=False)
                    st.download_button(
                        "Download CSV",
                        data=csv,
                        file_name="query_result.csv",
                        mime="text/csv",
                    )
                except Exception as exc:
                    st.warning(f"Display error: {exc}")
            elif result.get("total_rows", 0) == 0:
                st.info("Query returned no rows.")

            # Add to query history
            if st.button("Save to History", key="save_editor_history"):
                ts = datetime.now().strftime("%H:%M:%S")
                st.session_state.query_history.append({
                    "query": f"[Editor] {sql_input[:80]}",
                    "timestamp": ts,
                    "row_count": result.get("total_rows", 0),
                })
                st.success("Saved to history.")


def _run_editor_sql(sql: str, config) -> None:
    """Execute SQL in the editor using the mock/oracle executor."""
    from agent.nodes.query_executor import _mock_execute, _oracle_execute

    with st.spinner("Executing SQL..."):
        try:
            if config.demo_mode:
                result = _mock_execute(sql)
            else:
                try:
                    result = _oracle_execute(sql, config)
                except Exception as exc:
                    st.warning(f"Oracle execution failed: {exc} — using mock data")
                    result = _mock_execute(sql)

            total_rows = result.get("total_rows", 0)
            exec_ms = result.get("execution_time_ms", 0)
            source = result.get("source", "mock")
            result["type"] = "query_result"
            result["summary"] = (
                f"Returned {total_rows:,} row(s) in {exec_ms / 1000:.2f}s ({source})"
            )
            st.session_state.editor_result = result

        except Exception as exc:
            st.session_state.editor_result = {
                "type": "error",
                "summary": str(exc),
                "columns": [],
                "rows": [],
                "total_rows": 0,
            }


def _format_sql(sql: str) -> str:
    """Attempt to format SQL using sqlglot; return original on failure."""
    try:
        import sqlglot
        formatted = sqlglot.transpile(sql, read="oracle", write="oracle", pretty=True)
        return formatted[0] if formatted else sql
    except Exception:
        return sql


def _default_editor_sql() -> str:
    return (
        "SELECT\n"
        "    c.CUSTOMER_ID,\n"
        "    c.FIRST_NAME || ' ' || c.LAST_NAME AS FULL_NAME,\n"
        "    c.RISK_RATING,\n"
        "    c.NATIONALITY,\n"
        "    e.FIRST_NAME || ' ' || e.LAST_NAME AS ACCOUNT_MANAGER\n"
        "FROM KYC.CUSTOMERS c\n"
        "LEFT JOIN KYC.EMPLOYEES e ON e.EMPLOYEE_ID = c.ACCOUNT_MANAGER_ID\n"
        "WHERE c.RISK_RATING IN ('HIGH', 'VERY_HIGH')\n"
        "ORDER BY c.RISK_RATING DESC, c.LAST_NAME\n"
        "FETCH FIRST 100 ROWS ONLY"
    )


# ---------------------------------------------------------------------------
# Knowledge Graph visualisation tab
# ---------------------------------------------------------------------------

def render_graph_tab() -> None:
    """Interactive network diagram of table relationships from the knowledge graph."""
    import plotly.graph_objects as go
    import networkx as nx
    import pandas as pd

    graph = st.session_state.graph
    if graph is None:
        st.info("The knowledge graph is not yet initialised. Submit a chat query first to load it.")
        return

    # ── Controls ──────────────────────────────────────────────────────────────
    col_hdr, col_ctrl = st.columns([4, 1])
    with col_hdr:
        st.markdown("### Table Relationship Graph")
        st.caption(
            "Nodes = tables · Edges = foreign key relationships detected by the knowledge graph. "
            "Hover a node for table details. Node size reflects the number of connections."
        )
    with col_ctrl:
        show_all = st.toggle("Multi-hop paths", value=False, key="graph_show_all")

    # ── Pull data from the in-memory graph ────────────────────────────────────
    tables = graph.get_all_nodes("Table")
    join_paths = graph.get_all_edges("JOIN_PATH")

    if not tables:
        st.warning("No tables found in the knowledge graph.")
        return

    # Index by FQN — merge_node stores fqn as a property
    table_meta: dict = {t["fqn"]: t for t in tables if t.get("fqn")}

    if not table_meta:
        st.warning("Table nodes do not carry FQN properties — cannot render graph.")
        return

    # Filter: direct FK (weight=1) or all pre-computed paths
    filtered = [
        e for e in join_paths
        if (show_all or e.get("weight", 1) == 1)
        and e.get("_from") in table_meta
        and e.get("_to") in table_meta
    ]

    # Deduplicate bidirectional JOIN_PATH edges
    seen_pairs: set = set()
    unique_edges = []
    for e in filtered:
        key = frozenset([e["_from"], e["_to"]])
        if key not in seen_pairs:
            seen_pairs.add(key)
            unique_edges.append(e)

    # ── NetworkX spring layout ────────────────────────────────────────────────
    G = nx.Graph()
    for fqn in table_meta:
        G.add_node(fqn)
    for e in unique_edges:
        G.add_edge(e["_from"], e["_to"])

    pos = nx.spring_layout(G, seed=42, k=2.5)

    # ── Build Plotly traces ───────────────────────────────────────────────────
    # -- Edge lines
    edge_x: list = []
    edge_y: list = []
    mid_x: list = []
    mid_y: list = []
    mid_labels: list = []

    for e in unique_edges:
        x0, y0 = pos[e["_from"]]
        x1, y1 = pos[e["_to"]]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        mid_x.append((x0 + x1) / 2)
        mid_y.append((y0 + y1) / 2)
        # join_columns is a list of {src, tgt, constraint} dicts
        jcs = e.get("join_columns", [])
        col_strs = [jc.get("src", "").split(".")[-1] for jc in jcs if isinstance(jc, dict)]
        mid_labels.append(", ".join(col_strs) if col_strs else "")

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        mode="lines",
        line=dict(width=1.5, color="#adb5bd"),
        hoverinfo="none",
    )

    # FK column names shown at edge midpoints
    label_trace = go.Scatter(
        x=mid_x, y=mid_y,
        mode="text",
        text=mid_labels,
        textfont=dict(size=9, color="#6c757d"),
        hoverinfo="none",
    )

    # -- Table nodes (sized by degree)
    degrees = dict(G.degree())
    node_x: list = []
    node_y: list = []
    node_text: list = []
    node_hover: list = []
    node_sizes: list = []

    for fqn in G.nodes():
        x, y = pos[fqn]
        node_x.append(x)
        node_y.append(y)
        meta = table_meta[fqn]
        name = meta.get("name", fqn)
        node_text.append(name)
        deg = degrees[fqn]
        rc = meta.get("row_count")
        rows_str = f"{rc:,}" if rc else "—"
        hover = (
            f"<b>{name}</b><br>"
            f"Schema: {meta.get('schema', '')}<br>"
            f"Connections: {deg}<br>"
            f"Est. rows: {rows_str}"
        )
        if meta.get("comments"):
            hover += f"<br><i>{meta['comments']}</i>"
        node_hover.append(hover)
        node_sizes.append(22 + deg * 6)

    node_trace = go.Scatter(
        x=node_x, y=node_y,
        mode="markers+text",
        text=node_text,
        textposition="top center",
        textfont=dict(size=11, color="#1f4e79", family="monospace"),
        hovertext=node_hover,
        hoverinfo="text",
        marker=dict(
            size=node_sizes,
            color="#2e86de",
            opacity=0.85,
            line=dict(width=2, color="white"),
        ),
    )

    fig = go.Figure(
        data=[edge_trace, label_trace, node_trace],
        layout=go.Layout(
            showlegend=False,
            hovermode="closest",
            margin=dict(b=20, l=5, r=5, t=10),
            height=580,
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

    # ── FK relationship table ─────────────────────────────────────────────────
    if unique_edges:
        label = f"Foreign Key Relationships — {len(unique_edges)} connections"
        with st.expander(label, expanded=True):
            rows = []
            for e in sorted(unique_edges, key=lambda e: e.get("_from", "")):
                jcs = e.get("join_columns", [])
                col_strs = [
                    f"{jc.get('src','').split('.')[-1]} → {jc.get('tgt','').split('.')[-1]}"
                    for jc in jcs if isinstance(jc, dict)
                ]
                rows.append({
                    "From Table": table_meta.get(e["_from"], {}).get("name", e["_from"]),
                    "To Table": table_meta.get(e["_to"], {}).get("name", e["_to"]),
                    "Join Columns": "  |  ".join(col_strs),
                    "Hops": int(e.get("weight", 1)),
                    "Cardinality": e.get("cardinality", "—"),
                    "Constraint": jcs[0].get("constraint", "—") if jcs else "—",
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )
    elif not join_paths:
        st.info("No JOIN_PATH edges found. The graph may not have pre-computed join routes yet.")


# ---------------------------------------------------------------------------
# Main app entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main Streamlit application."""
    init_session_state()

    # Initialize graph on first load (non-blocking — happens in background via cache)
    config = st.session_state.config
    config_hash = f"{config.llm_provider}:{config.llm_model}:{config.demo_mode}"

    # Pre-load graph if not yet loaded
    if st.session_state.graph is None:
        try:
            st.session_state.graph = get_knowledge_graph(config_hash)
            st.session_state.graph_initialized = True
        except Exception as exc:
            st.error(f"Failed to initialize knowledge graph: {exc}")

    # Pre-load pipeline if not yet loaded
    if st.session_state.pipeline is None:
        try:
            st.session_state.pipeline = get_pipeline(config_hash, config.llm_api_key)
        except Exception as exc:
            st.warning(f"Pipeline not fully initialized: {exc}")

    # Sidebar
    render_sidebar()

    # Main area
    st.markdown(
        "<h1 class='main-header'>KnowledgeQL</h1>"
        "<p class='main-subtitle'>Ask questions about your KYC data in plain English</p>",
        unsafe_allow_html=True,
    )

    # Tabs
    tab_chat, tab_editor, tab_graph = st.tabs(["Chat", "SQL Editor", "Knowledge Graph"])

    with tab_chat:
        render_chat_tab()

    with tab_editor:
        render_sql_editor_tab()

    with tab_graph:
        render_graph_tab()

    # Footer
    st.markdown(
        "<div class='footer'>KnowledgeQL · Powered by Oracle Knowledge Graph · "
        "Built with LangGraph & Streamlit</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
