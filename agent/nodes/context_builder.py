"""
Context Builder Node
=====================
Retrieves the relevant schema subgraph from the KnowledgeGraph and serializes
it to DDL-format text suitable for injection into an LLM prompt.

Algorithm:
  1. Get entity table hints from state["entities"]["tables"]
  2. Use search_schema() for each hint to find matching Table FQNs
  3. Use resolve_business_term() for business term resolution
  4. Expand by 1-hop FK neighbours so the LLM has context for every JOIN it needs
  5. If still no tables found, fall back to the most FK-connected tables in the graph
  6. Call get_context_subgraph() to build the full context
  7. Serialize with serialize_context_to_ddl() and truncate to token budget
  8. Enrich with similar column hints and pre-computed join path hints
  9. Store result as state["schema_context"]
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
# Maximum tables to include in the context subgraph (budget guard)
_MAX_CONTEXT_TABLES = 10


def make_context_builder(graph: KnowledgeGraph) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that retrieves schema context.

    Parameters
    ----------
    graph : KnowledgeGraph
        The populated in-memory knowledge graph.
    """

    # Pre-rank all tables by JOIN_PATH connectivity so the fallback is fast.
    def _jp_degree(fqn: str) -> int:
        return len(graph.get_out_edges("JOIN_PATH", fqn))

    def retrieve_schema(state: AgentState) -> AgentState:
        entities = state.get("entities", {})
        table_hints: List[str] = entities.get("tables", [])
        token_budget: int = state.get("token_budget", 4000) if "token_budget" in state else 4000
        char_budget = int(token_budget * _CHARS_PER_TOKEN) if token_budget else 14_000

        logger.debug("Context builder: table_hints=%s", table_hints)

        collected_fqns: Set[str] = set()

        # --- Step 1: Search schema by table name hints ---
        for hint in table_hints:
            results = search_schema(graph, hint, limit=5)
            for r in results:
                if r.get("label") == "Table":
                    collected_fqns.add(r["fqn"])
                elif r.get("label") == "BusinessTerm":
                    bt_results = resolve_business_term(graph, hint)
                    for bt in bt_results:
                        target_fqn = bt.get("target_fqn", "")
                        if graph.get_node("Table", target_fqn):
                            collected_fqns.add(target_fqn)

        # --- Step 2: Business term resolution for all hints ---
        for hint in table_hints:
            bt_results = resolve_business_term(graph, hint)
            for bt in bt_results[:3]:
                target_fqn = bt.get("target_fqn", "")
                if graph.get_node("Table", target_fqn):
                    collected_fqns.add(target_fqn)

        # --- Step 3: Column-level fallback — derive parent table from column FQN ---
        if not collected_fqns and table_hints:
            for hint in table_hints:
                results = search_schema(graph, hint, limit=10)
                for r in results:
                    if r.get("label") == "Column":
                        parts = r.get("fqn", "").rsplit(".", 1)
                        if len(parts) == 2:
                            table_fqn = parts[0]
                            if graph.get_node("Table", table_fqn):
                                collected_fqns.add(table_fqn)
                    elif r.get("label") == "Table":
                        collected_fqns.add(r["fqn"])
                    if collected_fqns:
                        break

        # --- Step 4: Expand by 1-hop FK neighbours via pre-computed JOIN_PATHs ---
        # This ensures the LLM has DDL context for every table it will need to JOIN,
        # not just the directly-named tables. Only expand when the set is small so we
        # don't overflow the token budget.
        if collected_fqns and len(collected_fqns) <= 4:
            neighbors: Set[str] = set()
            for fqn in list(collected_fqns):
                for jp_edge in graph.get_out_edges("JOIN_PATH", fqn):
                    neighbor_fqn = jp_edge["_to"]
                    if neighbor_fqn not in collected_fqns:
                        neighbors.add(neighbor_fqn)

            # Add neighbours sorted by their own connectivity (most connected first)
            sorted_neighbors = sorted(neighbors, key=_jp_degree, reverse=True)
            for n in sorted_neighbors:
                if len(collected_fqns) >= _MAX_CONTEXT_TABLES:
                    break
                collected_fqns.add(n)
                logger.debug("Context builder: added FK neighbour %s", n)

            # --- Step 4b: SIMILAR_TO expansion — no JOIN_PATH edges found ---
            # When the DB has no FK constraints (so no JOIN_PATHs), use column-name
            # similarity edges as a secondary join-inference mechanism.
            if not neighbors and len(collected_fqns) < _MAX_CONTEXT_TABLES:
                for e in graph.get_all_edges("SIMILAR_TO"):
                    if e.get("score", 0) < 0.85:
                        continue  # only high-confidence similarity
                    src_col = e.get("_from", "")
                    tgt_col = e.get("_to", "")
                    # Column FQN is SCHEMA.TABLE.COLUMN — drop last part to get table FQN
                    src_table = src_col.rsplit(".", 1)[0] if "." in src_col else ""
                    tgt_table = tgt_col.rsplit(".", 1)[0] if "." in tgt_col else ""
                    if src_table in collected_fqns and tgt_table not in collected_fqns:
                        if graph.get_node("Table", tgt_table):
                            collected_fqns.add(tgt_table)
                            logger.debug(
                                "Context builder: added SIMILAR_TO neighbour %s (score=%.2f)",
                                tgt_table, e.get("score", 0),
                            )
                    if len(collected_fqns) >= _MAX_CONTEXT_TABLES:
                        break

        # --- Step 5: Connectivity-ranked fallback — no tables resolved at all ---
        if not collected_fqns:
            logger.warning(
                "No tables found for hints %s; falling back to most important tables",
                table_hints,
            )
            all_tables = graph.get_all_nodes("Table")

            # Sort priority: LLM importance_rank (1=best) > JOIN_PATH degree > row_count
            # With reverse=True, higher tuple values sort first.
            def _fallback_key(t: dict) -> tuple:
                fqn = t.get("fqn", "")
                jp = _jp_degree(fqn)
                rows = t.get("row_count") or 0
                llm_rank = t.get("importance_rank")
                if llm_rank is not None:
                    # Bucket 1: LLM-ranked (rank=1 → -1, largest → sorts first)
                    return (1, -int(llm_rank), jp)
                # Bucket 0: structural metrics only
                return (0, jp, rows)

            ranked = sorted(all_tables, key=_fallback_key, reverse=True)
            for t in ranked[:5]:
                fqn = t.get("fqn", "")
                if fqn:
                    collected_fqns.add(fqn)

        logger.info(
            "Context builder: resolved %d table FQNs: %s",
            len(collected_fqns),
            collected_fqns,
        )

        # --- Step 6: Build context subgraph and serialize to DDL ---
        context = get_context_subgraph(graph, list(collected_fqns))
        ddl_text = serialize_context_to_ddl(context)

        # --- Step 7: Truncate to token budget ---
        if len(ddl_text) > char_budget:
            ddl_text = ddl_text[:char_budget] + "\n-- [Schema truncated to fit token budget]"

        # --- Step 8a: Enrich with similar column hints for FK awareness ---
        column_hints: List[str] = entities.get("columns", [])
        similar_hints: List[str] = []
        for col_name in column_hints[:3]:
            for table_fqn in collected_fqns:
                col_fqn = f"{table_fqn}.{col_name.upper()}"
                sim_cols = get_similar_columns(graph, col_fqn, limit=3)
                for sc in sim_cols:
                    similar_hints.append(
                        f"-- Similar column: {sc['fqn']} ({sc['data_type']}, score={sc['score']:.2f})"
                    )

        if similar_hints:
            ddl_text += "\n\n-- SIMILAR COLUMNS (cross-table FK hints):\n" + "\n".join(similar_hints[:10])

        # --- Step 8b: Add join path hints if multiple tables in context ---
        fqn_list = list(collected_fqns)
        if len(fqn_list) >= 2:
            join_hints: List[str] = []
            for i in range(len(fqn_list)):
                for j in range(i + 1, len(fqn_list)):
                    path = find_join_path(graph, fqn_list[i], fqn_list[j], max_hops=4)
                    if path and path.get("source") == "precomputed":
                        join_cols = path.get("join_columns", [])
                        if join_cols:
                            join_hints.append(
                                f"-- JOIN PATH: {fqn_list[i]} → {fqn_list[j]} via {join_cols}"
                            )
            if join_hints:
                ddl_text += "\n\n-- SUGGESTED JOIN PATHS:\n" + "\n".join(join_hints[:8])

        return {**state, "schema_context": ddl_text, "step": "schema_retrieved"}

    return retrieve_schema
