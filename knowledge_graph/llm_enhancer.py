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
# Robust JSON extractor — handles Gemini thinking tags, trailing commas, fences
# ---------------------------------------------------------------------------

def _parse_json_robust(content: str) -> Any:
    """
    Extract and parse a JSON object/array from an LLM response, tolerating:
    - <thinking>...</thinking> blocks (Gemini 2.5 Pro with thinking budget > 0)
    - Markdown code fences (```json ... ```)
    - Trailing commas before ] or } (common LLM formatting mistake)
    - Prose before/after the JSON
    - Multiple JSON documents back-to-back (Gemini 2.0 Flash sometimes emits these)

    Uses JSONDecoder.raw_decode() to parse the FIRST complete document and
    ignore any trailing content, avoiding "Extra data" errors.
    """
    # 1. Strip thinking tags emitted by Gemini 2.5 Pro
    content = re.sub(r"<thinking>[\s\S]*?</thinking>", "", content, flags=re.IGNORECASE)

    # 2. Try to extract from a markdown code fence first
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
    candidate = fence_match.group(1).strip() if fence_match else content

    # 3. Locate the start of the first JSON value (object or array)
    first_brace = candidate.find("{")
    first_bracket = candidate.find("[")
    starts = [i for i in (first_brace, first_bracket) if i >= 0]
    if not starts:
        raise ValueError("No JSON object or array found in LLM response")
    text = candidate[min(starts):]

    # 4. Remove trailing commas before } or ] (e.g. [...,] or {...,})
    text = re.sub(r",\s*([\]}])", r"\1", text)

    # 5. raw_decode: parse one complete document, ignore trailing content
    decoder = json.JSONDecoder()
    try:
        result, _end = decoder.raw_decode(text)
        return result
    except json.JSONDecodeError as exc:
        # Last-ditch: try plain json.loads on the cleaned candidate
        try:
            return json.loads(re.sub(r",\s*([\]}])", r"\1", candidate))
        except json.JSONDecodeError:
            raise exc


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

    def _oracle_fk_count(fqn: str) -> int:
        """Count Oracle-derived FK edges (HAS_COLUMN + HAS_FOREIGN_KEY), not LLM-inferred ones."""
        col_edges = graph.get_out_edges("HAS_COLUMN", fqn)
        count = 0
        for ce in col_edges:
            # Count FK edges FROM this table's columns
            count += len(graph.get_out_edges("HAS_FOREIGN_KEY", ce["_to"]))
        # Also count FKs pointing TO this table's columns
        for ce in col_edges:
            count += len(graph.get_in_edges("HAS_FOREIGN_KEY", ce["_to"]))
        return count

    # Load business knowledge to boost tables mentioned in it
    import os
    knowledge_file = os.getenv("KYC_KNOWLEDGE_FILE", "kyc_business_knowledge.txt")
    knowledge_text = ""
    try:
        with open(knowledge_file, encoding="utf-8") as f:
            knowledge_text = f.read().upper()
    except Exception:
        pass

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
            "oracle_fk_count": _oracle_fk_count(fqn),
            "row_count": t.get("row_count") or 0,
            "in_knowledge_file": t.get("name", "").upper() in knowledge_text,
        })
    table_info.sort(key=lambda x: (
        -int(x.get("in_knowledge_file", False)),
        -x.get("oracle_fk_count", 0),
        -x["fk_degree"],
        -(x["row_count"]),
        x["name"],
    ))

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
1. oracle_fk_count — tables with more direct Oracle FK relationships are more central
2. in_knowledge_file — tables explicitly documented in the business knowledge file are important
3. fk_degree — tables referenced by many others via join paths are more central
4. row_count — high-volume fact/transaction tables score higher than empty lookup tables
5. name/comments — "master", "main", "transaction", "order" tables > "audit", "log", "history" tables
6. schema — application schemas > audit/config schemas

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
            parsed = _parse_json_robust(content)
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
            parsed = _parse_json_robust(content)

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
            parsed = _parse_json_robust(content)
            for item in parsed.get("descriptions", []):
                fqn = item.get("fqn", "")
                desc = item.get("description", "").strip()
                if fqn and desc:
                    graph.set_node_prop("Table", fqn, "llm_description", desc)
                    added += 1
        except Exception as exc:
            logger.warning("LLM description batch failed: %s", exc)

    return added


# ---------------------------------------------------------------------------
# Filter-candidate nomination (Phase 1 / Layer 1 of value-grounded WHEREs)
# ---------------------------------------------------------------------------

_NOMINATION_SYSTEM_PROMPT = """You are a database schema analyst.
For each column you are shown, decide whether it is likely to be used as a
filter (WHERE col = 'value' or col IN (...)) AND has a small bounded set of
distinct values (typically <= 30 — status flags, codes, types, categories,
risk levels, currencies, country codes, etc.).

Do NOT flag:
- Free-text columns (names, descriptions, notes)
- Identifiers (IDs, account numbers, keys)
- Continuous numeric metrics (amounts, balances, scores, percentages)
- Date/time columns
- Long string columns

Output JSON ONLY, no prose, exactly:
{
  "candidates": [
    {"col_fqn": "SCHEMA.TABLE.COL",
     "is_filter_candidate": true,
     "confidence": "HIGH" | "MEDIUM" | "LOW",
     "reason": "short reason"}
  ]
}
Only include columns you flag as TRUE. Skip columns you reject."""


def nominate_filter_candidates_llm(graph, llm, batch_size: int = 50) -> int:
    """
    Ask the LLM to nominate filter-candidate columns the heuristic missed.

    Walks every Column node where ``is_filter_candidate`` is not already True,
    sends them in batches of *batch_size* to the LLM, and flags accepted ones
    with ``filter_reason="llm:<reason>"``.

    Returns
    -------
    int
        Number of new columns flagged by the LLM (excluding heuristic flags).
    """
    if llm is None:
        logger.info("LLM unavailable — skipping filter-candidate nomination.")
        return 0

    pending = []
    for col in graph.get_all_nodes("Column"):
        if col.get("is_filter_candidate"):
            continue
        pending.append({
            "col_fqn": col.get("fqn", ""),
            "name": col.get("name", ""),
            "data_type": col.get("data_type", ""),
            "data_length": col.get("data_length"),
            "data_precision": col.get("data_precision"),
            "comments": col.get("comments", ""),
        })

    if not pending:
        return 0

    logger.info(
        "LLM filter-candidate nomination: %d columns in %d batches",
        len(pending), (len(pending) + batch_size - 1) // batch_size,
    )

    accepted = 0
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start:batch_start + batch_size]
        try:
            accepted += _nominate_one_batch(graph, llm, batch)
        except Exception as exc:
            logger.warning(
                "LLM nomination batch %d failed: %s — skipping",
                batch_start // batch_size, exc,
            )
    logger.info("LLM filter-candidate nomination: flagged %d new columns", accepted)
    return accepted


def _nominate_one_batch(graph, llm, batch) -> int:
    """Send one batch to the LLM and apply the results."""
    from langchain_core.messages import HumanMessage, SystemMessage

    user_lines = ["Columns to evaluate:"]
    for c in batch:
        length_part = f"({c['data_length']})" if c.get("data_length") else ""
        precision_part = f" precision={c['data_precision']}" if c.get("data_precision") else ""
        comment_part = f" -- {c['comments']}" if c.get("comments") else ""
        user_lines.append(
            f"- {c['col_fqn']} | {c['data_type']}{length_part}{precision_part}{comment_part}"
        )
    response = llm.invoke([
        SystemMessage(content=_NOMINATION_SYSTEM_PROMPT),
        HumanMessage(content="\n".join(user_lines)),
    ])
    content = getattr(response, "content", str(response))

    try:
        parsed = _extract_json_object(content)
        candidates = parsed.get("candidates", []) if parsed else []
    except Exception as exc:
        logger.warning("Failed to parse LLM nomination response: %s", exc)
        return 0

    flagged_in_batch = 0
    for cand in candidates:
        if not cand.get("is_filter_candidate"):
            continue
        fqn = cand.get("col_fqn", "")
        if not fqn or graph.get_node("Column", fqn) is None:
            continue
        reason = (cand.get("reason") or cand.get("confidence") or "nominated")[:80]
        graph.merge_node("Column", fqn, {
            "is_filter_candidate": True,
            "filter_reason": f"llm:{reason}",
        })
        flagged_in_batch += 1
    return flagged_in_batch


def _extract_json_object(text: str):
    """Extract the first {...} JSON object from text. Handles markdown fences."""
    import json
    import re
    cleaned = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE).replace("```", "")
    start = cleaned.find("{")
    if start == -1:
        return None
    depth, end = 0, -1
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    return json.loads(cleaned[start:end + 1])
