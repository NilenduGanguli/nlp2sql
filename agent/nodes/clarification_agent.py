"""
Clarification Agent Node
========================
Acts as an expert KYC/AML data analyst who:
1. Summarises what it already understands from the query
2. Identifies the single most important missing dimension
3. Offers 3-6 business-meaningful options + a free-text escape hatch

The agent is designed for multi-turn conversation — it accumulates requirements
across turns and only asks for information that genuinely changes the SQL.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from agent.prompts import load_prompt
from agent.trace import TraceStep

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a senior KYC/AML data analyst helping a user formulate a precise database query.

Your role is NOT to write SQL — it is to deeply understand what the user needs and
guide them to express it clearly, using your expert knowledge of KYC/AML concepts
and the database schema provided.

THINKING APPROACH
─────────────────
Before responding, silently reason through:
• What is the user fundamentally trying to achieve? (compliance reporting, customer
  investigation, risk monitoring, audit trail, etc.)
• What tables and joins will be needed?
• What dimensions are genuinely ambiguous (and would produce meaningfully different
  results depending on the answer)?
• Which single question, if answered, would most reduce ambiguity?

WHEN TO ASK FOR CLARIFICATION
──────────────────────────────
Ask when:
• A key filter is missing and any default would give misleading results
  (e.g. "list customers" — all? active only? with accounts? high-risk ones?)
• A term maps to multiple distinct business concepts
  (e.g. "status" could be KYC review status, account status, or customer status)
• The scope / time period matters and isn't specified
• Multiple JOIN paths exist that would give different business answers
• The user asked a broad analytical question where the metric/dimension isn't clear

Do NOT ask when:
• The conversation history already contains the required clarification
• The query is self-contained and has an obvious interpretation
• Only cosmetic output details are missing (column ordering, formatting)
• The last assistant message was a clarification and the user just answered it —
  use that answer to proceed

OPTION DESIGN
─────────────
Options must be expressed in business language, never as raw column values or
table names. Each option should describe a real-world scenario the user might mean.
Include 3–6 options. Always end with a free-text option like
"Custom — let me describe exactly what I need".

RESPONSE FORMAT
───────────────
Respond with compact JSON only — no markdown, no prose, no code fences.

If clarification is NOT needed:
{"needs_clarification": false}

If clarification IS needed:
{
  "needs_clarification": true,
  "understanding": "<1-2 sentence plain-English summary of what you understand the query to be asking — use business terms, mention key tables/filters you've identified>",
  "question": "<one focused, specific question in business language>",
  "options": ["<option1>", "<option2>", "<option3>", "...", "Custom — let me describe exactly what I need"],
  "multi_select": false
}

Set "multi_select": true only when the user is likely to want multiple options
combined with AND logic (e.g. selecting several risk levels, multiple statuses).
"""

_HUMAN = """\
Current query: {query}

Identified entities: {entities}

Conversation history (most recent last):
{history}

Schema context:
{schema}
"""


def make_clarification_agent(llm):
    """Return a LangGraph node function that checks for query ambiguity."""

    _sys = load_prompt("clarification_agent_system", default=_SYSTEM)
    _human = load_prompt("clarification_agent_human", default=_HUMAN)

    def check_clarification(state: Dict[str, Any]) -> Dict[str, Any]:
        _trace = list(state.get("_trace", []))
        trace = TraceStep("check_clarification", "clarification_check")

        _no_clarify: Dict[str, Any] = {
            **state,
            "need_clarification": False,
            "clarification_question": "",
            "clarification_options": [],
            "clarification_context": "",
        }

        user_input: str = (state.get("enriched_query") or state.get("user_input", "")).strip()
        if not user_input:
            trace.output_summary = {"needs_clarification": False}
            _trace.append(trace.finish().to_dict())
            result = dict(_no_clarify)
            result["_trace"] = _trace
            return result

        entities: Dict = state.get("entities", {})
        schema_context: str = state.get("schema_context", "")
        history: List[Dict] = state.get("conversation_history", [])

        entity_str = (
            ", ".join(f"{k}: {v}" for k, v in entities.items() if v) or "none identified"
        )
        # Provide enough schema context for domain reasoning
        schema_str = schema_context[:4000] if schema_context else "No schema context available."

        # Format recent history (last 10 turns) for the prompt
        if history:
            recent = history[-10:]
            history_str = "\n".join(
                f"  {turn.get('role', 'user').upper()}: {str(turn.get('content', ''))[:300]}"
                for turn in recent
            )
        else:
            history_str = "  (no prior conversation)"

        human_content = _human.format(
            query=user_input,
            entities=entity_str,
            history=history_str,
            schema=schema_str,
        )

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = llm.invoke(
                [
                    SystemMessage(content=_sys),
                    HumanMessage(content=human_content),
                ]
            )

            raw: str = (
                response.content.strip()
                if hasattr(response, "content")
                else str(response).strip()
            )

            logger.debug("Clarification LLM raw response: %s", raw)

            # Normalise: strip thinking tags, code fences, trailing commas
            raw = re.sub(r"<thinking>[\s\S]*?</thinking>", "", raw, flags=re.IGNORECASE)
            fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
            raw = fence.group(1).strip() if fence else raw
            obj = re.search(r"\{[\s\S]*\}", raw)
            raw = obj.group() if obj else raw
            raw = re.sub(r",\s*([\]}])", r"\1", raw)

            result = json.loads(raw.strip())

            trace.set_llm_call(_sys, human_content, raw, result)
            trace.output_summary = {
                "needs_clarification": result.get("needs_clarification"),
                "question": result.get("question", ""),
                "understanding": result.get("understanding", "")[:100],
            }

            if result.get("needs_clarification"):
                question = str(result.get("question", "")).strip()
                options: List[str] = [str(o) for o in result.get("options", [])]
                understanding: str = str(result.get("understanding", "")).strip()
                multi_select: bool = bool(result.get("multi_select", False))

                if question:
                    logger.info(
                        "Clarification needed: %r (options=%d, multi_select=%s)",
                        question, len(options), multi_select,
                    )
                    _trace.append(trace.finish().to_dict())
                    return {
                        **state,
                        "need_clarification": True,
                        "clarification_question": question,
                        "clarification_options": options,
                        "clarification_context": understanding,
                        "_trace": _trace,
                    }

        except Exception as exc:
            logger.warning(
                "Clarification check failed (%s) — proceeding without clarification", exc
            )
            trace.error = str(exc)
            trace.output_summary = {"needs_clarification": False, "error": str(exc)}

        _trace.append(trace.finish().to_dict())
        result = dict(_no_clarify)
        result["_trace"] = _trace
        return result

    return check_clarification
