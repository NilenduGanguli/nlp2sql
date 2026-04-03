"""
Clarification Agent Node
========================
Checks if the user's NL query is ambiguous before SQL generation.
When clarification is needed it sets ``need_clarification=True`` and
populates ``clarification_question`` / ``clarification_options`` in state.

The LLM receives the full conversation history and schema context so it can
decide whether more information is genuinely needed — even in multi-turn
conversations a new ambiguous question should still trigger clarification.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a data analyst reviewing a natural-language database query before writing SQL.

Decide whether the query needs clarification. Only ask when it GENUINELY matters for
producing the correct query — not for cosmetic or trivially-resolvable ambiguity.

Ask for clarification when:
- Multiple JOIN paths exist between the relevant tables and they would give different
  business results depending on which path is taken
- A key term could map to distinctly different columns or tables (e.g. "customer" in a
  schema that has RETAIL_CUSTOMER, CORPORATE_CUSTOMER, and CRM_CONTACT)
- An essential filter is clearly missing and any default would be misleading or dangerous
  (e.g. "show all transactions" on a table known to have millions of rows with no filter)
- The aggregation level is genuinely ambiguous (per-transaction vs per-customer vs
  per-account) and matters for the answer

Do NOT ask for clarification when:
- The query is clear even in a multi-turn conversation
- The conversation history already answers what the user is looking for
- The user just answered a previous clarification question — if the last assistant
  message was a clarification question and the current user message is a short
  direct answer, proceed without asking again
- The user is asking about schema structure or relationships
- A sensible default exists and would clearly satisfy the user

Respond with valid compact JSON only — no markdown, no code fences, no extra text:
{"needs_clarification": false}
OR
{"needs_clarification": true, "question": "<one concise, specific question>", "options": ["<option1>", "<option2>"]}

Options must be 2–4 short, mutually exclusive choices. Use an empty array [] for
open-ended questions where predefined options don't make sense."""

_HUMAN = """\
Current query: {query}

Identified entities: {entities}

Conversation history (most recent last):
{history}

Schema context (condensed):
{schema}"""


def make_clarification_agent(llm):
    """Return a LangGraph node function that checks for query ambiguity."""

    def check_clarification(state: Dict[str, Any]) -> Dict[str, Any]:
        _no_clarify: Dict[str, Any] = {
            **state,
            "need_clarification": False,
            "clarification_question": "",
            "clarification_options": [],
        }

        user_input: str = (state.get("enriched_query") or state.get("user_input", "")).strip()
        if not user_input:
            return _no_clarify

        entities: Dict = state.get("entities", {})
        schema_context: str = state.get("schema_context", "")
        history: List[Dict] = state.get("conversation_history", [])

        entity_str = (
            ", ".join(f"{k}: {v}" for k, v in entities.items() if v) or "none identified"
        )
        schema_str = schema_context[:3000] if schema_context else "No schema context available."

        # Format recent history (last 8 turns) for the prompt
        if history:
            recent = history[-8:]
            history_str = "\n".join(
                f"  {turn.get('role', 'user').upper()}: {str(turn.get('content', ''))[:200]}"
                for turn in recent
            )
        else:
            history_str = "  (no prior conversation)"

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = llm.invoke(
                [
                    SystemMessage(content=_SYSTEM),
                    HumanMessage(
                        content=_HUMAN.format(
                            query=user_input,
                            entities=entity_str,
                            history=history_str,
                            schema=schema_str,
                        )
                    ),
                ]
            )

            raw: str = (
                response.content.strip()
                if hasattr(response, "content")
                else str(response).strip()
            )

            # Normalise: strip thinking tags, code fences, trailing commas
            raw = re.sub(r"<thinking>[\s\S]*?</thinking>", "", raw, flags=re.IGNORECASE)
            fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
            raw = fence.group(1).strip() if fence else raw
            obj = re.search(r"\{[\s\S]*\}", raw)
            raw = obj.group() if obj else raw
            raw = re.sub(r",\s*([\]}])", r"\1", raw)

            result = json.loads(raw.strip())

            if result.get("needs_clarification"):
                question = str(result.get("question", "")).strip()
                options: List[str] = [str(o) for o in result.get("options", [])]
                if question:
                    logger.info("Clarification needed: %r (options=%s)", question, options)
                    return {
                        **state,
                        "need_clarification": True,
                        "clarification_question": question,
                        "clarification_options": options,
                    }

        except Exception as exc:
            logger.warning(
                "Clarification check failed (%s) — proceeding without clarification", exc
            )

        return _no_clarify

    return check_clarification
