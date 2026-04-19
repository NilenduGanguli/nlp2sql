"""Pydantic request/response models for the KnowledgeQL API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    user_input: str
    conversation_history: List[Dict[str, Any]] = []
    previous_sql_context: Optional[Dict[str, Any]] = None
    auto_execute: bool = False


class ExecuteConfirmedSqlRequest(BaseModel):
    """Execute a user-confirmed SQL query (skip full pipeline)."""
    sql: str
    user_input: str = ""
    conversation_history: List[Dict[str, Any]] = []


class ExecuteCandidateRequest(BaseModel):
    """Execute a user-selected SQL candidate (validate → optimize → present)."""
    sql: str
    explanation: str = ""
    user_input: str = ""
    conversation_history: List[Dict[str, Any]] = []


class SQLExecuteRequest(BaseModel):
    sql: str


class SQLFormatRequest(BaseModel):
    sql: str


# ---------------------------------------------------------------------------
# Schema response models
# ---------------------------------------------------------------------------

class ColumnDetail(BaseModel):
    name: str
    data_type: str = ""
    nullable: Optional[str] = None
    comments: Optional[str] = None
    is_pk: bool = False
    is_fk: bool = False
    is_indexed: bool = False
    column_id: Optional[int] = None
    data_length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None


class ForeignKeyRef(BaseModel):
    fk_col: str
    ref_table: str
    ref_col: str
    constraint_name: str = ""


class TableSummary(BaseModel):
    fqn: str
    name: str
    schema_name: str              # renamed from "schema" to avoid Pydantic BaseModel shadowing
    row_count: Optional[int] = None
    table_type: str = "TABLE"
    comments: Optional[str] = None
    partitioned: str = "NO"
    importance_tier: Optional[str] = None
    importance_rank: Optional[int] = None
    llm_description: Optional[str] = None
    column_count: int = 0


class TableDetail(BaseModel):
    fqn: str
    name: str
    schema_name: str
    row_count: Optional[int] = None
    table_type: str = "TABLE"
    comments: Optional[str] = None
    importance_tier: Optional[str] = None
    importance_rank: Optional[int] = None
    llm_description: Optional[str] = None
    columns: List[ColumnDetail] = []
    foreign_keys: List[ForeignKeyRef] = []
    constraints: List[Dict[str, Any]] = []


class TablesPage(BaseModel):
    items: List[TableSummary]
    total: int
    page: int
    pages: int
    page_size: int


class SchemaStats(BaseModel):
    table_count: int
    column_count: int
    fk_count: int
    join_path_count: int
    schemas: List[str]
    llm_enhanced: bool


class SearchResult(BaseModel):
    label: str
    fqn: str
    name: str
    schema_name: str
    description: Optional[str] = None
    match_score: float = 1.0


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult]


# ---------------------------------------------------------------------------
# Graph response models
# ---------------------------------------------------------------------------

class GraphNode(BaseModel):
    id: str
    label: str
    group: str          # "core" | "reference" | "audit" | "utility" | "unknown"
    name: str
    schema_name: str
    importance_rank: Optional[int] = None
    row_count: Optional[int] = None
    comments: Optional[str] = None


class JoinColumnDetail(BaseModel):
    from_col: str                       # short name, e.g. "CUSTOMER_ID"
    to_col: str
    from_col_fqn: str = ""             # fully-qualified, e.g. "KYC.ACCOUNTS.CUSTOMER_ID"
    to_col_fqn: str = ""
    from_col_type: Optional[str] = None
    to_col_type: Optional[str] = None
    from_col_comments: Optional[str] = None
    to_col_comments: Optional[str] = None
    constraint_name: str = ""
    on_delete_action: str = ""


class GraphEdge(BaseModel):
    id: str
    from_id: str
    to_id: str
    rel_type: str
    weight: float = 1.0
    source: str = "precomputed"   # "precomputed" | "llm_inferred" | "fk_constraint"
    # Rich join column details — populated for JOIN_PATH edges when FK info is available
    join_columns: List[JoinColumnDetail] = []
    join_type: Optional[str] = None
    cardinality: Optional[str] = None


class GraphVisualization(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    total_tables: int
    shown_tables: int


class JoinPathResult(BaseModel):
    found: bool
    from_table: str
    to_table: str
    join_columns: List[Dict[str, Any]] = []
    join_type: Optional[str] = None
    hops: int = 0
    source: str = ""
    sql_snippet: Optional[str] = None


class ForeignKeyEdge(BaseModel):
    from_table: str
    to_table: str
    from_col: str
    to_col: str
    constraint_name: str = ""


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str          # "ok" | "degraded" | "initializing"
    graph_loaded: bool
    graph_tables: int
    graph_columns: int
    llm_ready: bool
    llm_enhanced: bool
    oracle_connected: bool
    knowledge_file_ready: bool


class SQLExecuteResponse(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    total_rows: int
    execution_time_ms: int
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class RebuildResponse(BaseModel):
    status: str
    message: str


class CacheInfoResponse(BaseModel):
    path: str
    exists: bool
    created_at: Optional[float] = None
    age_hours: Optional[float] = None
    llm_enhanced: Optional[bool] = None
    size_mb: Optional[float] = None
    version: Optional[str] = None


class ConfigResponse(BaseModel):
    llm_provider: str
    llm_model: str
    has_api_key: bool
    vertex_project: str = ""
    vertex_location: str = "us-central1"


class ConfigUpdateRequest(BaseModel):
    llm_provider: str
    llm_model: str
    llm_api_key: str = ""


class KnowledgeFileResponse(BaseModel):
    content: str
    path: str
    size_bytes: int
    enricher_enabled: bool
