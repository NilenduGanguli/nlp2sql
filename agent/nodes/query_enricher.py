"""
Query Enricher Node
====================
First node in the pipeline. Reads business domain knowledge from
``kyc_business_knowledge.txt`` (or the path in env var KYC_KNOWLEDGE_FILE)
and uses an LLM to rewrite the user's query with precise domain context:

  - Business terms mapped to exact column names and values
    (e.g. "high risk" → RISK_RATING = 'HIGH')
  - Implied table joins identified
  - Business rules and constraints noted
  - Oracle-specific SQL conventions flagged

Purpose: act as a domain subject-matter expert that pre-processes the query
so that the entity extractor, schema retriever, and SQL generator all work
from a richer, less ambiguous specification — reducing hallucinated column
names, wrong filter values, and missing JOINs.

The knowledge file covers only the MOST IMPORTANT tables in the schema —
it is intentionally not exhaustive. The SQL generator always has access to
the full schema DDL. The enricher uses the knowledge file for domain-level
term mappings and join hints, not as a complete table catalogue.

If the knowledge file is missing/empty or the LLM call fails the node passes
through unchanged (``enriched_query = user_input``).

Enable/disable via the ``QUERY_ENRICHER_ENABLED`` env var (default: true).
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Dict

from agent.prompts import load_prompt
from agent.trace import TraceStep

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
You are a senior database and compliance domain expert helping an NLP-to-SQL \
system produce accurate Oracle SQL queries.

Your task: interpret the user's natural-language query and rewrite it as a \
precise, grounded specification that the SQL generator can use.

Use the KNOWLEDGE BASE below to:
  • Map vague business terms to exact column names and allowed values
    (e.g. "high risk customers" → CUSTOMERS.RISK_RATING = 'HIGH')
  • Identify which tables are needed and what JOINs are implied
  • Surface business rules or constraints the query must respect
  • Flag Oracle-specific conventions (SYSDATE, FETCH FIRST N ROWS)

⚠ IMPORTANT — the knowledge base is NOT exhaustive:
  The database may contain many more tables than those listed below. This file \
covers only the most frequently queried, business-critical tables. For tables \
not mentioned here, the SQL generator has access to the complete schema DDL \
and can resolve them from context. Do NOT assume a table doesn't exist just \
because it isn't in this knowledge base.

Do NOT write SQL. Write a structured English specification that preserves the
user's original intent and adds precision. If the knowledge base has no \
relevant information for a query, say so briefly and pass the query through.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KNOWLEDGE BASE (key tables only — not exhaustive):
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

    # Load prompts from file (with inline defaults as fallback)
    system_template = load_prompt("query_enricher_system", default=_SYSTEM_TEMPLATE)
    human_template = load_prompt("query_enricher_human", default=_HUMAN_TEMPLATE)

    # Load and format the system message once at factory creation time.
    # _load_knowledge is lru_cache'd, but the format() call is not — so we do
    # it here rather than on every query invocation.
    _knowledge = _load_knowledge(resolved_path)
    _system_content = system_template.format(knowledge=_knowledge) if _knowledge else ""

    def enrich_query(state: Dict[str, Any]) -> Dict[str, Any]:
        user_input: str = state.get("user_input", "")
        _trace = list(state.get("_trace", []))
        trace = TraceStep("enrich_query", "enriching")

        logger.debug("Query enricher: user_input=%r", user_input[:100])

        if not user_input.strip():
            trace.output_summary = {"enriched_query_length": 0, "enriched_preview": ""}
            _trace.append(trace.finish().to_dict())
            return {**state, "enriched_query": user_input, "step": "query_enriched", "_trace": _trace}

        # Pass-through when no knowledge or no LLM
        if not (_system_content and llm):
            reason = "no knowledge file" if not _system_content else "no LLM"
            logger.debug("Query enricher: %s — using original query", reason)
            trace.output_summary = {"enriched_query_length": len(user_input), "enriched_preview": user_input[:200]}
            _trace.append(trace.finish().to_dict())
            return {**state, "enriched_query": user_input, "step": "query_enriched", "_trace": _trace}

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            system_msg = SystemMessage(content=_system_content)
            human_msg = HumanMessage(
                content=human_template.format(user_input=user_input)
            )

            response = llm.invoke([system_msg, human_msg])
            enriched: str = (
                response.content.strip()
                if hasattr(response, "content")
                else str(response).strip()
            )

            if not enriched:
                enriched = user_input

            raw_response_str = enriched
            logger.debug("LLM raw response:\n%s", raw_response_str)

            logger.info(
                "Query enriched successfully (original=%d chars → enriched=%d chars)",
                len(user_input), len(enriched),
            )
            logger.debug("Enriched query:\n%s", enriched)

            trace.set_llm_call(system_msg.content, human_msg.content, raw_response_str, enriched)
            trace.output_summary = {
                "enriched_query_length": len(enriched),
                "enriched_preview": enriched[:200],
            }
            _trace.append(trace.finish().to_dict())

            return {**state, "enriched_query": enriched, "step": "query_enriched", "_trace": _trace}

        except Exception as exc:
            logger.warning(
                "Query enricher LLM call failed — using original query. Error: %s", exc
            )
            trace.error = str(exc)
            trace.output_summary = {"enriched_query_length": len(user_input), "enriched_preview": user_input[:200]}
            _trace.append(trace.finish().to_dict())
            return {**state, "enriched_query": user_input, "step": "query_enriched", "_trace": _trace}

    return enrich_query
