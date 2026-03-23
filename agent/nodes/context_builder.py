"""
Context Builder Node
=====================
Retrieves the relevant schema subgraph from the KnowledgeGraph and serializes
it to DDL-format text suitable for injection into an LLM prompt.

Algorithm:
  1. Get entity table hints from state["entities"]["tables"]
  2. Use search_schema() for each hint to find matching Table FQNs
  3. Use resolve_business_term() for business term resolution
  4. Deduplicate collected FQNs
  5. Call get_context_subgraph() to build the full context
  6. Serialize with serialize_context_to_ddl() and truncate to token budget
  7. Optionally enrich with similar column hints
  8. Store result as state["schema_context"]
"""

from __future__ import annotations

import logging
from typing import Callable, List, Set

from knowledge_graph.graph_store import KnowledgeGraph
from knowledge_graph.traversal import (
    find_join_path,
    get_context_subgraph,
    get_similar_columns,
    resolve_business_term,
    search_schema,
    serialize_context_to_ddl,
)
from agent.state import AgentState

logger = logging.getLogger(__name__)

# Approximate characters per token (conservative estimate)
_CHARS_PER_TOKEN = 3.5


def make_context_builder(graph: KnowledgeGraph) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that retrieves schema context.

    Parameters
    ----------
    graph : KnowledgeGraph
        The populated in-memory knowledge graph.

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def retrieve_schema(state: AgentState) -> AgentState:
        entities = state.get("entities", {})
        table_hints: List[str] = entities.get("tables", [])
        token_budget: int = state.get("token_budget", 4000) if "token_budget" in state else 4000

        # Try to get token_budget from config if it was passed via state
        # Default to 4000 tokens → ~14000 characters
        char_budget = int(token_budget * _CHARS_PER_TOKEN) if token_budget else 14000

        logger.debug("Context builder: table_hints=%s", table_hints)

        collected_fqns: Set[str] = set()

        # --- Step 1: Search schema by table name hints ---
        for hint in table_hints:
            results = search_schema(graph, hint, limit=5)
            for r in results:
                if r.get("label") == "Table":
                    collected_fqns.add(r["fqn"])
                elif r.get("label") == "BusinessTerm":
                    # Follow MAPS_TO edges to find table FQNs
                    bt_results = resolve_business_term(graph, hint)
                    for bt in bt_results:
                        target_fqn = bt.get("target_fqn", "")
                        # Check if target is a table
                        if graph.get_node("Table", target_fqn):
                            collected_fqns.add(target_fqn)

        # --- Step 2: Business term resolution for all hints ---
        for hint in table_hints:
            bt_results = resolve_business_term(graph, hint)
            for bt in bt_results[:3]:  # top 3 mappings per term
                target_fqn = bt.get("target_fqn", "")
                if graph.get_node("Table", target_fqn):
                    collected_fqns.add(target_fqn)

        # --- Step 3: If no tables found, use first search result of any kind ---
        if not collected_fqns and table_hints:
            for hint in table_hints:
                results = search_schema(graph, hint, limit=10)
                for r in results:
                    # Try to find the parent table for column matches
                    if r.get("label") == "Column":
                        fqn = r.get("fqn", "")
                        # Extract table FQN from column FQN (SCHEMA.TABLE.COLUMN)
                        parts = fqn.rsplit(".", 1)
                        if len(parts) == 2:
                            table_fqn = parts[0]
                            if graph.get_node("Table", table_fqn):
                                collected_fqns.add(table_fqn)
                    elif r.get("label") == "Table":
                        collected_fqns.add(r["fqn"])
                    if collected_fqns:
                        break

        # --- Step 4: Fallback — if still nothing, include all tables ---
        if not collected_fqns:
            logger.warning(
                "No tables found for hints %s; including all tables", table_hints
            )
            all_tables = graph.get_all_nodes("Table")
            for t in all_tables[:5]:  # limit to 5 tables to stay within budget
                collected_fqns.add(t.get("fqn", ""))

        logger.info("Context builder: resolved %d table FQNs: %s", len(collected_fqns), collected_fqns)

        # --- Step 5: Get context subgraph and serialize ---
        context = get_context_subgraph(graph, list(collected_fqns))
        ddl_text = serialize_context_to_ddl(context)

        # --- Step 6: Truncate to token budget ---
        if len(ddl_text) > char_budget:
            ddl_text = ddl_text[:char_budget] + "\n-- [Schema truncated to fit token budget]"

        # --- Step 7: Enrich with similar column hints for FK awareness ---
        column_hints: List[str] = entities.get("columns", [])
        similar_hints: List[str] = []
        for col_name in column_hints[:3]:
            # Find column FQNs matching this name
            for table_fqn in collected_fqns:
                col_fqn = f"{table_fqn}.{col_name.upper()}"
                sim_cols = get_similar_columns(graph, col_fqn, limit=3)
                for sc in sim_cols:
                    hint_line = f"-- Similar column: {sc['fqn']} ({sc['data_type']}, score={sc['score']:.2f})"
                    similar_hints.append(hint_line)

        if similar_hints:
            ddl_text += "\n\n-- SIMILAR COLUMNS (cross-table FK hints):\n" + "\n".join(similar_hints[:10])

        # --- Step 8: Add join path hints if multiple tables ---
        fqn_list = list(collected_fqns)
        if len(fqn_list) >= 2:
            join_hints = []
            for i in range(len(fqn_list)):
                for j in range(i + 1, len(fqn_list)):
                    path = find_join_path(graph, fqn_list[i], fqn_list[j], max_hops=4)
                    if path and path.get("source") == "precomputed":
                        join_cols = path.get("join_columns", [])
                        if join_cols:
                            hint = f"-- JOIN PATH: {fqn_list[i]} → {fqn_list[j]} via {join_cols}"
                            join_hints.append(hint)
            if join_hints:
                ddl_text += "\n\n-- SUGGESTED JOIN PATHS:\n" + "\n".join(join_hints[:5])

        return {**state, "schema_context": ddl_text, "step": "schema_retrieved"}

    return retrieve_schema
