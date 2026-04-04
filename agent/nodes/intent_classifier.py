"""
Intent Classifier Node
=======================
Classifies the user's query into one of four intent categories using an LLM.

Intent categories:
  DATA_QUERY     – User wants to retrieve data (SELECT query)
  SCHEMA_EXPLORE – User wants to understand table/column structure
  QUERY_EXPLAIN  – User wants an existing SQL query explained
  QUERY_REFINE   – User wants to modify a previously generated query
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable

from agent.prompts import load_prompt
from agent.state import AgentState
from agent.trace import TraceStep

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a query intent classifier for a KYC (Know Your Customer) compliance database system.

Classify the user's query into exactly ONE of these intents:
- DATA_QUERY: The user wants to retrieve or aggregate data from the database
  Examples: "Show me high-risk customers", "How many transactions last month?", "List all PEP-flagged customers"
- SCHEMA_EXPLORE: The user wants to understand the database structure, tables, or columns
  Examples: "What tables are in the KYC schema?", "What columns does CUSTOMERS have?", "Describe the TRANSACTIONS table"
- QUERY_EXPLAIN: The user wants an explanation of an existing SQL query
  Examples: "Explain this query: SELECT ...", "What does this SQL do?", "Break down this query for me"
- QUERY_REFINE: The user wants to modify or improve a previously generated query
  Examples: "Add a filter for last month", "Also include the account balance", "Limit results to 50 rows"

Respond with ONLY valid JSON in this exact format:
{"intent": "DATA_QUERY", "confidence": 0.95, "reasoning": "brief explanation"}

No other text before or after the JSON."""


def make_intent_classifier(llm) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node function that classifies user intent.

    Parameters
    ----------
    llm : BaseChatModel
        A LangChain chat model instance.

    Returns
    -------
    Callable[[AgentState], AgentState]
        A node function compatible with LangGraph's StateGraph.
    """
    system_prompt = load_prompt("intent_classifier_system", default=_SYSTEM_PROMPT)

    def classify_intent(state: AgentState) -> AgentState:
        user_input = state.get("user_input", "")
        _trace = list(state.get("_trace", []))
        trace = TraceStep("classify_intent", "classifying")

        logger.debug("Classifying intent for: %r", user_input[:100])

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Query: {user_input}"),
            ]
            response = llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)

            logger.debug("Intent LLM raw response: %s", content)

            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r"\{[^{}]+\}", content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                intent = parsed.get("intent", "DATA_QUERY").upper()
                confidence = float(parsed.get("confidence", 0.9))
            else:
                logger.warning("Intent classifier returned non-JSON: %r", content[:200])
                intent = "DATA_QUERY"
                confidence = 0.5

            # Validate intent value
            valid_intents = {"DATA_QUERY", "SCHEMA_EXPLORE", "QUERY_EXPLAIN", "QUERY_REFINE"}
            if intent not in valid_intents:
                logger.warning("Unknown intent %r, defaulting to DATA_QUERY", intent)
                intent = "DATA_QUERY"

            logger.info("Intent classified: %s (confidence=%.2f)", intent, confidence)

            trace.set_llm_call(system_prompt, f"Query: {user_input}", content, {"intent": intent, "confidence": confidence})
            trace.output_summary = {"intent": intent}

        except Exception as exc:
            logger.error("Intent classification failed: %s", exc)
            intent = "DATA_QUERY"
            trace.error = str(exc)
            trace.output_summary = {"intent": intent}

        _trace.append(trace.finish().to_dict())
        return {**state, "intent": intent, "step": "intent_classified", "_trace": _trace}

    return classify_intent
