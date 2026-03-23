"""
Typed dataclasses representing every node label and relationship type
in the KnowledgeQL knowledge graph.

Naming convention
-----------------
  *Node    – represents a graph node label
  *Rel     – represents a graph relationship type

Each class exposes a ``to_cypher_params()`` method that returns a plain
dictionary of properties suitable for storing in the KnowledgeGraph.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fqn(schema: str, *parts: str) -> str:
    """Build a fully-qualified name key: SCHEMA.TABLE or SCHEMA.TABLE.COLUMN."""
    return ".".join(p.upper() for p in [schema, *parts])


# ---------------------------------------------------------------------------
# Node Types
# ---------------------------------------------------------------------------

@dataclass
class SchemaNode:
    """
    Node label: Schema
    Represents an Oracle schema/user namespace — the top-level container.

    Inferred from: DBA_USERS / distinct owner values in DBA_TABLES
    """
    name: str                          # Schema/owner name (UPPER)
    owner: Optional[str] = None        # Same as name for Oracle schemas
    created_date: Optional[str] = None # ISO-8601 string

    @property
    def node_id(self) -> str:
        return self.name.upper()

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "name": self.name.upper(),
            "owner": (self.owner or self.name).upper(),
            "created_date": self.created_date,
        }


@dataclass
class TableNode:
    """
    Node label: Table
    A physical Oracle table (TABLE, IOT, EXTERNAL) or an object table.

    Inferred from: DBA_TABLES + DBA_TAB_COMMENTS
    """
    schema: str
    name: str
    row_count: Optional[int] = None
    avg_row_length: Optional[int] = None
    last_analyzed: Optional[str] = None
    table_type: str = "TABLE"          # TABLE | IOT | EXTERNAL
    partitioned: str = "NO"
    temporary: str = "N"
    comments: Optional[str] = None
    # Populated after sample data collection
    sample_data: Optional[List[Dict[str, Any]]] = field(default=None, repr=False)

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.name)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "schema": self.schema.upper(),
            "row_count": self.row_count,
            "avg_row_length": self.avg_row_length,
            "last_analyzed": self.last_analyzed,
            "table_type": self.table_type,
            "partitioned": self.partitioned,
            "temporary": self.temporary,
            "comments": self.comments,
        }


@dataclass
class ColumnNode:
    """
    Node label: Column
    A single column within a table.

    Inferred from: DBA_TAB_COLUMNS + DBA_COL_COMMENTS + DBA_TAB_COL_STATISTICS
    PK/FK flags are set by the graph builder after processing constraints.
    """
    schema: str
    table_name: str
    name: str
    data_type: str
    data_length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    nullable: str = "Y"
    default_value: Optional[str] = None
    column_id: int = 0
    comments: Optional[str] = None
    # Statistics from DBA_TAB_COL_STATISTICS
    num_distinct: Optional[int] = None
    histogram_type: Optional[str] = None
    # Populated from sample data
    sample_values: Optional[List[Any]] = field(default=None, repr=False)
    # Flags set by graph builder
    is_pk: bool = False
    is_fk: bool = False
    is_indexed: bool = False

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.table_name, self.name)

    @property
    def table_fqn(self) -> str:
        return _fqn(self.schema, self.table_name)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "table_name": self.table_name.upper(),
            "schema": self.schema.upper(),
            "table_fqn": self.table_fqn,
            "data_type": self.data_type,
            "data_length": self.data_length,
            "precision": self.precision,
            "scale": self.scale,
            "nullable": self.nullable,
            "default_value": self.default_value,
            "column_id": self.column_id,
            "comments": self.comments,
            "num_distinct": self.num_distinct,
            "histogram_type": self.histogram_type,
            "sample_values": self.sample_values or [],
            "is_pk": self.is_pk,
            "is_fk": self.is_fk,
            "is_indexed": self.is_indexed,
        }


@dataclass
class ViewNode:
    """
    Node label: View
    An Oracle view or materialized view.

    Inferred from: DBA_VIEWS / DBA_MVIEWS + DBA_TAB_COMMENTS
    Edges to base tables inferred from: DBA_DEPENDENCIES
    """
    name: str
    schema: str
    view_text: Optional[str] = None   # Original DDL (may be long)
    is_materialized: bool = False
    refresh_mode: Optional[str] = None     # FAST | COMPLETE | FORCE | NEVER
    last_refresh: Optional[str] = None
    comments: Optional[str] = None

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.name)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "schema": self.schema.upper(),
            # Truncate very long view text to avoid oversized property values
            "view_text": (self.view_text or "")[:4000],
            "is_materialized": self.is_materialized,
            "refresh_mode": self.refresh_mode,
            "last_refresh": self.last_refresh,
            "comments": self.comments,
        }


@dataclass
class IndexNode:
    """
    Node label: Index
    B-tree, bitmap, or function-based index.

    Inferred from: DBA_INDEXES + DBA_IND_COLUMNS
    """
    name: str
    schema: str
    table_name: str
    index_type: str = "NORMAL"        # NORMAL | BITMAP | FUNCTION-BASED NORMAL
    uniqueness: str = "NONUNIQUE"     # UNIQUE | NONUNIQUE
    columns_list: str = ""            # CSV of column names in key order
    tablespace: Optional[str] = None
    compression: str = "DISABLED"

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.name)

    @property
    def table_fqn(self) -> str:
        return _fqn(self.schema, self.table_name)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "schema": self.schema.upper(),
            "table_name": self.table_name.upper(),
            "table_fqn": self.table_fqn,
            "index_type": self.index_type,
            "uniqueness": self.uniqueness,
            "columns_list": self.columns_list,
            "tablespace": self.tablespace,
            "compression": self.compression,
        }


@dataclass
class ConstraintNode:
    """
    Node label: Constraint
    PK, FK, UNIQUE, or CHECK constraint.

    Inferred from: DBA_CONSTRAINTS + DBA_CONS_COLUMNS
    FK constraints additionally generate HAS_FOREIGN_KEY edges between Column nodes.
    """
    name: str
    schema: str
    table_name: str
    constraint_type: str               # P | R | U | C
    condition: Optional[str] = None    # CHECK condition text
    status: str = "ENABLED"
    validated: str = "VALIDATED"

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.name)

    @property
    def table_fqn(self) -> str:
        return _fqn(self.schema, self.table_name)

    @property
    def type_label(self) -> str:
        mapping = {"P": "PRIMARY_KEY", "R": "FOREIGN_KEY", "U": "UNIQUE", "C": "CHECK"}
        return mapping.get(self.constraint_type, self.constraint_type)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "schema": self.schema.upper(),
            "table_name": self.table_name.upper(),
            "table_fqn": self.table_fqn,
            "type": self.type_label,
            "condition": self.condition,
            "status": self.status,
            "validated": self.validated,
        }


@dataclass
class ProcedureNode:
    """
    Node label: Procedure
    Stored procedure, function, or package.

    Inferred from: DBA_PROCEDURES
    CALLS edges to tables/views are inferred from DBA_DEPENDENCIES.
    """
    name: str
    schema: str
    proc_type: str = "PROCEDURE"       # PROCEDURE | FUNCTION | PACKAGE
    parameters: Optional[str] = None   # JSON string of parameter list
    return_type: Optional[str] = None
    body_summary: Optional[str] = None # First 500 chars of body
    status: str = "VALID"

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.name)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "schema": self.schema.upper(),
            "type": self.proc_type,
            "parameters": self.parameters,
            "return_type": self.return_type,
            "body_summary": self.body_summary,
            "status": self.status,
        }


@dataclass
class SynonymNode:
    """
    Node label: Synonym
    Public or private synonym for name resolution.

    Inferred from: DBA_SYNONYMS
    """
    name: str
    schema: str
    target_schema: str
    target_object: str

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.name)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "schema": self.schema.upper(),
            "target_schema": self.target_schema.upper(),
            "target_object": self.target_object.upper(),
        }


@dataclass
class SequenceNode:
    """
    Node label: Sequence
    Oracle sequence used for surrogate key generation.

    Inferred from: DBA_SEQUENCES
    """
    name: str
    schema: str
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    increment_by: int = 1
    cache_size: int = 20

    @property
    def fqn(self) -> str:
        return _fqn(self.schema, self.name)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "fqn": self.fqn,
            "name": self.name.upper(),
            "schema": self.schema.upper(),
            "min_value": self.min_value,
            "max_value": self.max_value,
            "increment_by": self.increment_by,
            "cache_size": self.cache_size,
        }


@dataclass
class BusinessTermNode:
    """
    Node label: BusinessTerm
    A domain-specific business term from the KYC glossary.

    Loaded from the configurable glossary JSON file.
    MAPS_TO edges connect business terms to Column or Table nodes.
    """
    term: str
    definition: str
    aliases: List[str] = field(default_factory=list)
    domain: str = "KYC"
    sensitivity_level: str = "INTERNAL"  # PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "definition": self.definition,
            "aliases": self.aliases,
            "domain": self.domain,
            "sensitivity_level": self.sensitivity_level,
        }


@dataclass
class QueryPatternNode:
    """
    Node label: QueryPattern
    A reusable query pattern learned from query history, used as few-shot examples.

    Inserted during system operation (not during initial graph construction).
    """
    pattern_id: str
    description: str
    sql_template: str
    frequency: int = 1
    avg_execution_time_ms: float = 0.0
    tags: List[str] = field(default_factory=list)

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "sql_template": self.sql_template,
            "frequency": self.frequency,
            "avg_execution_time_ms": self.avg_execution_time_ms,
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Relationship Types
# ---------------------------------------------------------------------------

@dataclass
class BelongsToRel:
    """Table → Schema. Schema containment relationship."""
    table_fqn: str
    schema_name: str


@dataclass
class HasColumnRel:
    """Table → Column. Column ownership."""
    table_fqn: str
    column_fqn: str
    ordinal_position: int

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "table_fqn": self.table_fqn,
            "column_fqn": self.column_fqn,
            "ordinal_position": self.ordinal_position,
        }


@dataclass
class HasPrimaryKeyRel:
    """Table → Column. Marks a column as part of the primary key."""
    table_fqn: str
    column_fqn: str
    constraint_name: str
    key_position: int = 1


@dataclass
class HasForeignKeyRel:
    """
    Column → Column. Foreign key reference.
    source_col_fqn is the referencing column; target_col_fqn is the referenced PK column.

    Inferred from: DBA_CONSTRAINTS (type='R') + DBA_CONS_COLUMNS.
    This is the primary edge used for JOIN_PATH computation and join condition generation.
    """
    source_col_fqn: str   # e.g. KYC.ACCOUNTS.CUSTOMER_ID
    target_col_fqn: str   # e.g. KYC.CUSTOMERS.CUSTOMER_ID
    constraint_name: str
    on_delete_action: str = "NO ACTION"  # NO ACTION | CASCADE | SET NULL

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "source_col_fqn": self.source_col_fqn,
            "target_col_fqn": self.target_col_fqn,
            "constraint_name": self.constraint_name,
            "on_delete_action": self.on_delete_action,
        }


@dataclass
class HasIndexRel:
    """Table → Index."""
    table_fqn: str
    index_fqn: str


@dataclass
class IndexedByRel:
    """Column → Index. Records the column's position in the index key."""
    column_fqn: str
    index_fqn: str
    column_position: int


@dataclass
class HasConstraintRel:
    """Table → Constraint."""
    table_fqn: str
    constraint_fqn: str


@dataclass
class DependsOnRel:
    """View → Table/View. Dependency from DBA_DEPENDENCIES."""
    view_fqn: str
    target_fqn: str
    dependency_type: str = "SELECT"  # SELECT | INSERT | UPDATE | DELETE


@dataclass
class CallsRel:
    """Procedure → Table/View/Procedure. Data access pattern."""
    procedure_fqn: str
    target_fqn: str
    operation_type: str = "SELECT"


@dataclass
class MapsToRel:
    """
    BusinessTerm → Column | Table.
    Business glossary mapping with confidence scoring.

    mapping_type: 'exact' | 'fuzzy' | 'semantic' | 'manual'
    """
    term: str
    target_fqn: str
    target_label: str          # "Table" or "Column"
    confidence: float = 1.0    # 0.0 – 1.0
    mapping_type: str = "exact"

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "target_fqn": self.target_fqn,
            "confidence": self.confidence,
            "mapping_type": self.mapping_type,
        }


@dataclass
class JoinPathRel:
    """
    Table → Table. Pre-computed optimal join path.

    join_columns: list of dicts {"src_col": "...", "tgt_col": "..."}
    Inferred via BFS over HAS_FOREIGN_KEY edges; weight = sum of FK hops.
    """
    source_table_fqn: str
    target_table_fqn: str
    join_columns: List[Dict[str, str]]  # [{"src": "...", "tgt": "..."}]
    join_type: str = "INNER"
    cardinality: str = "N:1"  # 1:1 | N:1 | 1:N | N:M
    weight: int = 1            # Number of FK hops in the path

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "source_table_fqn": self.source_table_fqn,
            "target_table_fqn": self.target_table_fqn,
            "join_columns": self.join_columns,
            "join_type": self.join_type,
            "cardinality": self.cardinality,
            "weight": self.weight,
            "path_key": f"{self.source_table_fqn}>>{self.target_table_fqn}",
        }


@dataclass
class SimilarToRel:
    """
    Column → Column. Columns with similar names across tables that likely
    represent the same concept (e.g., customer_id in ACCOUNTS and ORDERS).

    Inferred via:
      1. Exact name match across tables
      2. Common suffix patterns (_ID, _CODE, _KEY, _NO, _NUM)
      3. Levenshtein edit distance ≤ threshold
    """
    source_col_fqn: str
    target_col_fqn: str
    similarity_score: float    # 0.0 – 1.0
    match_type: str            # exact | suffix | levenshtein | semantic

    def to_cypher_params(self) -> Dict[str, Any]:
        return {
            "source_col_fqn": self.source_col_fqn,
            "target_col_fqn": self.target_col_fqn,
            "similarity_score": self.similarity_score,
            "match_type": self.match_type,
        }
