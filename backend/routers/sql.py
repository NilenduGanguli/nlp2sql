"""Direct SQL execution and formatting endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_config
from backend.models import SQLExecuteRequest, SQLExecuteResponse, SQLFormatRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sql", tags=["sql"])


@router.post("/execute", response_model=SQLExecuteResponse)
async def execute_sql(req: SQLExecuteRequest, config=Depends(get_config)):
    """Execute raw SQL against Oracle and return results."""
    import anyio
    from agent.nodes.query_executor import _oracle_execute

    if not req.sql.strip():
        raise HTTPException(status_code=400, detail="SQL query is empty")

    try:
        result = await anyio.to_thread.run_sync(lambda: _oracle_execute(req.sql, config))
    except Exception as exc:
        logger.warning("SQL execution error: %s", exc)
        return SQLExecuteResponse(
            columns=[],
            rows=[],
            total_rows=0,
            execution_time_ms=0,
            error=str(exc),
        )

    if "error" in result:
        return SQLExecuteResponse(
            columns=[],
            rows=[],
            total_rows=0,
            execution_time_ms=0,
            error=result["error"],
        )

    return SQLExecuteResponse(
        columns=result.get("columns", []),
        rows=result.get("rows", []),
        total_rows=result.get("total_rows", 0),
        execution_time_ms=result.get("execution_time_ms", 0),
    )


@router.post("/format")
async def format_sql(req: SQLFormatRequest):
    """Format SQL using sqlglot Oracle dialect."""
    try:
        import sqlglot
        formatted = sqlglot.format(req.sql, dialect="oracle")
        return {"formatted_sql": formatted}
    except Exception as exc:
        # Return original if formatting fails
        logger.debug("sqlglot format failed: %s", exc)
        return {"formatted_sql": req.sql}
