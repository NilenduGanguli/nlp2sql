"""
KnowledgeQL — NLP-to-SQL Chat Interface
=========================================
Powered by an in-memory Knowledge Graph of Oracle KYC schema metadata.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger(__name__)

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
    .metric-label { font-size: 0.65rem; color: #6c757d; text-transform: uppercase; }
    .metric-value { font-size: 0.82rem; font-weight: 600; color: #212529; word-break: break-word; }

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

    /* Ensure result dataframes scroll horizontally — Streamlit's chat container
       can clip the default overflow. */
    [data-testid="stDataFrame"] > iframe {
        width: 100% !important;
        min-width: 0;
    }
    [data-testid="stChatMessage"] [data-testid="stDataFrame"] {
        overflow-x: auto !important;
    }
    .stDataFrame { overflow-x: auto !important; }

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

    if "graph_llm_enhanced" not in st.session_state:
        st.session_state.graph_llm_enhanced = False


# ---------------------------------------------------------------------------
# Knowledge graph builder (cached)
# ---------------------------------------------------------------------------

class _GraphBundle:
    """
    Mutable container for the KnowledgeGraph and its LLM-enhancement state.

    Returned by ``get_knowledge_graph()`` and cached by ``@st.cache_resource``,
    which means **all Streamlit sessions share the exact same object**.  Using a
    mutable class (instead of an immutable tuple) lets the LLM-enhancement block
    set ``bundle.llm_enhanced = True`` once and have every subsequent session see
    the updated value — preventing repeated, expensive LLM calls on each new tab.
    """
    __slots__ = ("graph", "llm_enhanced")

    def __init__(self, graph, llm_enhanced: bool = False) -> None:
        self.graph = graph
        self.llm_enhanced = llm_enhanced


@st.cache_resource(show_spinner="Building knowledge graph...")
def get_knowledge_graph(_config_hash: str) -> "_GraphBundle":
    """Build and cache the KnowledgeGraph from live Oracle metadata.

    Load order:
      1. Disk cache (``GRAPH_CACHE_PATH`` / ``~/.cache/knowledgeql``)
      2. Live Oracle build via ``initialize_graph()``
    """
    from knowledge_graph.graph_cache import get_cache_path, load_graph, save_graph
    from knowledge_graph.init_graph import initialize_graph
    from app_config import AppConfig

    config = AppConfig()

    ttl_hours = float(os.getenv("GRAPH_CACHE_TTL_HOURS", "0"))
    cache_path = get_cache_path(config)

    # 1. Try loading from disk cache
    cached = load_graph(cache_path, max_age_hours=ttl_hours)
    if cached is not None:
        graph, llm_enhanced = cached
        return _GraphBundle(graph, llm_enhanced)

    # 2. Cache miss — build from Oracle
    graph, report = initialize_graph(config.graph)
    if report.get("success"):
        save_graph(graph, cache_path, llm_enhanced=False)
        return _GraphBundle(graph, False)

    raise RuntimeError(
        "Knowledge graph initialisation failed — check Oracle connection and app logs."
    )


@st.cache_resource(show_spinner="Initializing pipeline...")
def get_pipeline(_config_hash: str, _api_key: str):
    """Build and cache the LangGraph pipeline."""
    from app_config import AppConfig
    from agent.pipeline import build_pipeline

    config = AppConfig()
    # Override API key if supplied from session state
    if _api_key:
        config.llm_api_key = _api_key

    graph = get_knowledge_graph(_config_hash).graph
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
        if has_key:
            llm_pill = f"<span class='status-pill pill-green'>{config.llm_provider.upper()} READY</span>"
        else:
            llm_pill = "<span class='status-pill pill-red'>NO API KEY</span>"
        st.markdown(f"**LLM** {llm_pill}", unsafe_allow_html=True)

        # Oracle status
        oracle_connected = _check_oracle_connectivity(config)
        if oracle_connected:
            oracle_pill = "<span class='status-pill pill-green'>CONNECTED</span>"
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
            if st.button("Apply Settings", use_container_width=True):
                config.llm_provider = provider
                config.llm_model = model
                config.llm_api_key = api_key
                st.session_state.config = config
                # Clear cached pipeline so it rebuilds with new settings
                get_pipeline.clear()
                st.session_state.pipeline = None
                st.success("Settings applied.")
                st.rerun()

            # ── Graph cache status + force-refresh ────────────────────────────
            from knowledge_graph.graph_cache import (
                cache_info, get_cache_path, invalidate_cache
            )
            _cpath = get_cache_path(config)
            _info  = cache_info(_cpath)
            if _info:
                _enh_icon = "✓" if _info.get("llm_enhanced") else "–"
                _cv = _info.get("cache_version", "1")
                st.caption(
                    f"Cached graph v{_cv}: {_info['age_hours']}h old · "
                    f"{_info['size_mb']} MB · LLM-enhanced: {_enh_icon}"
                )
            else:
                st.caption("No graph cache on disk (will build from Oracle on next load).")

            if st.button("Force Rebuild Graph", use_container_width=True):
                invalidate_cache(_cpath)
                get_knowledge_graph.clear()
                get_pipeline.clear()
                st.session_state.graph = None
                st.session_state.pipeline = None
                st.session_state.graph_initialized = False
                st.session_state.graph_llm_enhanced = False
                st.info("Graph cache cleared — rebuilding from Oracle on next load.")
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
        if config.oracle.thick_mode and oracledb.is_thin_mode():
            oracledb.init_oracle_client()
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

    tables = list_all_tables(graph, schema=None, skip=0, limit=200)

    for table in tables:
        table_name = table.get("name", "")
        schema_name = table.get("schema", "")
        row_count = table.get("row_count")
        row_str = f" (~{row_count:,} rows)" if row_count else ""
        label = f"{schema_name}.{table_name}" if schema_name else table_name
        with st.expander(f"{label}{row_str}", expanded=False):
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
                    # Must update the text_area widget's own session-state key so
                    # Streamlit doesn't ignore the new value on the next rerun.
                    st.session_state["editor_sql_input"] = sql
                    st.rerun()
            if explanation:
                st.caption(f"Explanation: {explanation}")

    # Metrics row
    if total_rows > 0 or source:
        try:
            oracle_svc = st.session_state.config.oracle.dsn.split("/")[-1].upper()
        except Exception:
            oracle_svc = source.upper()

        def _m(label: str, value: str) -> str:
            return (
                f"<div class='metric-card'>"
                f"<div class='metric-label'>{label}</div>"
                f"<div class='metric-value'>{value}</div>"
                f"</div>"
            )

        tables_str = ", ".join(tables_used) if tables_used else "—"
        metric_cols = st.columns(4)
        with metric_cols[0]:
            st.markdown(_m("Rows", f"{total_rows:,}"), unsafe_allow_html=True)
        with metric_cols[1]:
            st.markdown(_m("Time", f"{exec_ms / 1000:.2f}s"), unsafe_allow_html=True)
        with metric_cols[2]:
            st.markdown(_m("Oracle DB", oracle_svc), unsafe_allow_html=True)
        with metric_cols[3]:
            st.markdown(_m("Tables", tables_str), unsafe_allow_html=True)

    # Data table
    if columns and rows:
        import pandas as pd
        try:
            df = pd.DataFrame(rows, columns=columns)
            st.dataframe(df, use_container_width=True, height=300, hide_index=True)
        except Exception as exc:
            st.warning(f"Could not render dataframe: {exc}")
            st.json({"columns": columns, "rows": rows[:5]})
    elif total_rows == 0:
        st.info("Query returned no results.")


def _process_query(user_input: str) -> None:
    """Process a user query through the pipeline and update message history."""
    config = st.session_state.config

    # Ensure graph and pipeline are initialized
    config_hash = f"{config.llm_provider}:{config.llm_model}"
    if st.session_state.graph is None:
        _bundle = get_knowledge_graph(config_hash)
        st.session_state.graph = _bundle.graph
        if _bundle.llm_enhanced:
            st.session_state.graph_llm_enhanced = True

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
    config_hash = f"{config.llm_provider}:{config.llm_model}"

    # Initialize graph if needed
    if st.session_state.graph is None:
        _bundle = get_knowledge_graph(config_hash)
        st.session_state.graph = _bundle.graph
        if _bundle.llm_enhanced:
            st.session_state.graph_llm_enhanced = True

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
        st.session_state["editor_sql_input"] = formatted
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
                    st.dataframe(df, use_container_width=True, hide_index=True)

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
    """Execute SQL in the editor against Oracle."""
    from agent.nodes.query_executor import _oracle_execute

    with st.spinner("Executing SQL..."):
        try:
            result = _oracle_execute(sql, config)
            total_rows = result.get("total_rows", 0)
            exec_ms = result.get("execution_time_ms", 0)
            source = result.get("source", "oracle")
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

    # ── Pull data from the in-memory graph ────────────────────────────────────
    all_tables = graph.get_all_nodes("Table")
    join_paths  = graph.get_all_edges("JOIN_PATH")

    if not all_tables:
        st.warning("No tables found in the knowledge graph.")
        return

    # Index all tables by FQN (uppercase for stable lookups)
    all_table_meta: dict = {t["fqn"]: t for t in all_tables if t.get("fqn")}
    if not all_table_meta:
        st.warning("Table nodes do not carry FQN properties — cannot render graph.")
        return

    # Sorted list of all FQNs for multiselect options
    all_fqns_sorted = sorted(all_table_meta.keys())

    # ── Header + filter controls ───────────────────────────────────────────────
    st.markdown("### Table Relationship Graph")

    col_filter, col_toggle = st.columns([5, 1])

    with col_filter:
        selected_fqns: list = st.multiselect(
            "Filter tables (SCHEMA.TABLE_NAME) — leave empty to show all",
            options=all_fqns_sorted,
            default=[],
            key="graph_table_filter",
            placeholder="e.g. KYC.CUSTOMERS, KYC.ACCOUNTS …",
        )

    with col_toggle:
        st.markdown("&nbsp;", unsafe_allow_html=True)  # vertical alignment spacer
        show_all = st.toggle("Multi-hop paths", value=False, key="graph_show_all")

    # Apply table filter
    if selected_fqns:
        selected_set = set(selected_fqns)
        table_meta = {fqn: meta for fqn, meta in all_table_meta.items() if fqn in selected_set}
        st.caption(
            f"Showing {len(table_meta)} of {len(all_table_meta)} tables. "
            "Only relationships between the selected tables are rendered."
        )
    else:
        table_meta = all_table_meta
        st.caption(
            "Nodes = tables · Edges = foreign key relationships. "
            "Hover a node for table details. Node size reflects the number of connections."
        )

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
        fk_edges = graph.get_all_edges("HAS_FOREIGN_KEY")
        n_tables = len(all_tables)
        n_fks = len(fk_edges)
        if n_fks == 0:
            st.warning(
                f"No foreign key constraints found in the database ({n_tables} table(s) loaded). "
                "JOIN_PATH edges cannot be computed without FK constraints. "
                "The query pipeline will fall back to column-name similarity (SIMILAR_TO edges) "
                "for join inference. Consider adding FK constraints to your Oracle schema."
            )
        else:
            st.info(
                f"Graph has {n_tables} table(s) and {n_fks} FK constraint(s), but no JOIN_PATH "
                "edges have been computed yet. JOIN_PATHs are built during graph initialisation — "
                "try reloading the app or re-running graph initialisation."
            )


# ---------------------------------------------------------------------------
# Relationships tab
# ---------------------------------------------------------------------------

def render_relationships_tab() -> None:
    """Explore FK constraints and join paths between tables."""
    import pandas as pd
    from knowledge_graph.traversal import find_join_path, get_columns_for_table

    graph = st.session_state.graph
    if graph is None:
        st.info("The knowledge graph is not yet initialised. Submit a chat query first to load it.")
        return

    tables = graph.get_all_nodes("Table")
    fk_edges = graph.get_all_edges("HAS_FOREIGN_KEY")
    join_paths = graph.get_all_edges("JOIN_PATH")

    # ── Top metrics ───────────────────────────────────────────────────────────
    m1, m2, m3 = st.columns(3)
    seen_jp: set = set()
    direct_jp = 0
    for e in join_paths:
        if e.get("weight", 1) == 1:
            key = frozenset([e.get("_from"), e.get("_to")])
            if key not in seen_jp:
                seen_jp.add(key)
                direct_jp += 1
    m1.metric("Tables", len(tables))
    m2.metric("FK Constraints", len(fk_edges))
    m3.metric("Direct Join Paths", direct_jp)

    if not fk_edges and not join_paths:
        st.warning(
            "No FK constraints or JOIN_PATH edges found. "
            "The graph cannot infer table relationships without foreign key metadata. "
            "Check that FK constraints are defined in the database (ALL_CONSTRAINTS, type='R')."
        )

    st.divider()

    # ── Section 1: FK Constraint table ───────────────────────────────────────
    with st.expander(f"Foreign Key Constraints ({len(fk_edges)})", expanded=bool(fk_edges)):
        if not fk_edges:
            st.info("No FK constraints found in the knowledge graph.")
        else:
            rows = []
            for e in sorted(fk_edges, key=lambda x: x.get("_from", "")):
                src = e.get("_from", "")   # SCHEMA.TABLE.COL
                tgt = e.get("_to", "")
                src_table, src_col = (src.rsplit(".", 1) + [""])[:2] if "." in src else (src, "")
                tgt_table, tgt_col = (tgt.rsplit(".", 1) + [""])[:2] if "." in tgt else (tgt, "")
                rows.append({
                    "From Table": src_table,
                    "From Column": src_col,
                    "→ To Table": tgt_table,
                    "→ To Column": tgt_col,
                    "Constraint": e.get("constraint_name", ""),
                    "On Delete": e.get("on_delete_action", "NO ACTION"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── Section 2: Join Path Explorer ────────────────────────────────────────
    st.markdown("#### Join Path Explorer")
    st.caption("Select two tables to see the join path and suggested ON clauses.")

    table_fqns = sorted([t.get("fqn", "") for t in tables if t.get("fqn")])
    if len(table_fqns) < 2:
        st.info("At least two tables are required for the join path explorer.")
    else:
        c1, c2 = st.columns(2)
        t1 = c1.selectbox("Table A", table_fqns, key="rel_t1")
        remaining = [f for f in table_fqns if f != t1]
        t2 = c2.selectbox("Table B", remaining, key="rel_t2")

        if t1 and t2:
            path = find_join_path(graph, t1, t2, max_hops=6)
            if path is None:
                st.warning(
                    f"No join path found between **{t1.split('.')[-1]}** and "
                    f"**{t2.split('.')[-1]}** within 6 hops. "
                    "The tables may be unrelated or FK metadata is missing."
                )
            else:
                if path.get("source") == "precomputed":
                    join_cols = path.get("join_columns", [])
                    hops = path.get("weight", len(join_cols))
                    st.success(f"Join path found — {hops} hop(s), precomputed from FK graph")
                    if join_cols:
                        jrows = []
                        for jc in join_cols:
                            src = jc.get("src", "")
                            tgt = jc.get("tgt", "")
                            src_tbl = src.rsplit(".", 1)[0] if "." in src else src
                            src_col_ = src.rsplit(".", 1)[-1] if "." in src else src
                            tgt_tbl = tgt.rsplit(".", 1)[0] if "." in tgt else tgt
                            tgt_col_ = tgt.rsplit(".", 1)[-1] if "." in tgt else tgt
                            jrows.append({
                                "Left Table": src_tbl.split(".")[-1],
                                "Left Column": src_col_,
                                "Right Table": tgt_tbl.split(".")[-1],
                                "Right Column": tgt_col_,
                                "Suggested JOIN": (
                                    f"JOIN {tgt_tbl} ON {src_tbl.split('.')[-1]}.{src_col_}"
                                    f" = {tgt_tbl.split('.')[-1]}.{tgt_col_}"
                                ),
                                "Constraint": jc.get("constraint", ""),
                            })
                        st.dataframe(pd.DataFrame(jrows), use_container_width=True, hide_index=True)
                else:
                    path_nodes = path.get("path_nodes", [])
                    path_edges = path.get("path_edges", [])
                    hops = path.get("hops", len(path_nodes) - 1)
                    st.info(
                        f"Join path found via live traversal — {hops} hop(s): "
                        + " → ".join(n.split(".")[-1] for n in path_nodes)
                    )
                    if path_edges:
                        jrows = []
                        for pe in path_edges:
                            src = pe.get("src_col_fqn", pe.get("src", ""))
                            tgt = pe.get("tgt_col_fqn", pe.get("tgt", ""))
                            jrows.append({
                                "Left Column": src.split(".")[-1] if "." in src else src,
                                "Left Table": src.rsplit(".", 1)[0].split(".")[-1] if "." in src else "",
                                "Right Column": tgt.split(".")[-1] if "." in tgt else tgt,
                                "Right Table": tgt.rsplit(".", 1)[0].split(".")[-1] if "." in tgt else "",
                                "Constraint": pe.get("constraint_name", pe.get("constraint", "")),
                            })
                        st.dataframe(pd.DataFrame(jrows), use_container_width=True, hide_index=True)

    # ── Section 3: Column Browser ─────────────────────────────────────────────
    st.divider()
    st.markdown("#### Column Browser")
    st.caption("Inspect all columns, types, and key flags for any table.")

    if table_fqns:
        selected_tbl = st.selectbox("Select table", table_fqns, key="rel_col_browser")
        if selected_tbl:
            cols = get_columns_for_table(graph, selected_tbl)
            if cols:
                col_rows = []
                for c in cols:
                    flags = []
                    if c.get("is_pk"): flags.append("PK")
                    if c.get("is_fk"): flags.append("FK")
                    if c.get("is_indexed"): flags.append("IDX")
                    col_rows.append({
                        "Column": c.get("name", ""),
                        "Data Type": c.get("data_type", ""),
                        "Nullable": "Yes" if c.get("nullable") == "Y" else "No",
                        "Flags": "  ".join(f"[{f}]" for f in flags),
                        "Comments": (c.get("comments") or "").strip(),
                    })
                st.dataframe(pd.DataFrame(col_rows), use_container_width=True, hide_index=True)
            else:
                st.warning(f"No columns found for {selected_tbl}.")


# ---------------------------------------------------------------------------
# Main app entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main Streamlit application."""
    init_session_state()

    # Initialize graph on first load (non-blocking — happens in background via cache)
    config = st.session_state.config
    config_hash = f"{config.llm_provider}:{config.llm_model}"

    # Pre-load graph if not yet loaded
    if st.session_state.graph is None:
        try:
            _bundle = get_knowledge_graph(config_hash)
            st.session_state.graph = _bundle.graph
            st.session_state.graph_initialized = True
            if _bundle.llm_enhanced:
                st.session_state.graph_llm_enhanced = True
        except Exception as exc:
            st.error(f"Failed to initialize knowledge graph: {exc}")

    # Pre-load pipeline if not yet loaded
    if st.session_state.pipeline is None:
        try:
            st.session_state.pipeline = get_pipeline(config_hash, config.llm_api_key)
        except Exception as exc:
            st.warning(f"Pipeline not fully initialized: {exc}")

    # LLM graph enhancement — runs once per process after graph + pipeline are ready.
    # Uses the LLM to rank tables by importance, infer missing FK relationships,
    # and generate descriptions for undocumented tables.
    #
    # The guard uses the shared _GraphBundle.llm_enhanced flag (not just session
    # state) so that if enhancement already ran in another session tab within this
    # process it is not repeated.  After completion, bundle.llm_enhanced is mutated
    # to True — all future sessions immediately see the up-to-date flag.
    _provider = getattr(config, "llm_provider", "").lower()
    _has_creds = bool(getattr(config, "llm_api_key", "")) or (_provider == "vertex")
    _bundle_ref = get_knowledge_graph(config_hash)  # always a fast in-memory lookup
    if (
        _bundle_ref is not None
        and not _bundle_ref.llm_enhanced          # shared across sessions
        and not st.session_state.graph_llm_enhanced  # per-session safety net
        and _has_creds
    ):
        try:
            from knowledge_graph.llm_enhancer import enhance_graph_with_llm
            from knowledge_graph.graph_cache import get_cache_path, save_graph
            from agent.llm import get_llm
            with st.spinner("Enhancing schema graph with LLM (ranking tables, inferring relationships)…"):
                _enhance_llm = get_llm(config)
                _enh_report = enhance_graph_with_llm(st.session_state.graph, _enhance_llm)
            # Mutate the shared bundle first — all other sessions see this immediately.
            _bundle_ref.llm_enhanced = True
            st.session_state.graph_llm_enhanced = True
            logger.info("LLM graph enhancement done: %s", _enh_report)
            # Persist to disk so future process restarts skip enhancement too.
            _cache_path = get_cache_path(config)
            save_graph(st.session_state.graph, _cache_path, llm_enhanced=True)
        except Exception as _enh_exc:
            logger.warning("LLM graph enhancement skipped: %s", _enh_exc)
            # Mark as done in bundle and session to prevent retry loops.
            _bundle_ref.llm_enhanced = True
            st.session_state.graph_llm_enhanced = True

    # Sidebar
    render_sidebar()

    # Main area
    st.markdown(
        "<h1 class='main-header'>KnowledgeQL</h1>"
        "<p class='main-subtitle'>Ask questions about your KYC data in plain English</p>",
        unsafe_allow_html=True,
    )

    # Tabs
    tab_chat, tab_editor, tab_graph, tab_rel = st.tabs(
        ["Chat", "SQL Editor", "Knowledge Graph", "Relationships"]
    )

    with tab_chat:
        render_chat_tab()

    with tab_editor:
        render_sql_editor_tab()

    with tab_graph:
        render_graph_tab()

    with tab_rel:
        render_relationships_tab()

    # Footer
    st.markdown(
        "<div class='footer'>KnowledgeQL · Powered by Oracle Knowledge Graph · "
        "Built with LangGraph & Streamlit</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
