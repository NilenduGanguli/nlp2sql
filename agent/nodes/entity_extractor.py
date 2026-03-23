"""
Entity Extractor Node
======================
Extracts structured business entities from the user's natural-language query
using an LLM with a structured JSON output prompt.

Extracted entities:
  tables       – likely Oracle table names mentioned (e.g. "customers", "transactions")
  columns      – specific column names mentioned
  conditions   – filter predicates (e.g. "risk_rating = 'HIGH'", "amount > 10000")
  time_range   – temporal reference (e.g. "last quarter", "2024", "last month")
  aggregations – aggregation functions needed (COUNT, SUM, AVG, MAX, MIN)
  sort_by      – ordering requirements (e.g. "by amount descending")
  limit        – result row limit if specified
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict

from agent.state import AgentState

logger = logging.getLogger(__name__)

# Known KYC tables for fallback entity resolution
_KYC_TABLES = [
    "CUSTOMERS",
    "ACCOUNTS",
    "TRANSACTIONS",
    "KYC_REVIEWS",
    "RISK_ASSESSMENTS",
    "BENEFICIAL_OWNERS",
    "EMPLOYEES",
    "PEP_STATUS",
]

_SYSTEM_PROMPT = """You are an entity extractor for a KYC (Know Your Customer) compliance Oracle database.

The database has these tables:
- CUSTOMERS: Core customer records with CUSTOMER_ID, FIRST_NAME, LAST_NAME, RISK_RATING, NATIONALITY, DATE_OF_BIRTH, ACCOUNT_MANAGER_ID, CREATED_DATE
- ACCOUNTS: Customer accounts with ACCOUNT_ID, CUSTOMER_ID, ACCOUNT_TYPE, BALANCE, CURRENCY, STATUS, OPENED_DATE
- TRANSACTIONS: Financial transactions with TRANSACTION_ID, ACCOUNT_ID, AMOUNT, CURRENCY, TRANSACTION_DATE, TRANSACTION_TYPE, IS_FLAGGED
- KYC_REVIEWS: KYC review records with REVIEW_ID, CUSTOMER_ID, REVIEW_DATE, REVIEWER_ID, STATUS, NEXT_REVIEW_DATE
- RISK_ASSESSMENTS: Risk scores with ASSESSMENT_ID, CUSTOMER_ID, RISK_SCORE, RISK_LEVEL, ASSESSED_DATE, ASSESSED_BY
- BENEFICIAL_OWNERS: UBO records with OWNER_ID, CUSTOMER_ID, OWNER_NAME, OWNERSHIP_PCT, RELATIONSHIP
- EMPLOYEES: Staff directory with EMPLOYEE_ID, FIRST_NAME, LAST_NAME, DEPARTMENT, ROLE, EMAIL
- PEP_STATUS: PEP flags with PEP_ID, CUSTOMER_ID, IS_PEP, PEP_TYPE, LISTED_DATE

Extract entities from the user query and respond ONLY with valid JSON:
{
  "tables": ["TABLE1", "TABLE2"],
  "columns": ["COL1", "COL2"],
  "conditions": ["risk_rating = 'HIGH'", "amount > 10000"],
  "time_range": "last quarter",
  "aggregations": ["COUNT", "SUM"],
  "sort_by": "amount DESC",
  "limit": null
}

Rules:
- tables: use UPPERCASE Oracle table names from the list above; infer from context
- columns: use UPPERCASE column names as they appear in the schema
- conditions: write Oracle SQL-style predicates where possible
- time_range: extract any temporal reference as a string, null if none
- aggregations: only include if aggregation is needed (COUNT, SUM, AVG, MAX, MIN)
- sort_by: null if no ordering is implied
- limit: integer if a specific row count is requested, null otherwise

Return ONLY the JSON object. No explanation."""


def make_entity_extractor(llm) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that extracts business entities.

    Parameters
    ----------
    llm : BaseChatModel
        A LangChain chat model instance.

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """

    def extract_entities(state: AgentState) -> AgentState:
        user_input = state.get("user_input", "")
        logger.debug("Extracting entities from: %r", user_input[:100])

        entities: Dict[str, Any] = {
            "tables": [],
            "columns": [],
            "conditions": [],
            "time_range": None,
            "aggregations": [],
            "sort_by": None,
            "limit": None,
        }

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=f"User query: {user_input}"),
            ]
            response = llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)

            # Extract JSON — handle markdown code blocks
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                parsed = json.loads(json_match.group())
                # Merge with defaults so all keys are always present
                entities.update({k: v for k, v in parsed.items() if v is not None})

                # Normalize table names to uppercase
                entities["tables"] = [
                    t.upper() for t in entities.get("tables", []) if isinstance(t, str)
                ]
                entities["columns"] = [
                    c.upper() for c in entities.get("columns", []) if isinstance(c, str)
                ]
                entities["aggregations"] = [
                    a.upper() for a in entities.get("aggregations", []) if isinstance(a, str)
                ]
            else:
                logger.warning(
                    "Entity extractor returned non-JSON: %r", content[:200]
                )
                # Fallback: simple keyword matching
                entities = _fallback_extract(user_input)

        except Exception as exc:
            logger.error("Entity extraction failed: %s", exc)
            entities = _fallback_extract(user_input)

        # Always ensure at least one table is populated
        if not entities.get("tables"):
            entities["tables"] = _fallback_extract(user_input).get("tables", ["CUSTOMERS"])

        logger.info(
            "Entities extracted: tables=%s, conditions=%d",
            entities.get("tables"),
            len(entities.get("conditions", [])),
        )

        return {**state, "entities": entities, "step": "entities_extracted"}

    return extract_entities


def _fallback_extract(user_input: str) -> Dict[str, Any]:
    """Simple keyword-based entity extraction when LLM fails."""
    text = user_input.upper()
    found_tables = [t for t in _KYC_TABLES if t in text or t.rstrip("S") in text]

    # Detect aggregations from common English phrases
    aggregations = []
    if any(kw in text for kw in ("HOW MANY", "COUNT", "TOTAL NUMBER", "NUMBER OF")):
        aggregations.append("COUNT")
    if any(kw in text for kw in ("SUM", "TOTAL AMOUNT", "TOTAL VALUE")):
        aggregations.append("SUM")
    if any(kw in text for kw in ("AVERAGE", "AVG", "MEAN")):
        aggregations.append("AVG")

    # Detect time references
    time_range = None
    time_kws = {
        "LAST MONTH": "last month",
        "LAST QUARTER": "last quarter",
        "LAST YEAR": "last year",
        "THIS YEAR": "this year",
        "THIS MONTH": "this month",
        "PAST YEAR": "past year",
        "PAST MONTH": "past month",
    }
    for kw, val in time_kws.items():
        if kw in text:
            time_range = val
            break

    # Detect conditions
    conditions = []
    if "HIGH RISK" in text or "HIGH-RISK" in text:
        conditions.append("RISK_RATING = 'HIGH'")
    if "VERY HIGH" in text:
        conditions.append("RISK_RATING = 'VERY_HIGH'")
    if "PEP" in text:
        conditions.append("IS_PEP = 'Y'")
    if "FLAGGED" in text:
        conditions.append("IS_FLAGGED = 'Y'")
    if "ACTIVE" in text:
        conditions.append("STATUS = 'ACTIVE'")
    if "FROZEN" in text:
        conditions.append("STATUS = 'FROZEN'")

    return {
        "tables": found_tables or ["CUSTOMERS"],
        "columns": [],
        "conditions": conditions,
        "time_range": time_range,
        "aggregations": aggregations,
        "sort_by": None,
        "limit": None,
    }
