"""
Entity Extractor Node
======================
Extracts structured business entities from the user's natural-language query
using an LLM with a structured JSON output prompt.

The schema summary injected into the system prompt is built dynamically from
the live KnowledgeGraph so that it reflects the actual database — not a
hardcoded KYC fixture.  Tables are ranked by FK connectivity so the most
"important" (most joined-to) tables appear first in the prompt.

Extracted entities:
  tables       – likely Oracle table names mentioned (e.g. "customers", "transactions")
  columns      – specific column names mentioned
  conditions   – filter predicates (e.g. "risk_rating = 'HIGH'", "amount > 10000")
  time_range   – temporal reference (e.g. "last quarter", "2024", "last month")
  aggregations – aggregation functions needed (COUNT, SUM, AVG, MAX, MIN)
  sort_by      – ordering requirements (e.g. "by amount descending")
  limit        – result row limit if specified
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.state import AgentState

logger = logging.getLogger(__name__)

# Maximum tables to include in the entity-extraction prompt.
# For very large schemas we pick the most FK-connected ones.
_MAX_TABLES_IN_PROMPT = 30


# ---------------------------------------------------------------------------
# Dynamic schema summary builder
# ---------------------------------------------------------------------------

def _build_schema_summary(graph) -> Tuple[str, List[str]]:
    """
    Build a concise schema summary for the entity-extraction prompt.

    Returns
    -------
    (table_list_text, all_table_names)
        table_list_text  – formatted for inclusion in the LLM system prompt
        all_table_names  – flat list of every table NAME in the graph (for fallback)
    """
    try:
        from knowledge_graph.traversal import get_columns_for_table
    except Exception:
        return "(schema unavailable)", []

    all_tables = graph.get_all_nodes("Table")
    if not all_tables:
        return "(no tables in graph)", []

    # Rank tables: LLM importance_rank takes priority (1 = most important),
    # then JOIN_PATH degree, then row_count as tiebreaker.
    # With reverse=True, higher tuple values sort first.
    # For LLM-ranked tables: bucket 1 with -rank (rank=1 → -1, largest in bucket)
    # For un-ranked tables: bucket 0, sorted structurally.
    def _rank_key(t: Dict[str, Any]) -> Tuple[int, int, int]:
        fqn = t.get("fqn", "")
        jp_count = len(graph.get_out_edges("JOIN_PATH", fqn))
        row_count = t.get("row_count") or 0
        llm_rank = t.get("importance_rank")
        if llm_rank is not None:
            return (1, -int(llm_rank), jp_count)
        return (0, jp_count, row_count)

    ranked = sorted(all_tables, key=_rank_key, reverse=True)
    selected = ranked[:_MAX_TABLES_IN_PROMPT]

    lines: List[str] = []
    for t in selected:
        fqn  = t.get("fqn", "")
        name = t.get("name", "")
        # Prefer Oracle comments; fall back to LLM-generated description
        comment = (t.get("comments") or t.get("llm_description") or "").strip()
        tier = t.get("importance_tier", "")
        tier_tag = f" [{tier}]" if tier and tier != "utility" else ""

        # Collect key column names: PKs > FKs > indexed > first N regular columns
        try:
            cols = get_columns_for_table(graph, fqn)
        except Exception:
            cols = []

        key_col_names: List[str] = []
        for c in cols:
            if c.get("is_pk") or c.get("is_fk") or c.get("is_indexed"):
                key_col_names.append(c["name"])
        for c in cols:
            if c["name"] not in key_col_names:
                key_col_names.append(c["name"])
            if len(key_col_names) >= 8:
                break

        desc = f"- {name}{tier_tag}"
        if comment:
            desc += f": {comment[:80]}"
        if key_col_names:
            desc += f" [key columns: {', '.join(key_col_names)}]"
        lines.append(desc)

    suffix = ""
    if len(all_tables) > _MAX_TABLES_IN_PROMPT:
        suffix = (
            f"\n(Showing top {_MAX_TABLES_IN_PROMPT} of {len(all_tables)} tables "
            "ranked by importance. Others exist — infer from context.)"
        )

    table_list_text = "\n".join(lines) + suffix
    all_table_names = [t.get("name", "") for t in all_tables if t.get("name")]
    return table_list_text, all_table_names


def _build_system_prompt(graph=None) -> Tuple[str, List[str]]:
    """
    Return (system_prompt, all_table_names).
    When graph is provided the prompt lists actual tables; otherwise falls back
    to a generic Oracle extraction prompt.
    """
    if graph is not None:
        table_list, all_names = _build_schema_summary(graph)
        schemas = sorted({
            t.get("schema", "") for t in graph.get_all_nodes("Table") if t.get("schema")
        })
        schema_str = ", ".join(schemas) if schemas else "unknown"
    else:
        table_list = "(schema not loaded)"
        all_names = []
        schema_str = "unknown"

    prompt = f"""You are an entity extractor for an Oracle database (schema(s): {schema_str}).

The database has these tables:
{table_list}

Extract entities from the user query and respond ONLY with valid JSON:
{{
  "tables": ["TABLE_NAME1", "TABLE_NAME2"],
  "columns": ["COL1", "COL2"],
  "conditions": ["col = 'VALUE'", "amount > 10000"],
  "time_range": "last quarter",
  "aggregations": ["COUNT", "SUM"],
  "sort_by": "amount DESC",
  "limit": null
}}

Rules:
- tables: use UPPERCASE table NAME (not schema-qualified) from the list above; infer the most likely tables from the user's intent; include all tables that need to be joined
- columns: use UPPERCASE column names as they appear in the schema
- conditions: write Oracle SQL-style predicates where possible
- time_range: extract any temporal reference as a string, null if none
- aggregations: only include if aggregation is explicitly or implicitly needed
- sort_by: null if no ordering is implied
- limit: integer if a specific row count is requested, null otherwise

Return ONLY the JSON object. No explanation."""

    return prompt, all_names


# ---------------------------------------------------------------------------
# Fallback entity extraction (no LLM or LLM returned non-JSON)
# ---------------------------------------------------------------------------

def _fallback_extract(user_input: str, all_table_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Keyword-based entity extraction.
    Matches against actual graph table names when provided; otherwise returns
    an empty tables list so the context builder can apply its own fallback.
    """
    text = user_input.upper()
    found_tables: List[str] = []

    if all_table_names:
        for name in all_table_names:
            name_upper = name.upper()
            # Simple heuristic: match by name or singular/plural
            if (name_upper in text
                    or name_upper.rstrip("S") in text
                    or text.rstrip("S") in name_upper):
                found_tables.append(name)
        found_tables = found_tables[:5]  # cap at 5

    # Time ranges
    time_range: Optional[str] = None
    for kw, val in {
        "LAST MONTH":   "last month",
        "LAST QUARTER": "last quarter",
        "LAST YEAR":    "last year",
        "THIS YEAR":    "this year",
        "THIS MONTH":   "this month",
        "PAST YEAR":    "past year",
        "PAST MONTH":   "past month",
    }.items():
        if kw in text:
            time_range = val
            break

    # Aggregations
    aggregations: List[str] = []
    if any(kw in text for kw in ("HOW MANY", "COUNT", "TOTAL NUMBER", "NUMBER OF")):
        aggregations.append("COUNT")
    if any(kw in text for kw in ("SUM", "TOTAL AMOUNT", "TOTAL VALUE")):
        aggregations.append("SUM")
    if any(kw in text for kw in ("AVERAGE", "AVG", "MEAN")):
        aggregations.append("AVG")

    return {
        "tables":       found_tables,  # empty list → context builder picks by connectivity
        "columns":      [],
        "conditions":   [],
        "time_range":   time_range,
        "aggregations": aggregations,
        "sort_by":      None,
        "limit":        None,
    }


# ---------------------------------------------------------------------------
# Node factory
# ---------------------------------------------------------------------------

def make_entity_extractor(llm, graph=None) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that extracts business entities.

    Parameters
    ----------
    llm : BaseChatModel
        A LangChain chat model instance.
    graph : KnowledgeGraph | None
        The populated in-memory knowledge graph.  When provided, the LLM system
        prompt is built from the actual tables instead of a hardcoded schema.
    """
    system_prompt, all_table_names = _build_system_prompt(graph)
    logger.info(
        "Entity extractor initialised: %d tables in prompt schema",
        len(all_table_names),
    )

    def extract_entities(state: AgentState) -> AgentState:
        user_input = state.get("user_input", "")
        logger.debug("Extracting entities from: %r", user_input[:100])

        entities: Dict[str, Any] = {
            "tables":       [],
            "columns":      [],
            "conditions":   [],
            "time_range":   None,
            "aggregations": [],
            "sort_by":      None,
            "limit":        None,
        }

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"User query: {user_input}"),
            ]
            response = llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)

            # Extract JSON — handle markdown code blocks
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                parsed = json.loads(json_match.group())
                entities.update({k: v for k, v in parsed.items() if v is not None})
                entities["tables"] = [
                    t.upper() for t in entities.get("tables", []) if isinstance(t, str)
                ]
                entities["columns"] = [
                    c.upper() for c in entities.get("columns", []) if isinstance(c, str)
                ]
                entities["aggregations"] = [
                    a.upper() for a in entities.get("aggregations", []) if isinstance(a, str)
                ]
            else:
                logger.warning(
                    "Entity extractor returned non-JSON: %r", content[:200]
                )
                entities = _fallback_extract(user_input, all_table_names)

        except Exception as exc:
            logger.error("Entity extraction failed: %s", exc)
            entities = _fallback_extract(user_input, all_table_names)

        # If tables list is still empty, leave it empty so the context builder
        # applies its connectivity-ranked fallback (better than hardcoding a name).
        if not entities.get("tables"):
            logger.info(
                "No tables extracted — context builder will apply connectivity fallback"
            )

        logger.info(
            "Entities extracted: tables=%s, conditions=%d",
            entities.get("tables"),
            len(entities.get("conditions", [])),
        )

        return {**state, "entities": entities, "step": "entities_extracted"}

    return extract_entities
