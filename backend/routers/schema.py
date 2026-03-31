"""Schema metadata endpoints — all read from in-memory KnowledgeGraph."""
from __future__ import annotations

import logging
import math
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.deps import get_config, get_graph
from backend.models import (
    ColumnDetail, ForeignKeyRef, SchemaStats, SearchResponse,
    SearchResult, TableDetail, TableSummary, TablesPage,
)
from knowledge_graph.traversal import (
    get_columns_for_table, get_table_detail, list_all_tables, search_schema,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("/stats", response_model=SchemaStats)
async def schema_stats(
    graph=Depends(get_graph),
    request_obj=None,
):
    """Overall schema statistics."""
    from fastapi import Request  # avoid circular at module level
    table_count = graph.count_nodes("Table")
    col_count = graph.count_nodes("Column")
    fk_count = graph.count_edges("HAS_FOREIGN_KEY")
    jp_count = graph.count_edges("JOIN_PATH")

    schemas = sorted({
        t.get("schema", "")
        for t in graph.get_all_nodes("Table")
        if t.get("schema")
    })

    return SchemaStats(
        table_count=table_count,
        column_count=col_count,
        fk_count=fk_count,
        join_path_count=jp_count,
        schemas=schemas,
        llm_enhanced=False,  # populated by health endpoint
    )


@router.get("/tables", response_model=TablesPage)
async def list_tables(
    graph=Depends(get_graph),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    schema: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Filter by name/comment substring"),
):
    """
    Paginated list of all tables. Fast: reads from in-memory graph.
    Use q= for client-side filtering by name or comment.
    Schema data is cached by the frontend (staleTime: Infinity).
    """
    # Load all (no limit) then filter+paginate in Python
    all_tables = list_all_tables(graph, schema=schema, skip=0, limit=100_000)

    if q:
        q_lower = q.lower()
        all_tables = [
            t for t in all_tables
            if q_lower in t["name"].lower() or q_lower in (t.get("comments") or "").lower()
        ]

    total = len(all_tables)
    pages = max(1, math.ceil(total / page_size))
    skip = (page - 1) * page_size
    page_items = all_tables[skip: skip + page_size]

    # Enrich with graph props (importance, column count)
    items: List[TableSummary] = []
    for t in page_items:
        node = graph.get_node("Table", t["fqn"])
        col_edges = graph.get_out_edges("HAS_COLUMN", t["fqn"])
        items.append(TableSummary(
            fqn=t["fqn"],
            name=t["name"],
            schema_name=t["schema"],
            row_count=t.get("row_count"),
            table_type=t.get("table_type", "TABLE"),
            comments=t.get("comments"),
            partitioned=t.get("partitioned", "NO"),
            importance_tier=node.get("importance_tier") if node else None,
            importance_rank=node.get("importance_rank") if node else None,
            llm_description=node.get("llm_description") if node else None,
            column_count=len(col_edges),
        ))

    return TablesPage(items=items, total=total, page=page, pages=pages, page_size=page_size)


@router.get("/tables/{fqn:path}", response_model=TableDetail)
async def get_table(fqn: str, graph=Depends(get_graph)):
    """Full table detail: columns, FKs, constraints."""
    fqn_upper = fqn.upper()
    detail = get_table_detail(graph, fqn_upper)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Table not found: {fqn}")

    t = detail["table"]
    node = graph.get_node("Table", fqn_upper)

    columns = [
        ColumnDetail(
            name=c["name"],
            data_type=c.get("data_type", ""),
            nullable=c.get("nullable"),
            comments=c.get("comments"),
            is_pk=bool(c.get("is_pk")),
            is_fk=bool(c.get("is_fk")),
            is_indexed=bool(c.get("is_indexed")),
            column_id=c.get("column_id"),
            data_length=c.get("data_length"),
            precision=c.get("precision"),
            scale=c.get("scale"),
        )
        for c in detail["columns"]
    ]

    foreign_keys = [
        ForeignKeyRef(
            fk_col=fk["fk_col"],
            ref_table=fk["ref_table"],
            ref_col=fk["ref_col"],
            constraint_name=fk.get("constraint_name", ""),
        )
        for fk in detail["foreign_keys"]
    ]

    return TableDetail(
        fqn=t.get("fqn", fqn_upper),
        name=t.get("name", ""),
        schema_name=t.get("schema", ""),
        row_count=t.get("row_count"),
        table_type=t.get("table_type", "TABLE"),
        comments=t.get("comments"),
        importance_tier=node.get("importance_tier") if node else None,
        importance_rank=node.get("importance_rank") if node else None,
        llm_description=node.get("llm_description") if node else None,
        columns=columns,
        foreign_keys=foreign_keys,
        constraints=detail.get("constraints", []),
    )


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    graph=Depends(get_graph),
):
    """Full-text search across table names, column names, and comments."""
    raw = search_schema(graph, q, limit=30)
    results = [
        SearchResult(
            label=r.get("label", "Table"),
            fqn=r.get("fqn", ""),
            name=r.get("name", ""),
            schema_name=r.get("schema", ""),
            description=r.get("description") or r.get("comments"),
            match_score=r.get("match_score", 1.0),
        )
        for r in raw
    ]
    return SearchResponse(query=q, results=results)
