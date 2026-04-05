"""
Knowledge Graph Traversal Queries
===================================
Query functions for retrieving schema context from the in-memory KnowledgeGraph.

All functions accept a KnowledgeGraph instance and return typed Python dicts.

Pattern catalogue
-----------------
  get_columns_for_table        – All columns for a given table (ordered)
  get_table_detail             – Full table context including constraints
  find_join_path               – Shortest FK-based join path between two tables
  resolve_business_term        – Map a natural-language term to schema nodes
  get_context_subgraph         – Full DDL context for a set of table names
  search_schema                – Text search over tables and columns
  list_all_tables              – Paginated table listing for schema explorer
  get_index_hints              – Indexes available for a column set
  get_view_lineage             – Tables a view depends on
  get_procedure_calls          – Tables/views a procedure accesses
  get_query_patterns           – Few-shot examples for a given table set
  get_similar_columns          – Columns similar to a given column
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import networkx as nx

from knowledge_graph.graph_store import KnowledgeGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Get all columns for a table (ordered by column_id)
# ---------------------------------------------------------------------------

def get_columns_for_table(graph: KnowledgeGraph, table_fqn: str) -> List[Dict[str, Any]]:
    """Return an ordered list of column dicts for *table_fqn*."""
    table_fqn = table_fqn.upper()
    col_edges = graph.get_out_edges("HAS_COLUMN", table_fqn)
    columns = []
    for edge in col_edges:
        col = graph.get_node("Column", edge["_to"])
        if col:
            columns.append(dict(col))
    columns.sort(key=lambda c: c.get("column_id", 0))
    return columns


# ---------------------------------------------------------------------------
# 2. Get full table detail (table + columns + constraints + FK edges)
# ---------------------------------------------------------------------------

def get_table_detail(graph: KnowledgeGraph, table_fqn: str) -> Optional[Dict[str, Any]]:
    """Return a rich dict describing the table including all columns, constraints, and FKs."""
    table_fqn = table_fqn.upper()
    table = graph.get_node("Table", table_fqn)
    if table is None:
        return None

    columns = get_columns_for_table(graph, table_fqn)

    constraint_edges = graph.get_out_edges("HAS_CONSTRAINT", table_fqn)
    constraints = []
    for edge in constraint_edges:
        con = graph.get_node("Constraint", edge["_to"])
        if con:
            constraints.append(dict(con))

    foreign_keys = []
    for col in columns:
        col_fqn = col["fqn"]
        for fk_edge in graph.get_out_edges("HAS_FOREIGN_KEY", col_fqn):
            ref_col_fqn = fk_edge["_to"]
            ref_col = graph.get_node("Column", ref_col_fqn)
            if ref_col:
                ref_table = graph.get_node("Table", ref_col.get("table_fqn", ""))
                foreign_keys.append({
                    "fk_col": col["name"],
                    "ref_table": ref_table["name"] if ref_table else ref_col.get("table_name", ""),
                    "ref_col": ref_col["name"],
                    "constraint_name": fk_edge.get("constraint_name", ""),
                })

    return {
        "table": dict(table),
        "columns": columns,
        "constraints": constraints,
        "foreign_keys": foreign_keys,
    }


# ---------------------------------------------------------------------------
# 3. Find shortest join path between two tables (via FK edges)
# ---------------------------------------------------------------------------

def find_join_path(
    graph: KnowledgeGraph,
    table1_fqn: str,
    table2_fqn: str,
    max_hops: int = 4,
) -> Optional[Dict[str, Any]]:
    """
    Return the optimal join path between two tables.
    Checks pre-computed JOIN_PATH edges first, then falls back to live FK traversal.
    """
    t1 = table1_fqn.upper()
    t2 = table2_fqn.upper()

    # Try pre-computed path (fast)
    for edge in graph.get_out_edges("JOIN_PATH", t1):
        if edge["_to"] == t2:
            return {
                "join_columns": edge.get("join_columns", []),
                "join_type": edge.get("join_type", "INNER"),
                "cardinality": edge.get("cardinality", "N:1"),
                "weight": edge.get("weight", 1),
                "source": "precomputed",
            }

    # Fallback: build FK graph on demand and use NetworkX shortest path
    G = nx.Graph()
    for fk_edge in graph.get_all_edges("HAS_FOREIGN_KEY"):
        src_col_fqn = fk_edge["_from"]
        tgt_col_fqn = fk_edge["_to"]

        src_col = graph.get_node("Column", src_col_fqn)
        tgt_col = graph.get_node("Column", tgt_col_fqn)
        if not src_col or not tgt_col:
            continue

        src_table = src_col.get("table_fqn", "")
        tgt_table = tgt_col.get("table_fqn", "")
        if src_table and tgt_table and src_table != tgt_table:
            G.add_edge(
                src_table, tgt_table,
                src_col=src_col_fqn,
                tgt_col=tgt_col_fqn,
                constraint=fk_edge.get("constraint_name", ""),
            )

    try:
        path_nodes = nx.shortest_path(G, t1, t2)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None

    if len(path_nodes) - 1 > max_hops:
        return None

    path_edges = []
    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        edge_data = G.get_edge_data(u, v) or {}
        path_edges.append({
            "type": "HAS_FOREIGN_KEY",
            "src": u,
            "tgt": v,
            "constraint": edge_data.get("constraint", ""),
        })

    return {
        "path_nodes": path_nodes,
        "path_edges": path_edges,
        "hops": len(path_nodes) - 1,
        "source": "traversal",
    }


# ---------------------------------------------------------------------------
# 4. Resolve a business term to schema nodes (table or column)
# ---------------------------------------------------------------------------

def resolve_business_term(graph: KnowledgeGraph, term: str) -> List[Dict[str, Any]]:
    """
    Map a natural-language term to schema elements.
    Searches BusinessTerm glossary first, then falls back to name-based search.
    """
    term_lower = term.lower()
    results: List[Dict[str, Any]] = []

    # Search BusinessTerm nodes
    for bt in graph.get_all_nodes("BusinessTerm"):
        bt_term = bt.get("term", "")
        aliases = bt.get("aliases", [])
        if term_lower in bt_term.lower() or any(term_lower in a.lower() for a in aliases):
            for mapping in graph.get_out_edges("MAPS_TO", bt_term):
                target_fqn = mapping["_to"]
                # Determine target label
                target_labels = []
                for label in ("Table", "Column", "View"):
                    if graph.get_node(label, target_fqn):
                        target_labels = [label]
                        break
                target = (
                    graph.get_node("Table", target_fqn)
                    or graph.get_node("Column", target_fqn)
                    or {}
                )
                results.append({
                    "term": bt_term,
                    "definition": bt.get("definition", ""),
                    "sensitivity_level": bt.get("sensitivity_level", "INTERNAL"),
                    "target_labels": target_labels,
                    "target_fqn": target_fqn,
                    "target_name": target.get("name", ""),
                    "confidence": mapping.get("confidence", 1.0),
                    "mapping_type": mapping.get("mapping_type", "inferred"),
                })

    if results:
        results.sort(key=lambda r: r["confidence"], reverse=True)
        return results

    # Fallback: name-based search across tables and columns
    return _search_by_name(graph, term, limit=20)


# ---------------------------------------------------------------------------
# 5. Get full context subgraph for a set of tables (for LLM prompt assembly)
# ---------------------------------------------------------------------------

def get_context_subgraph(graph: KnowledgeGraph, table_fqns: List[str]) -> List[Dict[str, Any]]:
    """
    Retrieve the full DDL context for the given set of tables.
    Returns a list of table context dicts, each containing columns, FKs, indexes, etc.
    """
    fqns_upper = {fqn.upper() for fqn in table_fqns}
    context = []

    for table_fqn in fqns_upper:
        table = graph.get_node("Table", table_fqn)
        if table is None:
            continue

        columns = get_columns_for_table(graph, table_fqn)

        # FK edges originating from columns of this table
        foreign_keys = []
        for col in columns:
            for fk_edge in graph.get_out_edges("HAS_FOREIGN_KEY", col["fqn"]):
                ref_col = graph.get_node("Column", fk_edge["_to"])
                if ref_col:
                    ref_table_fqn = ref_col.get("table_fqn", "")
                    ref_table = graph.get_node("Table", ref_table_fqn) or {}
                    foreign_keys.append({
                        "fk_col": col["name"],
                        "ref_table": ref_table.get("name", ref_col.get("table_name", "")),
                        "ref_col": ref_col["name"],
                        "constraint": fk_edge.get("constraint_name", ""),
                    })

        # Index nodes
        indexes = []
        for idx_edge in graph.get_out_edges("HAS_INDEX", table_fqn):
            idx = graph.get_node("Index", idx_edge["_to"])
            if idx:
                indexes.append(dict(idx))

        # Constraint nodes (PK + UNIQUE only for context)
        constraints = []
        for con_edge in graph.get_out_edges("HAS_CONSTRAINT", table_fqn):
            con = graph.get_node("Constraint", con_edge["_to"])
            if con and con.get("type") in ("PRIMARY_KEY", "UNIQUE"):
                constraints.append(dict(con))

        # Business terms mapped to this table
        business_terms = []
        for bt in graph.get_all_nodes("BusinessTerm"):
            bt_term = bt.get("term", "")
            for mapping in graph.get_out_edges("MAPS_TO", bt_term):
                if mapping["_to"] == table_fqn:
                    business_terms.append({
                        "term": bt_term,
                        "definition": bt.get("definition", ""),
                        "confidence": mapping.get("confidence", 1.0),
                    })

        context.append({
            "table": dict(table),
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
            "constraints": constraints,
            "business_terms": business_terms,
        })

    return context


def serialize_context_to_ddl(
    context: List[Dict[str, Any]],
    get_values=None,
) -> str:
    """
    Convert a context subgraph (from get_context_subgraph) into a DDL-like
    text format suitable for injection into an LLM prompt.

    Parameters
    ----------
    context : list
        Output of :func:`get_context_subgraph`.
    get_values : callable, optional
        ``(schema, table, column) -> List[str]`` — when provided, enum-like
        columns are annotated with their actual distinct DB values, e.g.
        ``-- Values(3): 'ACTIVE', 'INACTIVE', 'PENDING'``.
        Typically created with :func:`~knowledge_graph.column_value_cache.make_value_getter`.
    """
    from knowledge_graph.column_value_cache import is_likely_enum_column  # local import avoids cycles

    lines: List[str] = []

    for entry in context:
        t = entry["table"]
        t_schema = t.get("schema", "")
        t_name   = t.get("name", "")
        tname = f"{t_schema}.{t_name}"
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
            # Annotate enum-like columns with actual distinct values from the DB
            if get_values and is_likely_enum_column(
                col["name"],
                col.get("data_type", ""),
                col.get("data_length") or 0,
            ):
                vals = get_values(t_schema, t_name, col["name"])
                if vals:
                    vals_str = ", ".join(f"'{v}'" for v in vals)
                    col_line += f"  -- Values({len(vals)}): {vals_str}"
            col_lines.append(col_line)

        lines.append(",\n".join(col_lines))
        lines.append(");")

        for fk in entry["foreign_keys"]:
            lines.append(
                f"-- FK: {tname}.{fk['fk_col']} → {fk['ref_table']}.{fk['ref_col']}"
                f"  (constraint: {fk.get('constraint', '')})"
            )

        for idx in entry["indexes"]:
            uniq = "UNIQUE " if idx.get("uniqueness") == "UNIQUE" else ""
            lines.append(
                f"-- {uniq}INDEX {idx.get('name', '')} ON"
                f" {tname}({idx.get('columns_list', '')})"
            )

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

def search_schema(graph: KnowledgeGraph, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Text search across Table, Column, and BusinessTerm nodes using
    substring matching on name and comments fields.
    """
    return _search_by_name(graph, query, limit=limit)


def _search_by_name(graph: KnowledgeGraph, term: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Substring search over Table and Column names and comments."""
    term_lower = term.lower()
    results: List[Dict[str, Any]] = []

    for table in graph.get_all_nodes("Table"):
        name_match = term_lower in table.get("name", "").lower()
        comment_match = term_lower in (table.get("comments") or "").lower()
        if name_match or comment_match:
            results.append({
                "label": "Table",
                "fqn": table.get("fqn", ""),
                "name": table.get("name", ""),
                "schema": table.get("schema", ""),
                "description": table.get("comments", ""),
                "score": 0.7 if name_match else 0.5,
                "match_type": "table_name" if name_match else "table_comment",
            })

    for col in graph.get_all_nodes("Column"):
        name_match = term_lower in col.get("name", "").lower()
        comment_match = term_lower in (col.get("comments") or "").lower()
        if name_match or comment_match:
            results.append({
                "label": "Column",
                "fqn": col.get("fqn", ""),
                "name": col.get("name", ""),
                "schema": col.get("table_name", ""),
                "description": col.get("comments", ""),
                "score": 0.6 if name_match else 0.4,
                "match_type": "column_name" if name_match else "column_comment",
            })

    for bt in graph.get_all_nodes("BusinessTerm"):
        term_val = bt.get("term", "")
        if term_lower in term_val.lower() or term_lower in (bt.get("definition") or "").lower():
            results.append({
                "label": "BusinessTerm",
                "fqn": term_val,
                "name": term_val,
                "schema": bt.get("domain", ""),
                "description": bt.get("definition", ""),
                "score": 0.8,
                "match_type": "business_term",
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


# ---------------------------------------------------------------------------
# 7. List all tables (for schema explorer)
# ---------------------------------------------------------------------------

def list_all_tables(
    graph: KnowledgeGraph,
    schema: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Paginated list of all Table nodes, optionally filtered by schema."""
    schema_upper = schema.upper() if schema else None
    tables = [
        {
            "fqn": t.get("fqn", ""),
            "name": t.get("name", ""),
            "schema": t.get("schema", ""),
            "row_count": t.get("row_count"),
            "table_type": t.get("table_type", "TABLE"),
            "comments": t.get("comments"),
            "partitioned": t.get("partitioned", "NO"),
        }
        for t in graph.get_all_nodes("Table")
        if schema_upper is None or t.get("schema", "").upper() == schema_upper
    ]
    tables.sort(key=lambda t: (t["schema"], t["name"]))
    return tables[skip: skip + limit]


# ---------------------------------------------------------------------------
# 8. Get index hints for a set of columns
# ---------------------------------------------------------------------------

def get_index_hints(graph: KnowledgeGraph, column_fqns: List[str]) -> List[Dict[str, Any]]:
    """Return available index hints for the specified columns."""
    fqns_upper = [f.upper() for f in column_fqns]
    results = []
    for col_fqn in fqns_upper:
        col = graph.get_node("Column", col_fqn)
        if not col:
            continue
        for edge in graph.get_out_edges("INDEXED_BY", col_fqn):
            idx_fqn = edge["_to"]
            idx = graph.get_node("Index", idx_fqn)
            if idx:
                results.append({
                    "column_fqn": col_fqn,
                    "column_name": col.get("name", ""),
                    "index_fqn": idx_fqn,
                    "index_name": idx.get("name", ""),
                    "index_type": idx.get("index_type", ""),
                    "uniqueness": idx.get("uniqueness", ""),
                    "table_name": idx.get("table_name", ""),
                })

    results.sort(key=lambda r: (r["uniqueness"] != "UNIQUE", r.get("index_type", "")))
    return results


# ---------------------------------------------------------------------------
# 9. Get view lineage (tables a view depends on)
# ---------------------------------------------------------------------------

def get_view_lineage(graph: KnowledgeGraph, view_fqn: str) -> List[Dict[str, Any]]:
    """Return all tables/views that a given view depends on."""
    view_fqn = view_fqn.upper()
    view = graph.get_node("View", view_fqn)
    if view is None:
        return []

    results = []
    for edge in graph.get_out_edges("DEPENDS_ON", view_fqn):
        dep_fqn = edge["_to"]
        dep_labels = []
        dep_node = None
        for label in ("Table", "View"):
            dep_node = graph.get_node(label, dep_fqn)
            if dep_node:
                dep_labels = [label]
                break
        results.append({
            "view_fqn": view_fqn,
            "view_name": view.get("name", ""),
            "dependency_fqn": dep_fqn,
            "dependency_name": dep_node.get("name", "") if dep_node else dep_fqn,
            "dependency_labels": dep_labels,
            "dependency_type": edge.get("dependency_type", "SELECT"),
        })
    return results


# ---------------------------------------------------------------------------
# 10. Get procedure calls (tables/views a procedure accesses)
# ---------------------------------------------------------------------------

def get_procedure_calls(graph: KnowledgeGraph, proc_fqn: str) -> List[Dict[str, Any]]:
    """Return all tables/views accessed by a given procedure."""
    proc_fqn = proc_fqn.upper()
    proc = graph.get_node("Procedure", proc_fqn)
    if proc is None:
        return []

    results = []
    for edge in graph.get_out_edges("CALLS", proc_fqn):
        target_fqn = edge["_to"]
        target_labels = []
        target_node = None
        for label in ("Table", "View", "Procedure"):
            target_node = graph.get_node(label, target_fqn)
            if target_node:
                target_labels = [label]
                break
        results.append({
            "procedure_fqn": proc_fqn,
            "procedure_name": proc.get("name", ""),
            "target_fqn": target_fqn,
            "target_name": target_node.get("name", "") if target_node else target_fqn,
            "target_labels": target_labels,
            "operation_type": edge.get("operation_type", "SELECT"),
        })
    return results


# ---------------------------------------------------------------------------
# 11. Retrieve few-shot query patterns for a set of tables
# ---------------------------------------------------------------------------

def get_query_patterns(
    graph: KnowledgeGraph, table_names: List[str], limit: int = 5
) -> List[Dict[str, Any]]:
    """Return the most-used query patterns associated with the given tables."""
    names_upper = {n.upper() for n in table_names}
    results = []
    for qp in graph.get_all_nodes("QueryPattern"):
        tags = {t.upper() for t in qp.get("tags", [])}
        if tags & names_upper:
            results.append({
                "pattern_id": qp.get("pattern_id", ""),
                "description": qp.get("description", ""),
                "sql_template": qp.get("sql_template", ""),
                "frequency": qp.get("frequency", 1),
                "avg_execution_time_ms": qp.get("avg_execution_time_ms", 0.0),
            })
    results.sort(key=lambda r: r["frequency"], reverse=True)
    return results[:limit]


# ---------------------------------------------------------------------------
# 12. Get similar columns (SIMILAR_TO traversal)
# ---------------------------------------------------------------------------

def get_similar_columns(
    graph: KnowledgeGraph, column_fqn: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """Find columns similar to the given column across all tables."""
    col_fqn = column_fqn.upper()
    results = []
    for edge in graph.get_out_edges("SIMILAR_TO", col_fqn):
        other_fqn = edge["_to"]
        other = graph.get_node("Column", other_fqn)
        if other:
            results.append({
                "fqn": other_fqn,
                "name": other.get("name", ""),
                "table_name": other.get("table_name", ""),
                "data_type": other.get("data_type", ""),
                "score": edge.get("similarity_score", 0.0),
                "match_type": edge.get("match_type", ""),
            })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
