"""
Query Enricher Node
====================
First node in the pipeline. Reads KYC business domain knowledge from
``kyc_business_knowledge.txt`` (or the path in env var KYC_KNOWLEDGE_FILE)
and uses an LLM to rewrite the user's query with precise domain context:

  - Business terms mapped to exact column names and values
    (e.g. "high risk" → RISK_RATING = 'HIGH')
  - Implied table joins identified
  - Business rules and constraints noted
  - Oracle-specific SQL conventions flagged

Purpose: act as a KYC subject-matter expert that pre-processes the query
so that the entity extractor, schema retriever, and SQL generator all work
from a richer, less ambiguous specification — reducing hallucinated column
names, wrong filter values, and missing JOINs.

If the knowledge file is missing or the LLM call fails the node passes
through unchanged (``enriched_query = user_input``).
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Knowledge file location
# --------------------------------------------------------------------------
# Default: <project-root>/kyc_business_knowledge.txt
# Override with env var KYC_KNOWLEDGE_FILE.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_DEFAULT_KNOWLEDGE_FILE = os.path.join(_PROJECT_ROOT, "kyc_business_knowledge.txt")


@functools.lru_cache(maxsize=4)
def _load_knowledge(path: str) -> str:
    """Load and cache the business knowledge file. Returns '' on any error."""
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read().strip()
        logger.info("Loaded business knowledge from %s (%d chars)", path, len(content))
        return content
    except FileNotFoundError:
        logger.warning("Business knowledge file not found: %s", path)
        return ""
    except Exception as exc:
        logger.warning("Cannot read business knowledge file %s: %s", path, exc)
        return ""


# --------------------------------------------------------------------------
# System prompt (built once per file path)
# --------------------------------------------------------------------------
_SYSTEM_TEMPLATE = """\
You are a senior KYC (Know Your Customer) compliance database expert with deep \
knowledge of AML regulations, customer risk classification, and the specific \
data model described below.

Your task is to interpret a user's natural-language query about the KYC database \
and rewrite it as a precise, grounded query specification that an SQL generator can \
use to produce a correct Oracle SQL statement.

Use the KNOWLEDGE BASE to:
  • Map vague business terms to exact column names and their allowed values
    (e.g. "high risk customers" → CUSTOMERS.RISK_RATING = 'HIGH')
  • Identify which tables are needed and what JOINs are implied
  • Surface any business rules or constraints the query must respect
  • Flag any Oracle-specific conventions (SYSDATE, FETCH FIRST N ROWS)

Do NOT write SQL. Write a structured English specification that preserves the
user's original intent and adds precision.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KNOWLEDGE BASE:
{knowledge}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

_HUMAN_TEMPLATE = """\
User query: {user_input}

Rewrite this as a precise query specification for the SQL generator. Structure your \
response as:

TABLES: <list the tables required>
FILTERS: <exact column = value conditions, using the knowledge base mappings>
JOINS: <join conditions implied by the query>
AGGREGATIONS: <any GROUP BY / COUNT / SUM needed>
CONSTRAINTS: <business rules that apply>
ENRICHED QUERY: <a single-paragraph summary of the full enriched query>

Keep the total response under 250 words. Start directly with TABLES:
"""


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

def make_query_enricher(
    llm,
    knowledge_file: str | None = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Return a pipeline node function that enriches ``state["user_input"]`` with
    KYC domain context and stores the result in ``state["enriched_query"]``.

    Parameters
    ----------
    llm:
        Any LangChain-compatible chat model.  If ``None``, the node is a
        transparent pass-through.
    knowledge_file:
        Path to the business knowledge text file.  Defaults to the
        ``KYC_KNOWLEDGE_FILE`` env var, then ``<project-root>/kyc_business_knowledge.txt``.
    """
    resolved_path = (
        knowledge_file
        or os.getenv("KYC_KNOWLEDGE_FILE")
        or _DEFAULT_KNOWLEDGE_FILE
    )

    # Load and format the system message once at factory creation time.
    # _load_knowledge is lru_cache'd, but the format() call is not — so we do
    # it here rather than on every query invocation.
    _knowledge = _load_knowledge(resolved_path)
    _system_content = _SYSTEM_TEMPLATE.format(knowledge=_knowledge) if _knowledge else ""

    def enrich_query(state: Dict[str, Any]) -> Dict[str, Any]:
        user_input: str = state.get("user_input", "")

        if not user_input.strip():
            return {**state, "enriched_query": user_input, "step": "query_enriched"}

        # Pass-through when no knowledge or no LLM
        if not (_system_content and llm):
            reason = "no knowledge file" if not _system_content else "no LLM"
            logger.debug("Query enricher: %s — using original query", reason)
            return {**state, "enriched_query": user_input, "step": "query_enriched"}

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            system_msg = SystemMessage(content=_system_content)
            human_msg = HumanMessage(
                content=_HUMAN_TEMPLATE.format(user_input=user_input)
            )

            response = llm.invoke([system_msg, human_msg])
            enriched: str = (
                response.content.strip()
                if hasattr(response, "content")
                else str(response).strip()
            )

            if not enriched:
                enriched = user_input

            logger.info(
                "Query enriched successfully (original=%d chars → enriched=%d chars)",
                len(user_input), len(enriched),
            )
            logger.debug("Enriched query:\n%s", enriched)

            return {**state, "enriched_query": enriched, "step": "query_enriched"}

        except Exception as exc:
            logger.warning(
                "Query enricher LLM call failed — using original query. Error: %s", exc
            )
            return {**state, "enriched_query": user_input, "step": "query_enriched"}

    return enrich_query
