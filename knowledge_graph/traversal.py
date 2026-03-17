"""
Knowledge Graph Traversal Queries
===================================
Parameterised Cypher query patterns used by the Agent Orchestration layer
at query time to retrieve schema context from Neo4j.

All functions accept an open neo4j.Session (or neo4j.AsyncSession) and
return typed Python objects or plain dicts.

Pattern catalogue
-----------------
  get_columns_for_table        – All columns for a given table (ordered)
  get_table_detail             – Full table context including constraints
  find_join_path               – Shortest FK-based join path between two tables
  resolve_business_term        – Map a natural-language term to schema nodes
  get_context_subgraph         – Full DDL context for a set of table names
  search_schema                – Fuzzy-text search over tables and columns
  list_all_tables              – Paginated table listing for schema explorer
  get_index_hints              – Indexes available for a column set
  get_view_lineage             – Tables a view depends on
  get_procedure_calls          – Tables/views a procedure accesses
  get_query_patterns           – Few-shot examples for a given table set
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Get all columns for a table (ordered by column_id)
# ---------------------------------------------------------------------------

GET_COLUMNS_FOR_TABLE = """
MATCH (t:Table {fqn: $table_fqn})-[r:HAS_COLUMN]->(c:Column)
RETURN
    c.fqn            AS fqn,
    c.name           AS name,
    c.data_type      AS data_type,
    c.data_length    AS data_length,
    c.precision      AS precision,
    c.scale          AS scale,
    c.nullable       AS nullable,
    c.default_value  AS default_value,
    c.column_id      AS column_id,
    c.comments       AS comments,
    c.is_pk          AS is_pk,
    c.is_fk          AS is_fk,
    c.is_indexed     AS is_indexed,
    c.sample_values  AS sample_values,
    c.num_distinct   AS num_distinct
ORDER BY c.column_id
"""


def get_columns_for_table(session: Any, table_fqn: str) -> List[Dict[str, Any]]:
    """Return an ordered list of column dicts for *table_fqn*."""
    result = session.run(GET_COLUMNS_FOR_TABLE, table_fqn=table_fqn.upper())
    return [dict(record) for record in result]


# ---------------------------------------------------------------------------
# 2. Get full table detail (table + columns + constraints + FK edges)
# ---------------------------------------------------------------------------

GET_TABLE_DETAIL = """
MATCH (t:Table {fqn: $table_fqn})
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
OPTIONAL MATCH (t)-[:HAS_CONSTRAINT]->(con:Constraint)
OPTIONAL MATCH (c)-[fk:HAS_FOREIGN_KEY]->(ref_col:Column)<-[:HAS_COLUMN]-(ref_table:Table)
RETURN
    t                  AS table,
    collect(DISTINCT c)  AS columns,
    collect(DISTINCT con) AS constraints,
    collect(DISTINCT {
        fk_col: c.name,
        ref_table: ref_table.name,
        ref_col: ref_col.name,
        constraint_name: fk.constraint_name
    }) AS foreign_keys
"""


def get_table_detail(session: Any, table_fqn: str) -> Optional[Dict[str, Any]]:
    """Return a rich dict describing the table including all columns, constraints, and FKs."""
    result = session.run(GET_TABLE_DETAIL, table_fqn=table_fqn.upper())
    record = result.single()
    if record is None:
        return None
    return {
        "table": dict(record["table"]),
        "columns": [dict(c) for c in record["columns"] if c],
        "constraints": [dict(con) for con in record["constraints"] if con],
        "foreign_keys": [fk for fk in record["foreign_keys"] if fk.get("ref_table")],
    }


# ---------------------------------------------------------------------------
# 3. Find shortest join path between two tables (via FK edges)
# ---------------------------------------------------------------------------

FIND_JOIN_PATH_PRECOMPUTED = """
MATCH (t1:Table {fqn: $table1_fqn})-[jp:JOIN_PATH]->(t2:Table {fqn: $table2_fqn})
RETURN jp.join_columns AS join_columns,
       jp.join_type    AS join_type,
       jp.cardinality  AS cardinality,
       jp.weight       AS weight
ORDER BY jp.weight ASC
LIMIT 1
"""

FIND_JOIN_PATH_TRAVERSAL = """
MATCH path = shortestPath(
    (t1:Table {fqn: $table1_fqn})-[:HAS_FOREIGN_KEY*..{max_hops}]-(t2:Table {fqn: $table2_fqn})
)
RETURN
    [node IN nodes(path) | node.fqn] AS path_nodes,
    [rel  IN relationships(path) | {
        type: type(rel),
        src: startNode(rel).fqn,
        tgt: endNode(rel).fqn,
        constraint: rel.constraint_name
    }] AS path_edges,
    length(path) AS hops
"""


def find_join_path(
    session: Any,
    table1_fqn: str,
    table2_fqn: str,
    max_hops: int = 4,
) -> Optional[Dict[str, Any]]:
    """
    Return the optimal join path between two tables.
    First checks pre-computed JOIN_PATH edges, then falls back to live traversal.
    """
    # Try pre-computed path first (fast)
    result = session.run(
        FIND_JOIN_PATH_PRECOMPUTED,
        table1_fqn=table1_fqn.upper(),
        table2_fqn=table2_fqn.upper(),
    )
    record = result.single()
    if record:
        return {
            "join_columns": record["join_columns"],
            "join_type": record["join_type"],
            "cardinality": record["cardinality"],
            "weight": record["weight"],
            "source": "precomputed",
        }

    # Fallback: live Cypher traversal
    traversal_cypher = FIND_JOIN_PATH_TRAVERSAL.replace(
        "{max_hops}", str(max_hops)
    )
    result = session.run(
        traversal_cypher,
        table1_fqn=table1_fqn.upper(),
        table2_fqn=table2_fqn.upper(),
    )
    record = result.single()
    if record:
        return {
            "path_nodes": record["path_nodes"],
            "path_edges": record["path_edges"],
            "hops": record["hops"],
            "source": "traversal",
        }

    return None


# ---------------------------------------------------------------------------
# 4. Resolve a business term to schema nodes (table or column)
# ---------------------------------------------------------------------------

RESOLVE_BUSINESS_TERM = """
MATCH (bt:BusinessTerm)-[m:MAPS_TO]->(target)
WHERE bt.term =~ $search_pattern
   OR ANY(alias IN bt.aliases WHERE alias =~ $search_pattern)
RETURN
    bt.term            AS term,
    bt.definition      AS definition,
    bt.sensitivity_level AS sensitivity_level,
    labels(target)     AS target_labels,
    target.fqn         AS target_fqn,
    target.name        AS target_name,
    m.confidence       AS confidence,
    m.mapping_type     AS mapping_type
ORDER BY m.confidence DESC
"""

SEARCH_TERM_BY_NAME = """
MATCH (t:Table)
WHERE toLower(t.name) CONTAINS toLower($term)
   OR toLower(t.comments) CONTAINS toLower($term)
WITH t, 0.7 AS score, 'table_name' AS match_type
RETURN
    t.fqn     AS fqn,
    t.name    AS name,
    t.schema  AS schema,
    score     AS score,
    match_type AS match_type,
    'Table'   AS label
UNION
MATCH (c:Column)
WHERE toLower(c.name) CONTAINS toLower($term)
   OR toLower(c.comments) CONTAINS toLower($term)
WITH c, 0.6 AS score, 'column_name' AS match_type
RETURN
    c.fqn        AS fqn,
    c.name       AS name,
    c.table_name AS schema,
    score        AS score,
    match_type   AS match_type,
    'Column'     AS label
ORDER BY score DESC
LIMIT 20
"""


def resolve_business_term(session: Any, term: str) -> List[Dict[str, Any]]:
    """
    Map a natural-language term to schema elements.
    Tries the BusinessTerm glossary first, then falls back to name-based search.
    """
    # Escape for regex: anchor as case-insensitive substring
    escaped = term.replace("(", r"\(").replace(")", r"\)")
    pattern = f"(?i).*{escaped}.*"

    result = session.run(RESOLVE_BUSINESS_TERM, search_pattern=pattern)
    records = [dict(r) for r in result]

    if records:
        return records

    # Fallback: name search
    result = session.run(SEARCH_TERM_BY_NAME, term=term)
    return [dict(r) for r in result]


# ---------------------------------------------------------------------------
# 5. Get full context subgraph for a set of tables (for LLM prompt assembly)
# ---------------------------------------------------------------------------

GET_CONTEXT_SUBGRAPH = """
MATCH (t:Table)
WHERE t.fqn IN $table_fqns
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
OPTIONAL MATCH (c)-[fk:HAS_FOREIGN_KEY]->(ref_col:Column)<-[:HAS_COLUMN]-(ref_table:Table)
  WHERE ref_table.fqn IN $table_fqns OR ref_table IS NOT NULL
OPTIONAL MATCH (t)-[:HAS_INDEX]->(idx:Index)
OPTIONAL MATCH (t)-[:HAS_CONSTRAINT]->(con:Constraint WHERE con.type IN ['PRIMARY_KEY','UNIQUE'])
OPTIONAL MATCH (bt:BusinessTerm)-[m:MAPS_TO]->(t)
RETURN
    t                         AS table_node,
    collect(DISTINCT c)       AS columns,
    collect(DISTINCT {
        fk_col:       c.name,
        ref_table:    ref_table.name,
        ref_col:      ref_col.name,
        constraint:   fk.constraint_name
    })                        AS foreign_keys,
    collect(DISTINCT idx)     AS indexes,
    collect(DISTINCT con)     AS constraints,
    collect(DISTINCT {
        term:         bt.term,
        definition:   bt.definition,
        confidence:   m.confidence
    })                        AS business_terms
"""


def get_context_subgraph(session: Any, table_fqns: List[str]) -> List[Dict[str, Any]]:
    """
    Retrieve the full DDL context for the given set of tables.
    Returns a list of table context dicts, each containing columns, FKs, indexes, etc.
    """
    fqns_upper = [fqn.upper() for fqn in table_fqns]
    result = session.run(GET_CONTEXT_SUBGRAPH, table_fqns=fqns_upper)

    context = []
    for record in result:
        table_props = dict(record["table_node"])
        columns = [dict(c) for c in record["columns"] if c]
        columns.sort(key=lambda x: x.get("column_id", 0))

        fks = [
            fk for fk in record["foreign_keys"]
            if fk.get("ref_table")
        ]
        indexes = [dict(i) for i in record["indexes"] if i]
        constraints = [dict(c) for c in record["constraints"] if c]
        business_terms = [bt for bt in record["business_terms"] if bt.get("term")]

        context.append({
            "table": table_props,
            "columns": columns,
            "foreign_keys": fks,
            "indexes": indexes,
            "constraints": constraints,
            "business_terms": business_terms,
        })
    return context


def serialize_context_to_ddl(context: List[Dict[str, Any]]) -> str:
    """
    Convert a context subgraph (from get_context_subgraph) into a DDL-like
    text format suitable for injection into an LLM prompt.

    Format::

        -- TABLE: KYC.CUSTOMERS
        -- Description: Core customer entity for KYC compliance
        CREATE TABLE KYC.CUSTOMERS (
            CUSTOMER_ID     NUMBER(10)    NOT NULL,  -- Primary key
            FIRST_NAME      VARCHAR2(100) NOT NULL,
            ...
        );
        -- FK: CUSTOMERS.CUSTOMER_ID → (referenced by ACCOUNTS.CUSTOMER_ID)
        -- Sample data: CUSTOMER_ID=1001, FIRST_NAME='Alice', ...
        -- Business terms: "Customer Due Diligence" maps to this table (confidence: 1.0)
    """
    lines: List[str] = []

    for entry in context:
        t = entry["table"]
        tname = f'{t.get("schema", "")}.{t.get("name", "")}'
        lines.append(f"-- TABLE: {tname}")
        if t.get("comments"):
            lines.append(f"-- Description: {t['comments']}")
        if t.get("row_count"):
            lines.append(f"-- Approximate row count: {t['row_count']:,}")
        lines.append(f"CREATE TABLE {tname} (")

        col_lines = []
        for col in entry["columns"]:
            dtype = _format_data_type(col)
            null_str = "NOT NULL" if col.get("nullable") == "N" else "NULL"
            pk_flag = " -- PK" if col.get("is_pk") else ""
            fk_flag = " -- FK" if col.get("is_fk") else ""
            idx_flag = " [INDEXED]" if col.get("is_indexed") else ""
            comment = f" -- {col['comments']}" if col.get("comments") else ""
            col_line = (
                f"    {col['name']:<35} {dtype:<25} {null_str}"
                f"{pk_flag}{fk_flag}{idx_flag}{comment}"
            )
            col_lines.append(col_line)

        lines.append(",\n".join(col_lines))
        lines.append(");")

        # FK annotations
        for fk in entry["foreign_keys"]:
            lines.append(
                f"-- FK: {tname}.{fk['fk_col']} → {fk['ref_table']}.{fk['ref_col']}"
                f"  (constraint: {fk.get('constraint', '')})"
            )

        # Index annotations
        for idx in entry["indexes"]:
            uniq = "UNIQUE " if idx.get("uniqueness") == "UNIQUE" else ""
            lines.append(
                f"-- {uniq}INDEX {idx.get('name', '')} ON"
                f" {tname}({idx.get('columns_list', '')})"
            )

        # Business terms
        for bt in entry["business_terms"]:
            lines.append(
                f"-- Business term: \"{bt['term']}\" → {tname}"
                f"  (confidence: {bt.get('confidence', 1.0):.1f})"
            )
            if bt.get("definition"):
                lines.append(f"--   Definition: {bt['definition']}")

        lines.append("")  # blank line between tables

    return "\n".join(lines)


def _format_data_type(col: Dict[str, Any]) -> str:
    """Format an Oracle column data type string."""
    dtype = col.get("data_type", "VARCHAR2")
    if dtype in ("NUMBER", "FLOAT"):
        p = col.get("precision")
        s = col.get("scale")
        if p and s:
            return f"{dtype}({p},{s})"
        if p:
            return f"{dtype}({p})"
        return dtype
    if dtype in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"):
        length = col.get("data_length")
        return f"{dtype}({length})" if length else dtype
    if dtype == "RAW":
        length = col.get("data_length")
        return f"RAW({length})" if length else "RAW"
    return dtype


# ---------------------------------------------------------------------------
# 6. Schema search (tables + columns by name or business term)
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = """
CALL db.index.fulltext.queryNodes('table_fulltext', $query)
YIELD node AS t, score AS t_score
WHERE score > 0.5
RETURN
    'Table'   AS label,
    t.fqn     AS fqn,
    t.name    AS name,
    t.schema  AS schema,
    t.comments AS description,
    t_score   AS score
UNION
CALL db.index.fulltext.queryNodes('column_fulltext', $query)
YIELD node AS c, score AS c_score
WHERE c_score > 0.5
RETURN
    'Column'     AS label,
    c.fqn        AS fqn,
    c.name       AS name,
    c.table_name AS schema,
    c.comments   AS description,
    c_score      AS score
UNION
CALL db.index.fulltext.queryNodes('business_term_fulltext', $query)
YIELD node AS bt, score AS bt_score
WHERE bt_score > 0.5
RETURN
    'BusinessTerm' AS label,
    bt.term        AS fqn,
    bt.term        AS name,
    bt.domain      AS schema,
    bt.definition  AS description,
    bt_score       AS score
ORDER BY score DESC
LIMIT $limit
"""


def search_schema(session: Any, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Full-text search across Table, Column, and BusinessTerm nodes.
    Requires the fulltext indexes created during graph setup.
    """
    try:
        result = session.run(SEARCH_SCHEMA, query=query, limit=limit)
        return [dict(r) for r in result]
    except Exception:
        # Fallback to simple CONTAINS search if full-text index not available
        result = session.run(SEARCH_TERM_BY_NAME, term=query)
        return [dict(r) for r in result]


# ---------------------------------------------------------------------------
# 7. List all tables (for schema explorer)
# ---------------------------------------------------------------------------

LIST_ALL_TABLES = """
MATCH (t:Table)-[:BELONGS_TO]->(s:Schema)
WHERE ($schema IS NULL OR s.name = $schema)
RETURN
    t.fqn        AS fqn,
    t.name       AS name,
    t.schema     AS schema,
    t.row_count  AS row_count,
    t.table_type AS table_type,
    t.comments   AS comments,
    t.partitioned AS partitioned
ORDER BY t.schema, t.name
SKIP $skip
LIMIT $limit
"""


def list_all_tables(
    session: Any,
    schema: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Paginated list of all Table nodes, optionally filtered by schema."""
    result = session.run(
        LIST_ALL_TABLES,
        schema=schema.upper() if schema else None,
        skip=skip,
        limit=limit,
    )
    return [dict(r) for r in result]


# ---------------------------------------------------------------------------
# 8. Get index hints for a set of columns
# ---------------------------------------------------------------------------

GET_INDEX_HINTS = """
MATCH (c:Column)-[:INDEXED_BY]->(idx:Index)
WHERE c.fqn IN $column_fqns
RETURN
    c.fqn          AS column_fqn,
    c.name         AS column_name,
    idx.fqn        AS index_fqn,
    idx.name       AS index_name,
    idx.index_type AS index_type,
    idx.uniqueness AS uniqueness,
    idx.table_name AS table_name
ORDER BY idx.uniqueness DESC, idx.index_type
"""


def get_index_hints(session: Any, column_fqns: List[str]) -> List[Dict[str, Any]]:
    """Return available index hints for the specified columns."""
    result = session.run(GET_INDEX_HINTS, column_fqns=[f.upper() for f in column_fqns])
    return [dict(r) for r in result]


# ---------------------------------------------------------------------------
# 9. Get view lineage (tables a view depends on)
# ---------------------------------------------------------------------------

GET_VIEW_LINEAGE = """
MATCH (v:View {fqn: $view_fqn})-[d:DEPENDS_ON]->(t)
RETURN
    v.fqn             AS view_fqn,
    v.name            AS view_name,
    t.fqn             AS dependency_fqn,
    t.name            AS dependency_name,
    labels(t)         AS dependency_labels,
    d.dependency_type AS dependency_type
"""


def get_view_lineage(session: Any, view_fqn: str) -> List[Dict[str, Any]]:
    """Return all tables/views that a given view depends on."""
    result = session.run(GET_VIEW_LINEAGE, view_fqn=view_fqn.upper())
    return [dict(r) for r in result]


# ---------------------------------------------------------------------------
# 10. Get procedure calls (tables/views a procedure accesses)
# ---------------------------------------------------------------------------

GET_PROCEDURE_CALLS = """
MATCH (p:Procedure {fqn: $proc_fqn})-[c:CALLS]->(target)
RETURN
    p.fqn              AS procedure_fqn,
    p.name             AS procedure_name,
    target.fqn         AS target_fqn,
    target.name        AS target_name,
    labels(target)     AS target_labels,
    c.operation_type   AS operation_type
"""


def get_procedure_calls(session: Any, proc_fqn: str) -> List[Dict[str, Any]]:
    """Return all tables/views accessed by a given procedure."""
    result = session.run(GET_PROCEDURE_CALLS, proc_fqn=proc_fqn.upper())
    return [dict(r) for r in result]


# ---------------------------------------------------------------------------
# 11. Retrieve few-shot query patterns for a set of tables
# ---------------------------------------------------------------------------

GET_QUERY_PATTERNS = """
MATCH (qp:QueryPattern)
WHERE ANY(tag IN qp.tags WHERE tag IN $table_names)
RETURN
    qp.pattern_id          AS pattern_id,
    qp.description         AS description,
    qp.sql_template        AS sql_template,
    qp.frequency           AS frequency,
    qp.avg_execution_time_ms AS avg_execution_time_ms
ORDER BY qp.frequency DESC
LIMIT $limit
"""


def get_query_patterns(
    session: Any, table_names: List[str], limit: int = 5
) -> List[Dict[str, Any]]:
    """Return the most-used query patterns associated with the given tables."""
    result = session.run(
        GET_QUERY_PATTERNS,
        table_names=[n.upper() for n in table_names],
        limit=limit,
    )
    return [dict(r) for r in result]


# ---------------------------------------------------------------------------
# 12. Get similar columns (SIMILAR_TO traversal)
# ---------------------------------------------------------------------------

GET_SIMILAR_COLUMNS = """
MATCH (c:Column {fqn: $column_fqn})-[st:SIMILAR_TO]->(other:Column)
RETURN
    other.fqn          AS fqn,
    other.name         AS name,
    other.table_name   AS table_name,
    other.data_type    AS data_type,
    st.similarity_score AS score,
    st.match_type       AS match_type
ORDER BY st.similarity_score DESC
LIMIT $limit
"""


def get_similar_columns(
    session: Any, column_fqn: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """Find columns similar to the given column across all tables."""
    result = session.run(GET_SIMILAR_COLUMNS, column_fqn=column_fqn.upper(), limit=limit)
    return [dict(r) for r in result]
