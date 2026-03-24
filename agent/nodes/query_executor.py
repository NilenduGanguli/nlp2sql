"""
Query Executor Node
====================
Executes the optimized SQL against Oracle DB (or returns mock data in demo mode).

Execution modes:
  - demo_mode=True OR Oracle unreachable → mock executor (synthetic KYC data)
  - demo_mode=False AND Oracle reachable  → live oracledb execution

Mock data patterns:
  The mock executor inspects which tables appear in the SQL and returns
  5-10 plausible synthetic rows that match the KYC domain.

Result format:
  {
    "columns": [...],
    "rows": [[...], ...],
    "total_rows": N,
    "execution_time_ms": N,
    "source": "mock" | "oracle"
  }
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock data generators
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer",
    "Michael", "Linda", "William", "Barbara", "David", "Elizabeth",
    "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah",
    "Charles", "Karen",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
    "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez",
    "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore",
    "Jackson", "Martin",
]
_NATIONALITIES = ["USA", "GBR", "DEU", "FRA", "CHN", "JPN", "IND", "BRA", "CAN", "AUS"]
_RISK_RATINGS = ["LOW", "LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH", "VERY_HIGH"]
_ACCOUNT_TYPES = ["SAVINGS", "CURRENT", "INVESTMENT"]
_ACCOUNT_STATUSES = ["ACTIVE", "ACTIVE", "ACTIVE", "DORMANT", "CLOSED", "FROZEN"]
_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF"]
_TXN_TYPES = ["DEBIT", "CREDIT", "WIRE", "INTERNAL"]
_REVIEW_STATUSES = ["COMPLETED", "COMPLETED", "PENDING", "FAILED", "ESCALATED"]
_RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]
_DEPARTMENTS = ["Compliance", "Risk", "Operations", "IT", "Finance"]
_ROLES = ["Analyst", "Manager", "Senior Analyst", "Director", "Associate"]
_PEP_TYPES = ["HEAD_OF_STATE", "SENIOR_OFFICIAL", "JUDGE", "MILITARY"]
_RELATIONSHIPS = ["Director", "Shareholder", "Trustee", "Beneficiary", "Agent"]


def _rand_date(start_days_ago: int = 365, end_days_ago: int = 0) -> str:
    delta = random.randint(end_days_ago, start_days_ago)
    d = date.today() - timedelta(days=delta)
    return d.strftime("%Y-%m-%d")


def _rand_amount(min_val: float = 100.0, max_val: float = 500000.0) -> float:
    return round(random.uniform(min_val, max_val), 2)


def _mock_customers(n: int = 8) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "CUSTOMER_ID", "FIRST_NAME", "LAST_NAME", "DATE_OF_BIRTH",
        "NATIONALITY", "RISK_RATING", "ACCOUNT_MANAGER_ID", "CREATED_DATE",
    ]
    rows = []
    for i in range(1, n + 1):
        rows.append([
            i * 1000 + random.randint(1, 999),
            random.choice(_FIRST_NAMES),
            random.choice(_LAST_NAMES),
            _rand_date(start_days_ago=25000, end_days_ago=6000),
            random.choice(_NATIONALITIES),
            random.choice(_RISK_RATINGS),
            random.randint(1, 15),
            _rand_date(start_days_ago=1800, end_days_ago=30),
        ])
    return columns, rows


def _mock_accounts(n: int = 8) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "ACCOUNT_ID", "CUSTOMER_ID", "ACCOUNT_TYPE",
        "BALANCE", "CURRENCY", "STATUS", "OPENED_DATE",
    ]
    rows = []
    for i in range(1, n + 1):
        rows.append([
            i * 10000 + random.randint(1, 9999),
            i * 1000 + random.randint(1, 999),
            random.choice(_ACCOUNT_TYPES),
            _rand_amount(100.0, 250000.0),
            random.choice(_CURRENCIES),
            random.choice(_ACCOUNT_STATUSES),
            _rand_date(start_days_ago=1800, end_days_ago=30),
        ])
    return columns, rows


def _mock_transactions(n: int = 10) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "TRANSACTION_ID", "ACCOUNT_ID", "AMOUNT", "CURRENCY",
        "TRANSACTION_DATE", "TRANSACTION_TYPE", "IS_FLAGGED",
    ]
    rows = []
    for i in range(1, n + 1):
        amount = _rand_amount(50.0, 150000.0)
        rows.append([
            i * 100000 + random.randint(1, 99999),
            random.randint(10001, 90000),
            amount,
            random.choice(_CURRENCIES),
            _rand_date(start_days_ago=90, end_days_ago=0),
            random.choice(_TXN_TYPES),
            "Y" if amount > 10000 and random.random() < 0.3 else "N",
        ])
    return columns, rows


def _mock_kyc_reviews(n: int = 8) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "REVIEW_ID", "CUSTOMER_ID", "REVIEW_DATE",
        "REVIEWER_ID", "STATUS", "NEXT_REVIEW_DATE",
    ]
    rows = []
    for i in range(1, n + 1):
        review_date = _rand_date(start_days_ago=730, end_days_ago=30)
        rows.append([
            i * 5000 + random.randint(1, 4999),
            i * 1000 + random.randint(1, 999),
            review_date,
            random.randint(1, 15),
            random.choice(_REVIEW_STATUSES),
            _rand_date(start_days_ago=0, end_days_ago=-365),  # future date
        ])
    return columns, rows


def _mock_risk_assessments(n: int = 8) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "ASSESSMENT_ID", "CUSTOMER_ID", "RISK_SCORE",
        "RISK_LEVEL", "ASSESSED_DATE", "ASSESSED_BY",
    ]
    rows = []
    for i in range(1, n + 1):
        risk_level = random.choice(_RISK_LEVELS)
        score_map = {"LOW": (10, 30), "MEDIUM": (31, 60), "HIGH": (61, 80), "VERY_HIGH": (81, 100)}
        lo, hi = score_map[risk_level]
        rows.append([
            i * 3000 + random.randint(1, 2999),
            i * 1000 + random.randint(1, 999),
            round(random.uniform(lo, hi), 2),
            risk_level,
            _rand_date(start_days_ago=180, end_days_ago=1),
            random.randint(1, 15),
        ])
    return columns, rows


def _mock_beneficial_owners(n: int = 8) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "OWNER_ID", "CUSTOMER_ID", "OWNER_NAME", "OWNERSHIP_PCT", "RELATIONSHIP",
    ]
    rows = []
    for i in range(1, n + 1):
        fname = random.choice(_FIRST_NAMES)
        lname = random.choice(_LAST_NAMES)
        rows.append([
            i * 2000 + random.randint(1, 1999),
            i * 1000 + random.randint(1, 999),
            f"{fname} {lname}",
            round(random.uniform(10.0, 100.0), 2),
            random.choice(_RELATIONSHIPS),
        ])
    return columns, rows


def _mock_employees(n: int = 8) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "EMPLOYEE_ID", "FIRST_NAME", "LAST_NAME",
        "DEPARTMENT", "ROLE", "EMAIL",
    ]
    rows = []
    for i in range(1, n + 1):
        fname = random.choice(_FIRST_NAMES)
        lname = random.choice(_LAST_NAMES)
        dept = random.choice(_DEPARTMENTS)
        role = random.choice(_ROLES)
        rows.append([
            i,
            fname,
            lname,
            dept,
            role,
            f"{fname.lower()}.{lname.lower()}@kyc-bank.com",
        ])
    return columns, rows


def _mock_pep_status(n: int = 5) -> Tuple[List[str], List[List[Any]]]:
    columns = [
        "PEP_ID", "CUSTOMER_ID", "IS_PEP", "PEP_TYPE", "LISTED_DATE",
    ]
    rows = []
    for i in range(1, n + 1):
        rows.append([
            i * 100 + random.randint(1, 99),
            i * 1000 + random.randint(1, 999),
            "Y",
            random.choice(_PEP_TYPES),
            _rand_date(start_days_ago=2000, end_days_ago=30),
        ])
    return columns, rows


# Table → mock generator function mapping
_TABLE_MOCK_MAP = {
    "CUSTOMERS": _mock_customers,
    "ACCOUNTS": _mock_accounts,
    "TRANSACTIONS": _mock_transactions,
    "KYC_REVIEWS": _mock_kyc_reviews,
    "RISK_ASSESSMENTS": _mock_risk_assessments,
    "BENEFICIAL_OWNERS": _mock_beneficial_owners,
    "EMPLOYEES": _mock_employees,
    "PEP_STATUS": _mock_pep_status,
}


def _detect_tables_in_sql(sql: str) -> List[str]:
    """Extract table names referenced in a SQL string (simple regex approach)."""
    # Match schema.table or bare table names after FROM/JOIN keywords
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+(?:KYC\.)?(\w+)",
        re.IGNORECASE,
    )
    found = []
    for m in pattern.finditer(sql):
        name = m.group(1).upper()
        if name in _TABLE_MOCK_MAP:
            if name not in found:
                found.append(name)
    return found


def _parse_select_columns(sql: str) -> Optional[List[str]]:
    """
    Very lightly parse the SELECT column list.
    Returns None if SELECT * or too complex to parse.
    """
    # Match SELECT ... FROM
    m = re.search(r"\bSELECT\b\s+([\s\S]+?)\s+\bFROM\b", sql, re.IGNORECASE)
    if not m:
        return None
    col_text = m.group(1).strip()
    if "*" in col_text:
        return None
    # Split on commas (naively — doesn't handle nested parens)
    cols = [c.strip() for c in col_text.split(",")]
    # Extract alias or last identifier
    result = []
    for col in cols:
        # Handle "expr AS alias" or "table.col"
        alias_match = re.search(r"\bAS\s+(\w+)$", col, re.IGNORECASE)
        if alias_match:
            result.append(alias_match.group(1).upper())
        else:
            # Take last word (may be "t.COLUMN_NAME" → "COLUMN_NAME")
            last_word = col.rstrip().split()[-1] if col.strip() else col
            result.append(last_word.split(".")[-1].upper())
    return result if result else None


def _is_count_query(sql: str) -> bool:
    """Detect COUNT(*) / COUNT(1) style aggregate queries."""
    return bool(re.search(r"\bCOUNT\s*\(", sql, re.IGNORECASE))


def _mock_execute(sql: str) -> Dict[str, Any]:
    """
    Generate plausible synthetic data for the given SQL.
    Inspects which tables appear in the SQL and merges their mock rows.
    """
    start_ms = time.time()
    random.seed(42)  # deterministic output for demo

    tables = _detect_tables_in_sql(sql)
    if not tables:
        tables = ["CUSTOMERS"]

    # COUNT query → return single aggregate row
    if _is_count_query(sql):
        count_val = random.randint(50, 500)
        elapsed = int((time.time() - start_ms) * 1000)
        return {
            "columns": ["COUNT(*)"],
            "rows": [[count_val]],
            "total_rows": 1,
            "execution_time_ms": elapsed,
            "source": "mock",
        }

    # Use the first matched table as primary
    primary_table = tables[0]
    generator = _TABLE_MOCK_MAP.get(primary_table, _mock_customers)
    columns, rows = generator(n=random.randint(5, 10))

    # Try to match explicit SELECT columns from SQL
    requested_cols = _parse_select_columns(sql)
    if requested_cols:
        # Filter columns to those matching the request (fuzzy — just match names)
        col_upper = [c.upper() for c in columns]
        matched_indices = []
        matched_cols = []
        for rc in requested_cols:
            rc_bare = rc.split(".")[-1]
            if rc_bare in col_upper:
                idx = col_upper.index(rc_bare)
                matched_indices.append(idx)
                matched_cols.append(columns[idx])
        if matched_indices:
            columns = matched_cols
            rows = [[row[i] for i in matched_indices] for row in rows]

    elapsed = int((time.time() - start_ms) * 1000) + random.randint(10, 200)
    return {
        "columns": columns,
        "rows": rows,
        "total_rows": len(rows),
        "execution_time_ms": elapsed,
        "source": "mock",
    }


# ---------------------------------------------------------------------------
# Live Oracle executor
# ---------------------------------------------------------------------------

def _oracle_execute(sql: str, config) -> Dict[str, Any]:
    """Execute SQL against a real Oracle database via oracledb."""
    try:
        import oracledb
    except ImportError:
        raise ImportError(
            "python-oracledb is required for live Oracle execution. "
            "Install it with: pip install python-oracledb"
        )

    start_ms = time.time()
    if config.oracle.thick_mode and oracledb.is_thin_mode():
        oracledb.init_oracle_client(lib_dir=config.oracle.oracle_lib_dir or None)
    conn = oracledb.connect(
        user=config.oracle.user,
        password=config.oracle.password,
        dsn=config.oracle.dsn,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        col_names = [d[0] for d in cursor.description]
        max_rows = config.max_result_rows or 10000
        raw_rows = cursor.fetchmany(max_rows)
        rows = [list(r) for r in raw_rows]
        cursor.close()
    finally:
        conn.close()

    elapsed = int((time.time() - start_ms) * 1000)
    return {
        "columns": col_names,
        "rows": rows,
        "total_rows": len(rows),
        "execution_time_ms": elapsed,
        "source": "oracle",
    }


# ---------------------------------------------------------------------------
# Node factory
# ---------------------------------------------------------------------------

def make_query_executor(config) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that executes the optimized SQL.

    Parameters
    ----------
    config : AppConfig
        Application configuration (demo_mode, oracle credentials, row limits).

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def execute_query(state: AgentState) -> AgentState:
        sql = state.get("optimized_sql", "") or state.get("generated_sql", "")

        if not sql:
            return {
                **state,
                "execution_result": {
                    "columns": [],
                    "rows": [],
                    "total_rows": 0,
                    "execution_time_ms": 0,
                    "source": "none",
                },
                "error": "No SQL to execute.",
                "step": "query_executed",
            }

        # Determine execution mode
        use_mock = getattr(config, "demo_mode", True)

        if not use_mock:
            # Try real Oracle; fall back to mock on any error
            try:
                result = _oracle_execute(sql, config)
                logger.info(
                    "Oracle execution: %d rows in %dms",
                    result["total_rows"],
                    result["execution_time_ms"],
                )
            except Exception as exc:
                logger.warning(
                    "Oracle execution failed (%s); falling back to mock data", exc
                )
                result = _mock_execute(sql)
                result["error"] = str(exc)
        else:
            result = _mock_execute(sql)
            logger.info(
                "Mock execution: %d rows in %dms",
                result["total_rows"],
                result["execution_time_ms"],
            )

        return {
            **state,
            "execution_result": result,
            "step": "query_executed",
        }

    return execute_query
