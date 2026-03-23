"""
SQL Validator Node
==================
Validates Oracle SQL syntax and checks for dangerous anti-patterns.

Validation checks:
  1. Syntax validation via sqlglot (Oracle dialect)
  2. Blocked DML/DDL keyword detection
  3. Dangerous built-ins / injection vectors
  4. Cartesian product detection (multiple tables in FROM without JOIN)
  5. Empty SQL guard

Sets state["validation_passed"] and state["validation_errors"].
"""

from __future__ import annotations

import logging
import re
from typing import Callable, List

from agent.state import AgentState

logger = logging.getLogger(__name__)

# Keywords that must never appear in a query submitted to the executor
_BLOCKED_KEYWORDS = frozenset(
    [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "EXECUTE",
        "EXEC",
        "UTL_FILE",
        "UTL_HTTP",
        "UTL_TCP",
        "UTL_SMTP",
        "DBMS_LOCK",
        "DBMS_SCHEDULER",
        "DBMS_JOB",
        "DBMS_OUTPUT",
        "GRANT",
        "REVOKE",
        "CREATE",
        "MERGE",
    ]
)

# Regex to detect standalone keywords (not inside string literals for basic check)
_BLOCKED_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in _BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Detect potential Cartesian product: FROM clause has comma-separated tables
# without explicit JOIN keyword following the comma
_CARTESIAN_PATTERN = re.compile(
    r"\bFROM\b\s+\w[\w.]*\s*,\s*\w[\w.]*",
    re.IGNORECASE,
)


def make_sql_validator() -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that validates Oracle SQL.

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def validate_sql(state: AgentState) -> AgentState:
        sql = state.get("generated_sql", "").strip()
        errors: List[str] = []

        # Guard: empty SQL
        if not sql:
            errors.append("Generated SQL is empty.")
            return {
                **state,
                "validation_passed": False,
                "validation_errors": errors,
                "step": "sql_validated",
            }

        # ------------------------------------------------------------------ #
        # 1. Blocked keyword check
        # ------------------------------------------------------------------ #
        match = _BLOCKED_PATTERN.search(sql)
        if match:
            errors.append(
                f"SQL contains blocked keyword '{match.group().upper()}'. "
                "Only SELECT statements are permitted."
            )

        # ------------------------------------------------------------------ #
        # 2. Must start with SELECT (after stripping comments/whitespace)
        # ------------------------------------------------------------------ #
        sql_stripped = re.sub(r"--[^\n]*", "", sql)  # remove single-line comments
        sql_stripped = re.sub(r"/\*[\s\S]*?\*/", "", sql_stripped)  # remove block comments
        first_token = sql_stripped.strip().split()[0].upper() if sql_stripped.strip() else ""
        if first_token and first_token != "SELECT":
            # Allow WITH (CTE) as starting keyword
            if first_token != "WITH":
                errors.append(
                    f"SQL must begin with SELECT (or WITH for CTEs). "
                    f"Found: '{first_token}'."
                )

        # ------------------------------------------------------------------ #
        # 3. Cartesian product detection
        # ------------------------------------------------------------------ #
        if _CARTESIAN_PATTERN.search(sql):
            errors.append(
                "Potential Cartesian product detected: multiple tables in FROM clause "
                "separated by commas without explicit JOIN. "
                "Use explicit JOIN ... ON syntax instead."
            )

        # ------------------------------------------------------------------ #
        # 4. sqlglot syntax validation
        # ------------------------------------------------------------------ #
        try:
            import sqlglot
            from sqlglot import ErrorLevel

            parse_errors = sqlglot.parse(sql, read="oracle", error_level=ErrorLevel.WARN)
            # sqlglot.parse raises on fatal errors; warnings come back as parse_errors
            # It returns a list of AST nodes; any None entries indicate parse failures
            if parse_errors and any(node is None for node in parse_errors):
                errors.append("SQL failed to parse in Oracle dialect (sqlglot).")

        except ImportError:
            logger.debug("sqlglot not installed; skipping syntax validation")
        except Exception as exc:
            # sqlglot can raise on truly invalid SQL
            errors.append(f"SQL syntax error (sqlglot): {exc}")

        # ------------------------------------------------------------------ #
        # 5. Basic structural checks
        # ------------------------------------------------------------------ #
        sql_upper = sql.upper()
        if "FROM" not in sql_upper:
            errors.append("SQL does not contain a FROM clause.")

        # ------------------------------------------------------------------ #
        # Result
        # ------------------------------------------------------------------ #
        validation_passed = len(errors) == 0
        if validation_passed:
            logger.info("SQL validation passed.")
        else:
            logger.warning("SQL validation failed: %s", errors)

        return {
            **state,
            "validation_passed": validation_passed,
            "validation_errors": errors,
            "step": "sql_validated",
        }

    return validate_sql
