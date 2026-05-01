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
  6. Column existence check (requires knowledge graph)

Sets state["validation_passed"] and state["validation_errors"].
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Dict, List, Optional

from agent.state import AgentState
from agent.trace import TraceStep

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


def _check_column_existence(sql: str, graph) -> List[str]:
    """
    Check that alias.column references in the SQL actually exist in the
    referenced tables according to the knowledge graph.

    Returns a list of error strings (empty = all OK).
    Only validates columns that are explicitly qualified with a table alias
    that maps to a schema-qualified table (e.g. ``c.CUSTOMER_ID`` where
    ``c`` is an alias for ``KYC.CUSTOMERS``).
    """
    if graph is None:
        return []
    try:
        import sqlglot
        import sqlglot.expressions as exp

        statement = sqlglot.parse_one(sql, read="oracle")
        if statement is None:
            return []

        # Collect CTE names so we don't try to look them up in the graph
        cte_names: set = set()
        for cte in statement.find_all(exp.CTE):
            if cte.alias:
                cte_names.add(cte.alias.upper())

        # Build alias → FQN from FROM / JOIN references that have a schema prefix
        alias_to_fqn: Dict[str, str] = {}
        for table_expr in statement.find_all(exp.Table):
            t_name = (table_expr.name or "").upper()
            if not t_name or t_name in cte_names:
                continue
            t_schema = (table_expr.db or "").upper()
            if not t_schema:
                continue  # no schema prefix → can't map to a graph FQN
            fqn = f"{t_schema}.{t_name}"
            # Register both the alias and the bare table name
            alias = (table_expr.alias or "").upper()
            alias_to_fqn[alias or t_name] = fqn
            if alias:
                alias_to_fqn[t_name] = fqn

        if not alias_to_fqn:
            return []

        # Lazily load column names per FQN
        fqn_col_cache: Dict[str, Optional[frozenset]] = {}

        def _get_col_names(fqn: str) -> Optional[frozenset]:
            if fqn not in fqn_col_cache:
                try:
                    from knowledge_graph.traversal import get_columns_for_table
                    cols = get_columns_for_table(graph, fqn)
                    fqn_col_cache[fqn] = (
                        frozenset(c["name"].upper() for c in cols) if cols else None
                    )
                except Exception:
                    fqn_col_cache[fqn] = None
            return fqn_col_cache[fqn]

        errors: List[str] = []
        for col_expr in statement.find_all(exp.Column):
            qualifier = (col_expr.table or "").upper()
            if not qualifier:
                continue  # unqualified column — can't validate
            fqn = alias_to_fqn.get(qualifier)
            if not fqn:
                continue  # alias not mapped to a graph table
            col_name = (col_expr.name or "").upper()
            if not col_name or col_name == "*":
                continue  # wildcard or empty — OK
            known_cols = _get_col_names(fqn)
            if known_cols is None:
                continue  # table not found in graph, skip
            if col_name not in known_cols:
                sample = sorted(known_cols)[:10]
                hint = ", ".join(sample)
                suffix = " (and more)" if len(known_cols) > 10 else ""
                errors.append(
                    f"Column '{col_name}' does not exist in {fqn}. "
                    f"Available columns: {hint}{suffix}. "
                    "Please correct the column name and regenerate the SQL."
                )

        return errors

    except ImportError:
        return []  # sqlglot not available
    except Exception as exc:
        logger.debug("Column existence check error: %s", exc)
        return []


def make_sql_validator(
    graph=None,
    value_cache=None,
    fuzzy_threshold: float = 0.85,
) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that validates Oracle SQL.

    Parameters
    ----------
    graph : KnowledgeGraph | None
        When provided, enables column-existence validation (check 6).
    value_cache : ValueCache | None
        When provided, enables literal-grounding check 7 — every WHERE/HAVING
        literal is compared against cached distinct values for that column.
        Confident fuzzy matches (case-insensitive equal, unique prefix, etc.)
        are silently rewritten in-place; ambiguous or unmatched literals
        emit ``[VALUE_HINT]`` errors that drive the existing retry loop.
    fuzzy_threshold : float
        Minimum score required for an auto-fix rewrite (default 0.85).

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def validate_sql(state: AgentState) -> AgentState:
        sql = state.get("generated_sql", "").strip()
        errors: List[str] = []
        _trace = list(state.get("_trace", []))
        trace = TraceStep("validate_sql", "validating")

        logger.debug("SQL validator: sql=%s", sql[:200])
        if not sql:
            errors.append("Generated SQL is empty.")
            trace.output_summary = {"validation_passed": False, "errors": errors}
            _trace.append(trace.finish().to_dict())
            return {
                **state,
                "validation_passed": False,
                "validation_errors": errors,
                "step": "sql_validated",
                "_trace": _trace,
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
        # 6. Column existence check (graph-powered, only when graph provided)
        # ------------------------------------------------------------------ #
        if not errors:  # skip if already failing — prevents redundant noise
            col_errors = _check_column_existence(sql, graph)
            errors.extend(col_errors)

        # ------------------------------------------------------------------ #
        # 7. Literal-grounding check (Phase 2 / Layer 3)
        #    Confident fuzzy matches → silently rewrite the SQL in-place.
        #    Ambiguous / unmatched literals → push [VALUE_HINT] error so
        #    the existing retry path regenerates with the explicit allowed
        #    value list.
        # ------------------------------------------------------------------ #
        value_mappings: List[Dict] = []
        if not errors and value_cache is not None:
            try:
                from agent.value_validator import (
                    apply_rewrites,
                    validate_where_literals,
                )
                findings, rewrites = validate_where_literals(
                    sql, value_cache, fuzzy_threshold=fuzzy_threshold,
                )
                if rewrites:
                    sql = apply_rewrites(sql, rewrites)
                    for rw in rewrites:
                        value_mappings.append({
                            "table": rw.table_fqn,
                            "column": rw.column,
                            "original": rw.original,
                            "mapped": rw.replacement,
                            "reason": rw.reason,
                        })
                    logger.info(
                        "Literal validator: auto-fixed %d literal(s)", len(rewrites),
                    )
                for f in findings:
                    allowed_str = ", ".join(f"'{v}'" for v in f.allowed_values)
                    errors.append(
                        f"[VALUE_HINT] Column {f.table_fqn}.{f.column} does not "
                        f"contain literal '{f.bad_literal}'. Allowed values: "
                        f"{allowed_str}. Map the user's intent to one or more of "
                        f"these and rewrite the WHERE clause."
                    )
            except Exception as exc:
                logger.debug("Literal validator skipped due to error: %s", exc)

        # ------------------------------------------------------------------ #
        # Result
        # ------------------------------------------------------------------ #
        validation_passed = len(errors) == 0
        if validation_passed:
            logger.info("SQL validation passed.")
        else:
            logger.warning("SQL validation failed: %s", errors)

        trace.output_summary = {
            "validation_passed": validation_passed,
            "errors": errors,
            "value_mappings": value_mappings,
        }
        _trace.append(trace.finish().to_dict())

        return {
            **state,
            "generated_sql": sql,                     # may have been rewritten
            "validation_passed": validation_passed,
            "validation_errors": errors,
            "value_mappings": value_mappings,
            "step": "sql_validated",
            "_trace": _trace,
        }

    return validate_sql
