"""Knowledge graph visualization and join-path endpoints."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from backend.deps import get_graph
from backend.models import (
    ForeignKeyEdge, GraphEdge, GraphNode, GraphVisualization, JoinColumnDetail, JoinPathResult,
)
from knowledge_graph.traversal import find_join_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/visualization", response_model=GraphVisualization)
async def get_visualization(
    graph=Depends(get_graph),
    limit: int = Query(default=200, ge=10, le=1000, description="Max tables to include"),
):
    """
    Return a graph visualization payload (nodes + edges).
    For large schemas, samples the most important tables (by importance_rank, then FK degree).
    The full 1000-table graph is never sent to the browser — use limit to control size.
    """
    all_tables = list(graph.get_all_nodes("Table"))
    total_tables = len(all_tables)

    # Sort: ranked tables first (by importance_rank asc), then unranked by degree desc
    def _sort_key(t):
        rank = t.get("importance_rank")
        degree = len(graph.get_out_edges("JOIN_PATH", t.get("fqn", "")))
        return (0 if rank else 1, rank or 9999, -degree)

    all_tables.sort(key=_sort_key)
    selected = all_tables[:limit]
    selected_fqns = {t["fqn"] for t in selected}

    nodes: List[GraphNode] = []
    for t in selected:
        nodes.append(GraphNode(
            id=t["fqn"],
            label=t.get("name", ""),
            group=t.get("importance_tier", "unknown") or "unknown",
            name=t.get("name", ""),
            schema_name=t.get("schema", ""),
            importance_rank=t.get("importance_rank"),
            row_count=t.get("row_count"),
            comments=t.get("llm_description") or t.get("comments"),
        ))

    # Build FK lookup: (src_col_fqn, tgt_col_fqn) → FK edge props
    # so we can attach constraint names / on-delete actions to each join column pair
    fk_lookup: dict = {}
    for fk in graph.get_all_edges("HAS_FOREIGN_KEY"):
        key = (fk.get("_from", ""), fk.get("_to", ""))
        fk_lookup[key] = fk

    # Only include edges between selected nodes
    edges: List[GraphEdge] = []
    seen_pairs: set = set()
    for jp in graph.get_all_edges("JOIN_PATH"):
        src = jp.get("_from", "")
        tgt = jp.get("_to", "")
        if src in selected_fqns and tgt in selected_fqns:
            pair = tuple(sorted([src, tgt]))
            if pair not in seen_pairs:
                seen_pairs.add(pair)

                # Resolve join column details
                raw_cols = jp.get("join_columns", [])
                join_col_details: List[JoinColumnDetail] = []
                for jc in raw_cols:
                    if not isinstance(jc, dict):
                        continue
                    src_fqn = jc.get("src") or jc.get("from_col") or jc.get("col", "")
                    tgt_fqn = jc.get("tgt") or jc.get("to_col") or jc.get("ref_col", "")
                    if not src_fqn or not tgt_fqn:
                        continue

                    src_node = graph.get_node("Column", src_fqn) or {}
                    tgt_node = graph.get_node("Column", tgt_fqn) or {}
                    fk_props = fk_lookup.get((src_fqn, tgt_fqn)) or {}

                    join_col_details.append(JoinColumnDetail(
                        from_col=src_node.get("name") or src_fqn.split(".")[-1],
                        to_col=tgt_node.get("name") or tgt_fqn.split(".")[-1],
                        from_col_fqn=src_fqn,
                        to_col_fqn=tgt_fqn,
                        from_col_type=src_node.get("data_type"),
                        to_col_type=tgt_node.get("data_type"),
                        from_col_comments=src_node.get("comments") or None,
                        to_col_comments=tgt_node.get("comments") or None,
                        constraint_name=fk_props.get("constraint_name", ""),
                        on_delete_action=fk_props.get("on_delete_action", ""),
                    ))

                edges.append(GraphEdge(
                    id=f"{src}--{tgt}",
                    from_id=src,
                    to_id=tgt,
                    rel_type="JOIN_PATH",
                    weight=jp.get("weight", 1.0),
                    source=jp.get("source", "precomputed"),
                    join_columns=join_col_details,
                    join_type=jp.get("join_type"),
                    cardinality=jp.get("cardinality"),
                ))

    return GraphVisualization(
        nodes=nodes,
        edges=edges,
        total_tables=total_tables,
        shown_tables=len(selected),
    )


@router.get("/join-path", response_model=JoinPathResult)
async def get_join_path(
    from_table: str = Query(..., alias="from"),
    to_table: str = Query(..., alias="to"),
    graph=Depends(get_graph),
):
    """Find the shortest join path between two tables."""
    result = find_join_path(graph, from_table, to_table)

    if result is None:
        return JoinPathResult(found=False, from_table=from_table, to_table=to_table)

    # Build ON clause snippet from join columns if available
    join_columns = result.get("join_columns", [])
    sql_snippet = None
    if join_columns:
        on_parts = []
        for jc in join_columns:
            if isinstance(jc, dict):
                a = jc.get("src") or jc.get("from_col") or jc.get("col", "")
                b = jc.get("tgt") or jc.get("to_col") or jc.get("ref_col", "")
                if a and b:
                    a_table = from_table.split(".")[-1]
                    b_table = to_table.split(".")[-1]
                    a_col = a.split(".")[-1]
                    b_col = b.split(".")[-1]
                    on_parts.append(f"{a_table}.{a_col} = {b_table}.{b_col}")
        if on_parts:
            sql_snippet = "ON " + " AND ".join(on_parts)

    # Normalise hops: precomputed paths store len(join_columns), traversal stores hops
    hops = result.get("hops", len(join_columns))

    return JoinPathResult(
        found=True,
        from_table=from_table,
        to_table=to_table,
        join_columns=join_columns,
        join_type=result.get("join_type"),
        hops=hops,
        source=result.get("source", ""),
        sql_snippet=sql_snippet,
    )


@router.get("/foreign-keys", response_model=List[ForeignKeyEdge])
async def list_foreign_keys(graph=Depends(get_graph)):
    """All FK constraints as source→target table pairs."""
    fk_edges: List[ForeignKeyEdge] = []
    for fk in graph.get_all_edges("HAS_FOREIGN_KEY"):
        src_col_fqn = fk.get("_from", "")
        tgt_col_fqn = fk.get("_to", "")
        src_col = graph.get_node("Column", src_col_fqn)
        tgt_col = graph.get_node("Column", tgt_col_fqn)
        if src_col and tgt_col:
            fk_edges.append(ForeignKeyEdge(
                from_table=src_col.get("table_fqn", ""),
                to_table=tgt_col.get("table_fqn", ""),
                from_col=src_col.get("name", ""),
                to_col=tgt_col.get("name", ""),
                constraint_name=fk.get("constraint_name", ""),
            ))
    return fk_edges
