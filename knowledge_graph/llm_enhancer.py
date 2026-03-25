"""
LLM Graph Enhancer
==================
Uses an LLM to post-process the KnowledgeGraph after the initial Oracle-metadata
build:

  1. _assign_table_importance  — asks the LLM to rank every table by business
     centrality and stores importance_rank / importance_tier / importance_reason
     on each Table node.  Tables are sorted by FK degree + row_count before being
     sent to the LLM so the model always sees the most structurally important
     ones first (context priming).

  2. _infer_missing_relationships — finds tables with no JOIN_PATH edges (i.e.
     the Oracle FK query returned nothing for them) and asks the LLM to infer
     likely join relationships from column-name patterns.  Confirmed pairs get
     synthesised JOIN_PATH edges (source="llm_inferred") so the context builder
     and entity extractor can use them normally.

  3. _fill_missing_descriptions — tables whose Oracle ALL_TAB_COMMENTS entry is
     NULL get an LLM-generated one-line description stored as llm_description.

All three steps are batched (≤50 tables per LLM call) and individually wrapped
in try/except so a failure in one step never blocks the others.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Maximum tables per LLM call (keeps prompts within token budget)
_BATCH_SIZE = 50

# Only infer FKs for columns whose names look like foreign keys
_FK_COLUMN_SUFFIXES = ("_ID", "_CODE", "_KEY", "_FK", "_NUM", "_NO", "_REF")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enhance_graph_with_llm(graph, llm) -> Dict[str, Any]:
    """
    Run all three LLM enhancement passes on *graph* (in-place).

    Parameters
    ----------
    graph : KnowledgeGraph
        Populated in-memory graph (from GraphBuilder.build()).
    llm : BaseChatModel
        Any LangChain chat model (openai / anthropic / vertex).

    Returns
    -------
    dict  — report with counts: tables_ranked, fks_inferred, descriptions_added, errors
    """
    report: Dict[str, Any] = {
        "tables_ranked": 0,
        "fks_inferred": 0,
        "descriptions_added": 0,
        "errors": [],
    }

    try:
        report["tables_ranked"] = _assign_table_importance(graph, llm)
        logger.info("LLM importance ranking: %d tables ranked", report["tables_ranked"])
    except Exception as exc:
        logger.warning("LLM table ranking failed: %s", exc)
        report["errors"].append(f"table_ranking: {exc}")

    try:
        report["fks_inferred"] = _infer_missing_relationships(graph, llm)
        logger.info("LLM FK inference: %d new join paths added", report["fks_inferred"])
    except Exception as exc:
        logger.warning("LLM FK inference failed: %s", exc)
        report["errors"].append(f"fk_inference: {exc}")

    try:
        report["descriptions_added"] = _fill_missing_descriptions(graph, llm)
        logger.info("LLM descriptions: %d tables enriched", report["descriptions_added"])
    except Exception as exc:
        logger.warning("LLM description fill failed: %s", exc)
        report["errors"].append(f"descriptions: {exc}")

    return report


# ---------------------------------------------------------------------------
# Step 1 — Table importance ranking
# ---------------------------------------------------------------------------

def _assign_table_importance(graph, llm) -> int:
    """
    Ask the LLM to rank all tables by business importance.

    The prompt is built from table name, schema, comments, FK degree, and
    row count.  Tables are first sorted by FK degree so the context is not
    random.  Results are stored as:

      - importance_rank   (int: 1 = most important)
      - importance_tier   (str: "core" | "reference" | "audit" | "utility")
      - importance_reason (str: one-line justification)
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    tables = graph.get_all_nodes("Table")
    if not tables:
        return 0

    def _jp_degree(fqn: str) -> int:
        return len(graph.get_out_edges("JOIN_PATH", fqn))

    # Build compact info list, pre-sorted by FK degree so the LLM sees the
    # most connected tables first and can calibrate its rankings.
    table_info: List[Dict[str, Any]] = []
    for t in tables:
        fqn = t.get("fqn", "")
        table_info.append({
            "fqn": fqn,
            "name": t.get("name", ""),
            "schema": t.get("schema", ""),
            "comments": (t.get("comments") or "")[:120],
            "fk_degree": _jp_degree(fqn),
            "row_count": t.get("row_count") or 0,
        })
    table_info.sort(key=lambda x: (-x["fk_degree"], -(x["row_count"]), x["name"]))

    system_prompt = (
        "You are a database schema expert. "
        "Rank the provided tables by their business importance and structural centrality. "
        "Respond ONLY with the JSON object shown in the instructions — no prose."
    )

    batches = [table_info[i:i + _BATCH_SIZE] for i in range(0, len(table_info), _BATCH_SIZE)]
    all_rankings: Dict[str, Dict[str, Any]] = {}
    global_offset = 0

    for batch in batches:
        user_msg = f"""Rank these {len(batch)} database tables from most to least important.

Tables (JSON):
{json.dumps(batch, indent=2)}

Criteria (in order of priority):
1. fk_degree — tables referenced by many others are more central
2. row_count — high-volume fact/transaction tables score higher than empty lookup tables
3. name/comments — "master", "main", "transaction", "order" tables > "audit", "log", "history" tables
4. schema — application schemas > audit/config schemas

Respond ONLY with valid JSON:
{{
  "rankings": [
    {{"fqn": "SCHEMA.TABLE", "rank": 1, "tier": "core", "reason": "one-sentence why"}},
    ...
  ]
}}

Tiers:
  "core"      — central fact or master tables (most JOINs point here)
  "reference" — lookup / code / type tables
  "audit"     — history, log, archive tables
  "utility"   — system, config, temp tables

Return ALL {len(batch)} tables ranked. rank must start from {global_offset + 1}."""

        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_msg),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                parsed = json.loads(json_match.group())
                for item in parsed.get("rankings", []):
                    fqn = item.get("fqn", "")
                    if fqn:
                        all_rankings[fqn] = {
                            "rank": int(item.get("rank", global_offset + 999)),
                            "tier": item.get("tier", "utility"),
                            "reason": item.get("reason", ""),
                        }
        except Exception as exc:
            logger.warning("LLM ranking batch failed: %s", exc)

        global_offset += len(batch)

    # Apply to graph nodes
    ranked_count = 0
    for t in tables:
        fqn = t.get("fqn", "")
        if fqn in all_rankings:
            info = all_rankings[fqn]
            graph.set_node_prop("Table", fqn, "importance_rank", info["rank"])
            graph.set_node_prop("Table", fqn, "importance_tier", info["tier"])
            graph.set_node_prop("Table", fqn, "importance_reason", info["reason"])
            ranked_count += 1
        else:
            # Structural fallback: tables the LLM missed get a high rank number
            graph.set_node_prop("Table", fqn, "importance_rank", global_offset + 999)
            graph.set_node_prop("Table", fqn, "importance_tier", "utility")

    logger.debug("Importance ranks applied to %d/%d tables", ranked_count, len(tables))
    return ranked_count


# ---------------------------------------------------------------------------
# Step 2 — FK inference for isolated tables
# ---------------------------------------------------------------------------

def _infer_missing_relationships(graph, llm) -> int:
    """
    For tables that have no JOIN_PATH edges, ask the LLM to infer likely FK
    relationships from column-name patterns.  Confirmed pairs get JOIN_PATH
    edges (weight=1, source="llm_inferred") added in both directions.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        from knowledge_graph.traversal import get_columns_for_table
    except Exception:
        return 0

    all_tables = graph.get_all_nodes("Table")
    if not all_tables:
        return 0

    # Build table name → fqn lookup (case-insensitive)
    name_to_fqn: Dict[str, str] = {
        t.get("name", "").upper(): t.get("fqn", "")
        for t in all_tables if t.get("name") and t.get("fqn")
    }

    # Identify isolated tables (no outgoing JOIN_PATH)
    isolated: List[Dict[str, Any]] = []
    for t in all_tables:
        fqn = t.get("fqn", "")
        if not fqn:
            continue
        if not graph.get_out_edges("JOIN_PATH", fqn):
            cols = get_columns_for_table(graph, fqn)
            if cols:
                isolated.append({"fqn": fqn, "name": t.get("name", ""), "cols": cols})

    if not isolated:
        logger.debug("No isolated tables — FK inference skipped")
        return 0

    logger.info("FK inference: %d isolated tables (no JOIN_PATH)", len(isolated))

    # Candidate columns: those that look like FK-pointing columns
    def _is_fk_candidate(col_name: str) -> bool:
        upper = col_name.upper()
        return any(upper.endswith(s) for s in _FK_COLUMN_SUFFIXES)

    # Build all-table summary for LLM context (name + PK columns only)
    all_table_summary = []
    for t in all_tables:
        fqn = t.get("fqn", "")
        cols = get_columns_for_table(graph, fqn)
        pk_cols = [c["name"] for c in cols if c.get("is_pk")]
        all_table_summary.append({
            "fqn": fqn,
            "name": t.get("name", ""),
            "pk_columns": pk_cols[:5],
        })

    system_prompt = (
        "You are a database schema expert. "
        "Infer likely foreign key relationships from column name patterns. "
        "Respond ONLY with valid JSON."
    )

    inferred_count = 0
    # Process isolated tables in batches of 10
    for batch_start in range(0, len(isolated), 10):
        batch = isolated[batch_start: batch_start + 10]
        candidates_payload = []
        for entry in batch:
            fk_cols = [
                c["name"] for c in entry["cols"]
                if _is_fk_candidate(c["name"]) and not c.get("is_pk")
            ]
            if fk_cols:
                candidates_payload.append({
                    "fqn": entry["fqn"],
                    "name": entry["name"],
                    "potential_fk_columns": fk_cols[:10],
                })

        if not candidates_payload:
            continue

        user_msg = f"""These tables have no known foreign key relationships. Examine their column names and identify likely FK references to other tables.

Isolated tables (with candidate FK columns):
{json.dumps(candidates_payload, indent=2)}

All available tables (with primary key columns):
{json.dumps(all_table_summary[:60], indent=2)}

Rules:
- A column like DEPT_ID in table EMPLOYEES likely references DEPARTMENTS.DEPT_ID
- Match by column name similarity and common naming conventions (_ID, _CODE, _KEY)
- Only include relationships you are confident about (confidence HIGH or MEDIUM)
- Ignore self-references (same table)

Respond ONLY with valid JSON:
{{
  "inferred_fks": [
    {{
      "src_table_fqn": "SCHEMA.TABLE",
      "src_column": "COLUMN_NAME",
      "tgt_table_fqn": "SCHEMA.OTHER_TABLE",
      "tgt_column": "COLUMN_NAME",
      "confidence": "high",
      "reason": "brief reason"
    }}
  ]
}}"""

        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_msg),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            json_match = re.search(r"\{[\s\S]*\}", content)
            if not json_match:
                continue
            parsed = json.loads(json_match.group())

            for inferred in parsed.get("inferred_fks", []):
                confidence = inferred.get("confidence", "low").lower()
                if confidence not in ("high", "medium"):
                    continue

                src_tbl = inferred.get("src_table_fqn", "")
                src_col = inferred.get("src_column", "")
                tgt_tbl = inferred.get("tgt_table_fqn", "")
                tgt_col = inferred.get("tgt_column", "")

                if not (src_tbl and src_col and tgt_tbl and tgt_col):
                    continue
                if src_tbl == tgt_tbl:
                    continue
                if not graph.get_node("Table", src_tbl) or not graph.get_node("Table", tgt_tbl):
                    continue

                src_col_fqn = f"{src_tbl}.{src_col.upper()}"
                tgt_col_fqn = f"{tgt_tbl}.{tgt_col.upper()}"
                join_col = {
                    "src": src_col_fqn,
                    "tgt": tgt_col_fqn,
                    "constraint": "llm_inferred",
                }

                # Add JOIN_PATH in both directions
                for from_t, to_t, jc in [
                    (src_tbl, tgt_tbl, join_col),
                    (tgt_tbl, src_tbl, {"src": tgt_col_fqn, "tgt": src_col_fqn, "constraint": "llm_inferred"}),
                ]:
                    path_key = f"{from_t}>>{to_t}"
                    # Only add if no existing JOIN_PATH between these tables
                    existing = [
                        e for e in graph.get_out_edges("JOIN_PATH", from_t)
                        if e.get("_to") == to_t
                    ]
                    if not existing:
                        graph.merge_edge(
                            "JOIN_PATH", from_t, to_t,
                            merge_key="path_key",
                            path_key=path_key,
                            join_columns=[jc],
                            join_type="INNER",
                            cardinality="N:1",
                            weight=1,
                            source="llm_inferred",
                        )
                        inferred_count += 1

                logger.debug(
                    "LLM FK inferred: %s.%s → %s.%s (confidence=%s)",
                    src_tbl, src_col, tgt_tbl, tgt_col, confidence,
                )

        except Exception as exc:
            logger.warning("LLM FK inference batch failed: %s", exc)

    return inferred_count


# ---------------------------------------------------------------------------
# Step 3 — Fill missing descriptions
# ---------------------------------------------------------------------------

def _fill_missing_descriptions(graph, llm) -> int:
    """
    Generate one-line descriptions for tables that have no Oracle comment.
    Stored as 'llm_description' on the Table node (never overwrites 'comments').
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        from knowledge_graph.traversal import get_columns_for_table
    except Exception:
        return 0

    tables_needing_desc = [
        t for t in graph.get_all_nodes("Table")
        if not (t.get("comments") or "").strip() and t.get("fqn")
    ]

    if not tables_needing_desc:
        return 0

    logger.info("Generating descriptions for %d tables without Oracle comments", len(tables_needing_desc))

    system_prompt = (
        "You are a database documentation expert. "
        "Generate concise one-line descriptions for database tables. "
        "Respond ONLY with valid JSON."
    )
    added = 0

    for batch_start in range(0, len(tables_needing_desc), _BATCH_SIZE):
        batch = tables_needing_desc[batch_start: batch_start + _BATCH_SIZE]
        payload = []
        for t in batch:
            fqn = t.get("fqn", "")
            cols = get_columns_for_table(graph, fqn)
            col_names = [c["name"] for c in cols[:12]]
            payload.append({
                "fqn": fqn,
                "name": t.get("name", ""),
                "schema": t.get("schema", ""),
                "columns": col_names,
            })

        user_msg = f"""Generate a single-sentence description for each of these database tables based on its name and column names.

Tables:
{json.dumps(payload, indent=2)}

Respond ONLY with valid JSON:
{{
  "descriptions": [
    {{"fqn": "SCHEMA.TABLE", "description": "Stores ... for ..."}}
  ]
}}
Return ALL {len(batch)} tables. Keep each description under 120 characters."""

        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_msg),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            json_match = re.search(r"\{[\s\S]*\}", content)
            if not json_match:
                continue
            parsed = json.loads(json_match.group())
            for item in parsed.get("descriptions", []):
                fqn = item.get("fqn", "")
                desc = item.get("description", "").strip()
                if fqn and desc:
                    graph.set_node_prop("Table", fqn, "llm_description", desc)
                    added += 1
        except Exception as exc:
            logger.warning("LLM description batch failed: %s", exc)

    return added
