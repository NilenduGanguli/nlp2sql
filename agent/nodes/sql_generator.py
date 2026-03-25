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

from agent.state import AgentState

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
```"""


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

    def generate_sql(state: AgentState) -> AgentState:
        user_input = state.get("user_input", "")
        schema_context = state.get("schema_context", "")
        conversation_history = state.get("conversation_history", [])
        validation_errors = state.get("validation_errors", [])
        retry_count = state.get("retry_count", 0)

        logger.debug(
            "SQL generation attempt %d for: %r", retry_count + 1, user_input[:80]
        )

        # Build conversation context (last 2 turns)
        history_text = ""
        if conversation_history:
            recent = conversation_history[-2:]
            history_lines = []
            for turn in recent:
                role = turn.get("role", "user")
                content = turn.get("content", "")[:300]
                history_lines.append(f"{role.upper()}: {content}")
            history_text = "\n".join(history_lines)

        # Build user message
        user_msg_parts = [
            f"Schema (DDL context):\n{schema_context}",
            f"\nQuestion: {user_input}",
        ]
        if history_text:
            user_msg_parts.append(f"\nConversation context:\n{history_text}")
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
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ]
            response = llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)

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

        except Exception as exc:
            logger.error("SQL generation failed: %s", exc)
            generated_sql = _build_fallback_sql(state)
            sql_explanation = f"Auto-generated fallback query (LLM error: {exc})"

        return {
            **state,
            "generated_sql": generated_sql,
            "sql_explanation": sql_explanation,
            "retry_count": retry_count + (1 if retry_count > 0 else 0),
            "step": "sql_generated",
        }

    return generate_sql


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

    # Try to match FQN from DDL header lines: "-- TABLE: SCHEMA.TABLE_NAME"
    if schema_context:
        for line in schema_context.splitlines():
            m = re.match(r"--\s*TABLE:\s*(\w+\.\w+)", line.strip(), re.IGNORECASE)
            if m:
                fqn = m.group(1)
                # Prefer a FQN whose table portion matches the extracted entity hint
                if not hint_name or hint_name in fqn.upper():
                    return f"SELECT * FROM {fqn} FETCH FIRST 100 ROWS ONLY"

    # If we have a hint name but no context match, use it unqualified
    if hint_name:
        return f"SELECT * FROM {hint_name} FETCH FIRST 100 ROWS ONLY"

    return "SELECT 'No table resolved' AS error FROM DUAL"
