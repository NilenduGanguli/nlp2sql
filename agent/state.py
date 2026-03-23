"""
LangGraph Agent State
======================
Defines the typed state dict shared across all pipeline nodes.
Each node receives the full state and returns an updated copy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict


class Intent(str, Enum):
    """Possible intent classifications for a user query."""

    DATA_QUERY = "DATA_QUERY"
    SCHEMA_EXPLORE = "SCHEMA_EXPLORE"
    QUERY_EXPLAIN = "QUERY_EXPLAIN"
    QUERY_REFINE = "QUERY_REFINE"


class AgentState(TypedDict):
    """
    Full mutable state passed between LangGraph nodes.

    Fields are populated progressively as the pipeline executes.
    Nodes return a partial dict; LangGraph merges the returned dict
    into the running state via its reducer logic.
    """

    # ------------------------------------------------------------------ Input
    user_input: str
    """The raw natural-language question from the user."""

    conversation_history: List[Dict[str, str]]
    """Previous turns: [{"role": "user"|"assistant", "content": "..."}]"""

    # -------------------------------------------------------- Pipeline stages
    intent: str
    """Classified intent: DATA_QUERY | SCHEMA_EXPLORE | QUERY_EXPLAIN | QUERY_REFINE"""

    entities: Dict[str, Any]
    """
    Extracted entities:
      tables        – List[str]: likely Oracle table names
      columns       – List[str]: specific column names
      conditions    – List[str]: filter predicates
      time_range    – Optional[str]: temporal reference
      aggregations  – List[str]: COUNT, SUM, AVG, etc.
      sort_by       – Optional[str]: ORDER BY directive
      limit         – Optional[int]: result limit
    """

    schema_context: str
    """DDL-formatted schema description injected into the LLM prompt."""

    candidate_sqls: List[str]
    """Multiple SQL candidates for self-consistency checks (future use)."""

    generated_sql: str
    """Primary SQL statement produced by the sql_generator node."""

    sql_explanation: str
    """Human-readable explanation of the generated SQL."""

    validation_passed: bool
    """True when the sql_validator node accepted the SQL."""

    validation_errors: List[str]
    """Error messages produced by the sql_validator node."""

    optimized_sql: str
    """Final SQL after rule-based optimizations (e.g. row limit injection)."""

    execution_result: Dict[str, Any]
    """
    Execution output:
      columns          – List[str]
      rows             – List[List[Any]]
      total_rows       – int
      execution_time_ms – int
      source           – "oracle" | "mock"
    """

    formatted_response: str
    """JSON-serialized response dict for the chat UI."""

    # ------------------------------------------------------------------ Meta
    step: str
    """Name of the last completed pipeline step."""

    error: Optional[str]
    """First unhandled error message encountered during execution."""

    retry_count: int
    """Number of times the sql_generator has been called (for retry logic)."""
