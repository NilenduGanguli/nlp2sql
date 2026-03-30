"""
Business Knowledge File Generator
====================================
Generates the business knowledge file (``kyc_business_knowledge.txt``) from
live Oracle schema metadata + LLM, making a handful of focused API calls.

Invoked at app startup when the knowledge file is missing or empty.

Design principles:
  - NOT exhaustive — covers only the top N most important tables, chosen by
    LLM importance rank and FK connectivity (the "80% tables" that handle
    most queries).
  - Batches tables into groups of ~10 per LLM call to stay within context.
  - Writes a structured, human-editable text file.  Editing that file is the
    primary way to tune the system; regeneration only happens when the file is
    empty.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT_PATH = os.path.join(_PROJECT_ROOT, "kyc_business_knowledge.txt")

# Top-N tables to include (importance rank + degree, then capped here)
_DEFAULT_MAX_TABLES = 30
# Tables per LLM call
_BATCH_SIZE = 10

# ── Static SQL directives (always appended — schema-agnostic) ─────────────────

_STATIC_SQL_DIRECTIVES = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 3. SQL DIRECTIVES FOR THE GENERATOR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Always qualify table names with the schema prefix exactly as found in DDL.
- Use SYSDATE for current date — never GETDATE() or NOW().
- Use FETCH FIRST N ROWS ONLY for row limits (Oracle syntax, not LIMIT N).
- Prefer INNER JOIN unless the query asks for "all ... including those without ..."
  (then use LEFT JOIN).
- When counting distinct entities use COUNT(DISTINCT <pk_column>).
- For date truncation: TRUNC(column, 'DD') for daily, TRUNC(column, 'MM') for monthly.
- Amount/balance comparisons use raw numeric values, not strings.
- Status, type, and rating columns are VARCHAR2 uppercase strings.
- "recent" → within last 90 days: column >= SYSDATE - 90
- "last month" → TRUNC(column,'MM') = TRUNC(ADD_MONTHS(SYSDATE,-1),'MM')
================================================================================
"""

# ── LLM prompt templates ───────────────────────────────────────────────────────

_TABLE_BATCH_SYSTEM = """\
You are a database documentation expert writing a concise business knowledge \
reference for an NLP-to-SQL assistant.

You will receive metadata for a batch of Oracle database tables from a \
financial compliance / KYC system. For EACH table write a compact entry \
covering ONLY what is useful for answering natural-language business questions:

  1. Business purpose (1-2 sentences)
  2. Business term → column value mappings for that table
     (e.g. "high risk" → RISK_RATING = 'HIGH')
  3. Typical join relationships to other tables

Rules:
  - Be concise. Focus on business-relevant columns only.
  - Do NOT list primary key columns or technical audit columns.
  - Do NOT enumerate every column — only business-meaningful ones.
  - Separate each table entry with a line of dashes (---).
"""

_TABLE_BATCH_HUMAN = """\
Generate a brief business knowledge entry for each of these tables.

For each table use this format:
TABLE: <SCHEMA.TABLE_NAME>
PURPOSE: <1-2 sentence business purpose>
KEY TERMS:
  "<business phrase>" → <COLUMN> = '<VALUE>' (or condition)
JOINS:
  → <other_table> via <column> — <why>

--- TABLE METADATA ---
{tables_section}
--- END METADATA ---

Write entries for ALL tables listed. Keep each entry under 15 lines.
"""

_PATTERNS_SYSTEM = """\
You are a senior KYC analyst who also knows SQL well. Given the key tables in \
a financial compliance database, describe the most common analytical queries \
users ask — in plain English followed by the tables and joins needed.
"""

_PATTERNS_HUMAN = """\
Given these key tables in our KYC / financial compliance system:
{table_names}

Write 6-8 common query patterns that business users ask. Use this format:

  "<plain English question>"
    → Join chain: TABLE_A → TABLE_B → TABLE_C
    → Filter: <column condition>

Focus on patterns that combine 2-4 tables. Keep each pattern to 3 lines max.
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _select_key_tables(graph, max_tables: int) -> List[Dict[str, Any]]:
    """
    Return up to ``max_tables`` tables ranked by importance.

    Priority:
      1. ``importance_rank`` ascending (set by LLM enhancer; lower = more central)
      2. FK connectivity (JOIN_PATH degree) descending as tiebreaker
      3. Tables with no rank come last, ordered by degree
    """
    all_tables = graph.get_all_nodes("Table")
    if not all_tables:
        return []

    def _sort_key(t: Dict[str, Any]):
        rank = t.get("importance_rank")
        fqn = t.get("fqn", "")
        degree = (
            len(graph.get_out_edges("JOIN_PATH", fqn))
            + len(graph.get_in_edges("JOIN_PATH", fqn))
        )
        # Tables with a rank come first (rank 1 = most important)
        return (0 if rank else 1, rank or 9999, -degree)

    return sorted(all_tables, key=_sort_key)[:max_tables]


def _format_table_block(table: Dict[str, Any], graph) -> str:
    """Format one table's metadata for the LLM prompt."""
    from knowledge_graph.traversal import get_columns_for_table

    fqn   = table.get("fqn", "")
    name  = table.get("name", fqn)
    schema = table.get("schema", "")
    tier  = table.get("importance_tier", "")
    desc  = table.get("comments") or table.get("llm_description") or ""
    rows  = table.get("row_count")

    lines: List[str] = []
    heading = f"TABLE: {schema + '.' if schema else ''}{name}"
    if tier:
        heading += f"  [{tier}]"
    lines.append(heading)

    if desc:
        lines.append(f"  DB description: {desc}")
    if rows:
        lines.append(f"  Approx rows: {rows:,}")

    # Columns — skip pure technical/audit columns, cap at 15
    cols = get_columns_for_table(graph, fqn)
    business_cols = [
        c for c in cols
        if not (c.get("name", "").upper() in {"CREATED_BY", "UPDATED_BY",
                "CREATED_DATE", "UPDATED_DATE", "LAST_MODIFIED", "VERSION_NO"}
                and not c.get("is_fk"))
    ][:15]

    if business_cols:
        lines.append("  Columns (business-relevant):")
        for col in business_cols:
            col_name  = col.get("name", "")
            dtype     = col.get("data_type", "")
            comment   = col.get("comments", "") or ""
            is_fk     = col.get("is_fk", False)
            samples   = col.get("sample_values") or []
            tag = " [FK]" if is_fk else ""
            sample_str = f" — e.g. {samples[:3]}" if samples else ""
            note = f" — {comment}" if comment else ""
            lines.append(f"    {col_name} ({dtype}){tag}{sample_str}{note}")

    return "\n".join(lines)


# ── Main generator function ────────────────────────────────────────────────────

def generate_knowledge_file(
    graph,
    llm,
    output_path: str = DEFAULT_OUTPUT_PATH,
    max_tables: int = _DEFAULT_MAX_TABLES,
) -> bool:
    """
    Generate and write the business knowledge file.

    Selects the top ``max_tables`` tables by importance, makes batched LLM
    calls to produce business-purpose summaries and term mappings, then writes
    a structured text file to ``output_path``.

    Parameters
    ----------
    graph : KnowledgeGraph
        Populated knowledge graph (ideally after LLM enhancement).
    llm
        Any LangChain-compatible chat model.
    output_path : str
        Destination file path.  Written atomically via a .tmp file.
    max_tables : int
        Maximum number of tables to document.

    Returns
    -------
    bool
        True on success, False on any error.
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:
        logger.error("langchain_core is not available — cannot generate knowledge file")
        return False

    try:
        tables = _select_key_tables(graph, max_tables)
        if not tables:
            logger.warning("No tables in graph — cannot generate knowledge file")
            return False

        n = len(tables)
        logger.info("Knowledge generator: %d tables selected → %s", n, output_path)

        # ── Batch calls: table documentation ──────────────────────────────────
        table_section_parts: List[str] = []
        batches = [tables[i : i + _BATCH_SIZE] for i in range(0, n, _BATCH_SIZE)]

        for idx, batch in enumerate(batches):
            batch_block = "\n\n".join(_format_table_block(t, graph) for t in batch)
            logger.info(
                "Knowledge generator: LLM call %d/%d (%d tables)",
                idx + 1, len(batches), len(batch),
            )
            try:
                resp = llm.invoke([
                    SystemMessage(content=_TABLE_BATCH_SYSTEM),
                    HumanMessage(content=_TABLE_BATCH_HUMAN.format(
                        tables_section=batch_block
                    )),
                ])
                text = (
                    resp.content.strip()
                    if hasattr(resp, "content")
                    else str(resp).strip()
                )
                if text:
                    table_section_parts.append(text)
            except Exception as exc:
                logger.warning("Knowledge gen batch %d failed: %s", idx + 1, exc)

        # ── Single call: common query patterns ────────────────────────────────
        patterns_text = ""
        table_names_list = "\n".join(
            f"  {t.get('schema','')}.{t.get('name','')}"
            + (f"  ({t.get('importance_tier','')})" if t.get("importance_tier") else "")
            for t in tables[:15]
        )
        try:
            resp = llm.invoke([
                SystemMessage(content=_PATTERNS_SYSTEM),
                HumanMessage(content=_PATTERNS_HUMAN.format(
                    table_names=table_names_list
                )),
            ])
            patterns_text = (
                resp.content.strip()
                if hasattr(resp, "content")
                else str(resp).strip()
            )
        except Exception as exc:
            logger.warning("Knowledge gen patterns call failed: %s", exc)

        # ── Assemble the file ─────────────────────────────────────────────────
        schema_label = (
            os.getenv("ORACLE_TARGET_SCHEMAS", "DATABASE").replace(",", " / ").upper()
        )
        header = (
            "================================================================================\n"
            f"{schema_label} — BUSINESS KNOWLEDGE BASE (AUTO-GENERATED)\n"
            "================================================================================\n"
            f"Generated from live Oracle metadata. Covers the {n} most important tables\n"
            "selected by schema importance and FK connectivity.\n"
            "\n"
            "IMPORTANT: This file is NOT exhaustive. The database has many more tables.\n"
            "The SQL query generator has full access to the complete schema DDL.\n"
            "This file provides business-domain context (term mappings, join patterns,\n"
            "SQL conventions) for the most frequently queried tables only.\n"
            "\n"
            "Edit freely — auto-regeneration only happens when this file is empty.\n"
            "================================================================================"
        )

        parts = [header]

        if table_section_parts:
            divider = (
                "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "SECTION 1. KEY TABLE OVERVIEW (top tables by importance — not exhaustive)\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            )
            parts.append(divider + "\n\n".join(table_section_parts))

        if patterns_text:
            divider = (
                "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "SECTION 2. COMMON QUERY PATTERNS\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            )
            parts.append(divider + patterns_text)

        parts.append("\n\n" + _STATIC_SQL_DIRECTIVES)

        content = "\n".join(parts)

        # Atomic write
        tmp = output_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, output_path)

        logger.info("Knowledge file written: %s (%d chars)", output_path, len(content))
        return True

    except Exception as exc:
        logger.error("Knowledge file generation failed: %s", exc, exc_info=True)
        return False
