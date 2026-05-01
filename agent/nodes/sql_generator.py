"""
SQL Generator Node
===================
Generates Oracle SQL using an LLM with chain-of-thought reasoning.

Features:
  - Oracle-specific SQL syntax rules enforced via system prompt
  - Chain-of-thought: the model explains its reasoning before writing SQL
  - Retry-aware: includes previous validation errors on retry attempts
  - Parses SQL from ```sql ... ``` blocks and explanation from ```explanation ... ``` blocks
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from agent.prompts import load_prompt
from agent.state import AgentState
from agent.trace import TraceStep

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert Oracle SQL developer. You write SELECT-only Oracle SQL queries based on the schema DDL provided in each question.

ORACLE SQL RULES — follow these strictly:
1. Use Oracle-specific syntax: NVL() not ISNULL(), FETCH FIRST N ROWS ONLY not LIMIT, TO_DATE/TO_CHAR for dates
2. Always qualify column names with table aliases (e.g. c.CUSTOMER_ID not just CUSTOMER_ID)
3. Use proper JOIN syntax — always include explicit ON conditions, never CROSS JOIN accidentally
4. For date arithmetic: ADD_MONTHS(), MONTHS_BETWEEN(), TRUNC(SYSDATE, 'Q') for quarters, TRUNC(SYSDATE, 'MM') for months
5. For pagination: FETCH FIRST N ROWS ONLY (Oracle 12c+) or ROWNUM for older style
6. For conditional logic: NVL2(), DECODE(), CASE WHEN ... THEN ... ELSE ... END
7. For hierarchical data: CONNECT BY PRIOR (if needed)
8. Always cast types explicitly where mixing types: TO_NUMBER(), TO_CHAR(), CAST()
9. Use HAVING for group-level filters, WHERE for row-level filters
10. Never write DML (INSERT/UPDATE/DELETE) or DDL (CREATE/DROP/ALTER/TRUNCATE)
11. Use INNER JOIN when referencing FK relationships; LEFT JOIN when the child may not exist
12. For "last month": TRUNC(SYSDATE, 'MM') - INTERVAL '1' MONTH and TRUNC(SYSDATE, 'MM')
13. For "last quarter": TRUNC(SYSDATE, 'Q') - INTERVAL '3' MONTH and TRUNC(SYSDATE, 'Q')
14. For "last year": TRUNC(SYSDATE, 'YYYY') - INTERVAL '1' YEAR and TRUNC(SYSDATE, 'YYYY')
15. Use the EXACT fully-qualified table names (SCHEMA.TABLE_NAME) as they appear in the DDL context provided. Do not invent or assume schema names — read them from the "-- TABLE: SCHEMA.TABLE" headers in the DDL.
16. When the question spans multiple tables, use all the FK and JOIN PATH hints in the DDL to determine the correct join columns.
17. VALUE GROUNDING — read the column annotations carefully.
    When a column has a `-- Values(N): 'A', 'B', ...` annotation in the DDL,
    that annotation is the EXACT, COMPLETE list of values stored in the
    database for that column. You MUST:
    - Use one of the listed values verbatim (case-sensitive) when filtering
      that column. Never invent, translate, or normalize the value.
      Example: if the DDL says `-- Values(3): 'A', 'I', 'P'` for STATUS,
      write `WHERE c.STATUS = 'A'` — never `'ACTIVE'`.
    - If the user's intent maps to multiple listed values, use IN (...) with
      all matching values.
    - If the user's intent does not obviously match any listed value, prefer
      to flag the ambiguity rather than guess.
18. UNANNOTATED COLUMNS — when a column has no `-- Values(...)` annotation,
    it is either high-cardinality (names, IDs, free text) or was not flagged
    as filter-relevant. Do NOT assume any specific literal. Use the user's
    quoted string verbatim, or use LIKE for partial matches, but flag this
    case in the ambiguity block.
19. CASE SENSITIVITY — Oracle string comparisons are case-sensitive by
    default. Always preserve the exact case from the `-- Values(...)`
    annotation. If the listed values are upper-case, write upper-case in
    the WHERE clause.

OUTPUT FORMAT — you MUST use exactly these code fences:
First, briefly reason through: which tables are needed, which joins to use (refer to FK hints), which conditions, which aggregations.
Then:

```sql
SELECT ...
FROM SCHEMA.TABLE_NAME alias
JOIN SCHEMA.OTHER_TABLE alias2 ON alias.FK_COL = alias2.PK_COL
...
```

```explanation
One or two sentences explaining what the query does in business terms.
```

AMBIGUITY DETECTION:
When the user's question admits more than one reasonable interpretation — different join paths, different aggregation strategies, different filter scopes, different fact tables, or different time-range conventions — you MUST enumerate up to 5 interpretations after the SQL block. Be aggressive about flagging ambiguity: if a competent analyst could reasonably read the question two different ways, list both. Output:

```ambiguity
- Interpretation 1: brief description (one short sentence)
- Interpretation 2: brief description
- Interpretation 3: brief description (only if it adds a genuinely distinct reading)
- Interpretation 4: brief description (only if needed)
- Interpretation 5: brief description (only if needed)
```

Hard limit: 5 interpretations. Skip the ambiguity block entirely only when the question has exactly one clear reading."""


def make_sql_generator(llm) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that generates Oracle SQL.

    Parameters
    ----------
    llm : BaseChatModel
        A LangChain chat model instance.

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """
    # Load system prompt from file (refreshed on each pipeline build)
    system_prompt = load_prompt("sql_generator_system", default=_SYSTEM_PROMPT)

    def generate_sql(state: AgentState) -> AgentState:
        # Prefer enriched query (domain-augmented by query_enricher node)
        user_input = state.get("enriched_query") or state.get("user_input", "")
        schema_context = state.get("schema_context", "")
        conversation_history = state.get("conversation_history", [])
        validation_errors = state.get("validation_errors", [])
        retry_count = state.get("retry_count", 0)
        intent = state.get("intent", "DATA_QUERY")
        _trace = list(state.get("_trace", []))
        trace = TraceStep("generate_sql", "generating")

        logger.debug(
            "SQL generation attempt %d for: %r (intent=%s)", retry_count + 1, user_input[:80], intent
        )

        is_followup = intent == "RESULT_FOLLOWUP"
        is_refine = intent == "QUERY_REFINE"
        prev_sql_for_mode = (state.get("previous_sql_context") or {}).get("sql", "")
        refinement_mode = bool(prev_sql_for_mode) and (is_followup or is_refine)

        # Build conversation context
        # RESULT_FOLLOWUP and QUERY_REFINE get more turns so the LLM sees prior SQL clearly
        history_turns = 10 if (is_followup or is_refine) else 4
        history_text = ""
        all_prev_sqls: list[dict[str, object]] = []
        if conversation_history:
            recent = conversation_history[-history_turns:]
            history_lines = []
            for turn in recent:
                role = turn.get("role", "user")
                content = turn.get("content", "")[:400]
                history_lines.append(f"{role.upper()}: {content}")
            history_text = "\n".join(history_lines)

            # Extract ALL previous SQLs from history (most-recent first)
            for turn in reversed(conversation_history):
                if turn.get("role") != "assistant":
                    continue
                sql_entry: dict[str, object] = {}
                raw_content = turn.get("content", "")
                # Try JSON-structured response
                try:
                    import json as _json
                    parsed = _json.loads(raw_content)
                    if isinstance(parsed, dict) and parsed.get("sql"):
                        sql_entry = {
                            "sql": parsed["sql"],
                            "explanation": parsed.get("explanation", ""),
                            "columns": parsed.get("columns", []),
                            "total_rows": parsed.get("total_rows"),
                        }
                except Exception:
                    pass
                # Regex fallback
                if not sql_entry:
                    m = re.search(
                        r'"sql"\s*:\s*"(SELECT[^"]+)"',
                        raw_content,
                        re.IGNORECASE,
                    )
                    if m:
                        sql_entry = {"sql": m.group(1), "explanation": "", "columns": [], "total_rows": None}
                if sql_entry:
                    all_prev_sqls.append(sql_entry)

        # Build user message
        user_msg_parts = [
            f"Schema (DDL context):\n{schema_context}",
            f"\nQuestion: {user_input}",
        ]
        if history_text:
            user_msg_parts.append(f"\nConversation context:\n{history_text}")

        # --- Previous SQL context for follow-ups and refinements ---
        # Prefer structured state context (richer than history-extracted)
        prev_ctx: dict = state.get("previous_sql_context", {}) or {}
        if prev_ctx.get("sql") and (is_followup or is_refine):
            context_parts = [
                "\n--- PREVIOUS QUERY CONTEXT ---",
                "The user is referencing this prior query:",
                f"```sql\n{prev_ctx['sql']}\n```",
            ]
            if prev_ctx.get("explanation"):
                context_parts.append(f"Explanation: {prev_ctx['explanation']}")
            if prev_ctx.get("columns"):
                cols = prev_ctx["columns"]
                if isinstance(cols, list):
                    context_parts.append(f"Returned columns: {', '.join(str(c) for c in cols[:20])}")
            if prev_ctx.get("total_rows") is not None:
                context_parts.append(f"Row count: {prev_ctx['total_rows']}")
            context_parts.append(
                "\nThe user's new request should MODIFY or BUILD UPON this query. "
                "Use the same tables, aliases, and joins as a starting point."
            )
            user_msg_parts.append("\n".join(context_parts))
        elif (is_followup or is_refine) and all_prev_sqls:
            # Fall back to history-extracted SQL chain
            most_recent = all_prev_sqls[0]
            context_parts = [
                "\n--- PREVIOUS QUERY CONTEXT ---",
                "The user is referencing this prior query:",
                f"```sql\n{most_recent['sql']}\n```",
            ]
            if most_recent.get("explanation"):
                context_parts.append(f"Explanation: {most_recent['explanation']}")
            if most_recent.get("columns"):
                cols = most_recent["columns"]
                if isinstance(cols, list) and cols:
                    context_parts.append(f"Returned columns: {', '.join(str(c) for c in cols[:20])}")
            if most_recent.get("total_rows") is not None:
                context_parts.append(f"Row count: {most_recent['total_rows']}")
            context_parts.append(
                "\nThe user's new request should MODIFY or BUILD UPON this query. "
                "Use the same tables, aliases, and joins as a starting point."
            )
            user_msg_parts.append("\n".join(context_parts))

        if retry_count > 0 and validation_errors:
            error_list = "\n".join(f"  - {e}" for e in validation_errors)
            user_msg_parts.append(
                f"\nPrevious SQL had these errors — please fix them:\n{error_list}"
            )

        user_message = "\n".join(user_msg_parts)

        generated_sql = ""
        sql_explanation = ""

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_message),
            ]
            response = llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)

            logger.debug("SQL generator LLM raw response:\n%s", content)

            # Extract SQL block
            sql_match = re.search(r"```sql\s*([\s\S]*?)```", content, re.IGNORECASE)
            if sql_match:
                generated_sql = sql_match.group(1).strip()
            else:
                # Try to find any SQL-like content (fallback)
                sql_match_fb = re.search(
                    r"(SELECT\s+[\s\S]+?(?:FETCH\s+FIRST|WHERE|FROM|ORDER|GROUP|HAVING|;|$))",
                    content,
                    re.IGNORECASE,
                )
                if sql_match_fb:
                    generated_sql = sql_match_fb.group(1).strip()
                else:
                    logger.warning(
                        "Could not extract SQL from LLM response: %r", content[:300]
                    )
                    generated_sql = _build_fallback_sql(state)

            # Extract explanation block
            exp_match = re.search(
                r"```explanation\s*([\s\S]*?)```", content, re.IGNORECASE
            )
            if exp_match:
                sql_explanation = exp_match.group(1).strip()
            else:
                # Use everything before the sql block as reasoning, or a default
                sql_explanation = "This query retrieves data based on your question."

            logger.info(
                "SQL generated (%d chars), explanation=%d chars",
                len(generated_sql),
                len(sql_explanation),
            )

            trace.set_llm_call(system_prompt, user_message, content, {"sql": generated_sql, "explanation": sql_explanation})

            # --- Ambiguity detection: check for ```ambiguity block ---
            ambiguity_match = re.search(r"```ambiguity\s*([\s\S]*?)```", content, re.IGNORECASE)
            if ambiguity_match:
                interpretations = _parse_ambiguity_block(ambiguity_match.group(1))
                if len(interpretations) >= 2:
                    logger.info("Ambiguity detected: %d interpretations", len(interpretations))
                    candidates = _generate_multi_candidates(
                        llm, system_prompt, schema_context, user_input,
                        interpretations, generated_sql, sql_explanation, trace,
                    )
                    if candidates:
                        trace.output_summary = {
                            "sql_length": len(generated_sql),
                            "sql_preview": generated_sql[:300],
                            "retry_count": retry_count,
                            "candidates": len(candidates),
                            "refinement_mode": refinement_mode,
                        }
                        _trace.append(trace.finish().to_dict())
                        return {
                            **state,
                            "generated_sql": candidates[0]["sql"],
                            "sql_explanation": candidates[0]["explanation"],
                            "sql_candidates": candidates,
                            "has_candidates": True,
                            "retry_count": retry_count + (1 if retry_count > 0 else 0),
                            "step": "sql_generated",
                            "_trace": _trace,
                        }

        except Exception as exc:
            logger.error("SQL generation failed: %s", exc)
            generated_sql = _build_fallback_sql(state)
            sql_explanation = f"Auto-generated fallback query (LLM error: {exc})"
            trace.error = str(exc)
            trace.set_llm_call(system_prompt, user_message, "", {"sql": generated_sql, "explanation": sql_explanation})

        trace.output_summary = {
            "sql_length": len(generated_sql),
            "sql_preview": generated_sql[:300],
            "retry_count": retry_count,
            "refinement_mode": refinement_mode,
        }
        _trace.append(trace.finish().to_dict())

        return {
            **state,
            "generated_sql": generated_sql,
            "sql_explanation": sql_explanation,
            "retry_count": retry_count + (1 if retry_count > 0 else 0),
            "step": "sql_generated",
            "_trace": _trace,
        }

    return generate_sql


def _extract_fqn_from_context(schema_context: str, hint_name: str = "") -> str:
    """
    Extract the fully-qualified table name from DDL context headers.

    Scans for ``-- TABLE: SCHEMA.TABLE_NAME`` lines in the DDL.
    Prefers a match whose table portion contains ``hint_name``;
    falls back to the first header found if no hint match is found.

    Returns an empty string when nothing is found.
    """
    hint_upper = hint_name.upper()
    first_fqn = ""
    for line in schema_context.splitlines():
        m = re.match(r"--\s*TABLE:\s*(\w+\.\w+)", line.strip(), re.IGNORECASE)
        if m:
            candidate = m.group(1)
            if not first_fqn:
                first_fqn = candidate
            if not hint_upper or hint_upper in candidate.upper():
                return candidate
    return first_fqn  # fallback: first header found regardless of hint


def _build_fallback_sql(state: AgentState) -> str:
    """
    Build a minimal valid Oracle SQL when the LLM fails.

    Extracts the fully-qualified table name from the DDL context (preferable)
    so we use the correct schema prefix, not a hardcoded one.
    """
    entities = state.get("entities", {})
    schema_context = state.get("schema_context", "")
    tables = entities.get("tables", [])
    hint_name = (tables[0] if tables else "").upper()

    fqn = _extract_fqn_from_context(schema_context, hint_name)
    if fqn:
        return f"SELECT * FROM {fqn} FETCH FIRST 100 ROWS ONLY"

    if hint_name:
        return f"SELECT * FROM {hint_name} FETCH FIRST 100 ROWS ONLY"

    return "SELECT 'No table resolved' AS error FROM DUAL"


def _parse_ambiguity_block(text: str) -> list[str]:
    """Parse interpretations from the ambiguity block."""
    interpretations = []
    for line in text.strip().splitlines():
        line = line.strip().lstrip("-").strip()
        # Remove "Interpretation N:" prefix
        line = re.sub(r"^Interpretation\s+\d+\s*:\s*", "", line, flags=re.IGNORECASE).strip()
        if line:
            interpretations.append(line)
    return interpretations[:5]  # cap at 5


def _generate_multi_candidates(
    llm,
    system_prompt: str,
    schema_context: str,
    user_input: str,
    interpretations: list[str],
    first_sql: str,
    first_explanation: str,
    trace: TraceStep,
) -> list[dict]:
    """Generate a SQL candidate for each interpretation."""
    from langchain_core.messages import HumanMessage, SystemMessage
    import uuid

    candidates = [{
        "id": str(uuid.uuid4())[:8],
        "interpretation": interpretations[0] if interpretations else "Primary interpretation",
        "sql": first_sql,
        "explanation": first_explanation,
    }]

    for interp in interpretations[1:]:
        try:
            msg = (
                f"Schema (DDL context):\n{schema_context}\n\n"
                f"Question: {user_input}\n\n"
                f"Generate SQL for THIS SPECIFIC interpretation:\n{interp}\n\n"
                f"Output the SQL in ```sql ... ``` and explanation in ```explanation ... ``` blocks. "
                f"Do NOT include an ambiguity block."
            )
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=msg),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            sql_match = re.search(r"```sql\s*([\s\S]*?)```", content, re.IGNORECASE)
            exp_match = re.search(r"```explanation\s*([\s\S]*?)```", content, re.IGNORECASE)

            if sql_match:
                candidates.append({
                    "id": str(uuid.uuid4())[:8],
                    "interpretation": interp,
                    "sql": sql_match.group(1).strip(),
                    "explanation": exp_match.group(1).strip() if exp_match else interp,
                })
        except Exception as exc:
            logger.warning("Failed to generate candidate for '%s': %s", interp[:50], exc)

    return candidates if len(candidates) >= 2 else []
