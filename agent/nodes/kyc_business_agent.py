"""
KYC Business Agent Node
========================
Intercepts clarification questions before they reach the user and attempts
to auto-answer them using the KYC knowledge base (static entries + learned
patterns).

Decision flow:
  1. Is this a user-preference question?  → route to user
  2. Search learned patterns (Jaccard)    → auto-answer if confidence >= 0.6
  3. Search static knowledge + LLM        → auto-answer if LLM says can_answer
  4. Nothing works                        → route to user
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from agent.knowledge_store import KYCKnowledgeStore
from agent.state import AgentState
from agent.trace import TraceStep

logger = logging.getLogger(__name__)

# Patterns that indicate user-preference questions (subjective choices)
_USER_PREF_PATTERNS = [
    r"which\s+(specific|particular)\s+(status|value|type|filter)",
    r"do you want\s+(active|all|specific)",
    r"what\s+(kind|type)\s+of\s+.*(do you|would you)",
    r"how\s+many\s+.*(do you|would you)\s+(want|like|need)",
    r"=\s*'[^']*'",  # SQL-like value expressions in options
]

_USER_PREF_RE = re.compile("|".join(_USER_PREF_PATTERNS), re.IGNORECASE)


def make_kyc_business_agent(
    llm=None,
    knowledge_store: Optional[KYCKnowledgeStore] = None,
) -> Callable[[AgentState], AgentState]:
    """Return a LangGraph node that auto-answers clarification questions.

    Parameters
    ----------
    llm : BaseChatModel | None
        LLM for knowledge-based reasoning. If None, only pattern matching is used.
    knowledge_store : KYCKnowledgeStore | None
        The knowledge store. If None, a passthrough node is returned.
    """
    # Load system prompt
    _system_prompt = ""
    try:
        import os
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "prompts", "kyc_business_agent_system.txt"
        )
        with open(prompt_path, "r") as f:
            _system_prompt = f.read().strip()
    except Exception:
        _system_prompt = "You are a KYC domain expert. Answer clarification questions from the knowledge base."

    def kyc_business_agent(state: AgentState) -> AgentState:
        _trace = list(state.get("_trace", []))
        trace = TraceStep("kyc_business_agent", "auto_clarifying")

        question = state.get("clarification_question", "")
        options = state.get("clarification_options", [])
        user_query = state.get("user_input", "")

        if not question:
            trace.output_summary = {"action": "skip", "reason": "no_question"}
            _trace.append(trace.finish().to_dict())
            return {**state, "_trace": _trace}

        # Step 1: Check if this is a user-preference question
        if _is_user_preference(question, options):
            trace.output_summary = {"action": "route_to_user", "reason": "user_preference"}
            _trace.append(trace.finish().to_dict())
            return {
                **state,
                "kyc_auto_answered": False,
                "kyc_auto_answer": "",
                "_trace": _trace,
            }

        # Step 2: Check learned patterns
        if knowledge_store:
            pattern = knowledge_store.find_matching_pattern(question, user_query)
            if pattern and pattern.confidence >= 0.6:
                answer = pattern.answer
                # Bump usage
                knowledge_store.bump_confidence(pattern.id, delta=0.0)  # just bump use_count
                trace.output_summary = {
                    "action": "auto_answer",
                    "source": "learned_pattern",
                    "pattern_id": pattern.id,
                    "confidence": pattern.confidence,
                    "answer_preview": answer[:100],
                }
                _trace.append(trace.finish().to_dict())
                return {
                    **state,
                    "kyc_auto_answered": True,
                    "kyc_auto_answer": answer,
                    "need_clarification": False,
                    "clarification_question": "",
                    "clarification_options": [],
                    "_trace": _trace,
                }

        # Step 3: Search static knowledge + LLM
        if knowledge_store and llm:
            relevant_entries = _find_relevant_entries(knowledge_store, question, user_query)
            if relevant_entries:
                answer_data = _ask_llm(
                    llm, _system_prompt, question, user_query, relevant_entries, trace
                )
                if answer_data and answer_data.get("can_answer"):
                    answer = answer_data.get("answer", "")
                    confidence = float(answer_data.get("confidence", 0.7))
                    if confidence >= 0.6 and answer:
                        trace.output_summary = {
                            "action": "auto_answer",
                            "source": "knowledge_base",
                            "confidence": confidence,
                            "answer_preview": answer[:100],
                            "entries_searched": len(relevant_entries),
                        }
                        _trace.append(trace.finish().to_dict())
                        return {
                            **state,
                            "kyc_auto_answered": True,
                            "kyc_auto_answer": answer,
                            "need_clarification": False,
                            "clarification_question": "",
                            "clarification_options": [],
                            "_trace": _trace,
                        }

        # Step 4: Route to user
        trace.output_summary = {"action": "route_to_user", "reason": "no_answer_found"}
        _trace.append(trace.finish().to_dict())
        return {
            **state,
            "kyc_auto_answered": False,
            "kyc_auto_answer": "",
            "_trace": _trace,
        }

    return kyc_business_agent


def _is_user_preference(question: str, options: List[str]) -> bool:
    """Check if the question is asking for a subjective user preference."""
    # Check question text
    if _USER_PREF_RE.search(question):
        return True
    # Check if options contain SQL-like value expressions
    sql_value_count = sum(1 for opt in options if re.search(r"=\s*'", opt))
    if sql_value_count >= 2:
        return True
    return False


def _find_relevant_entries(
    store: KYCKnowledgeStore, question: str, user_query: str, top_k: int = 5
) -> List[Dict[str, Any]]:
    """Find the most relevant knowledge entries by keyword overlap."""
    from agent.knowledge_store import _tokenize, _jaccard

    query_tokens = _tokenize(question + " " + user_query)
    if not query_tokens:
        return []

    scored = []
    for entry in store.static_entries:
        entry_tokens = _tokenize(entry.content)
        if not entry_tokens:
            continue
        score = _jaccard(query_tokens, entry_tokens)
        if score > 0.05:  # minimal relevance threshold
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"content": e.content, "category": e.category, "score": round(s, 3)}
            for s, e in scored[:top_k]]


def _ask_llm(
    llm,
    system_prompt: str,
    question: str,
    user_query: str,
    relevant_entries: List[Dict[str, Any]],
    trace: TraceStep,
) -> Optional[Dict[str, Any]]:
    """Ask the LLM to answer the clarification from knowledge."""
    knowledge_text = "\n\n".join(
        f"[{e['category']}] (relevance: {e['score']})\n{e['content']}"
        for e in relevant_entries
    )

    user_message = (
        f"User's original query: {user_query}\n\n"
        f"Clarification question: {question}\n\n"
        f"Relevant knowledge:\n{knowledge_text}\n\n"
        f"Can you answer this clarification from the knowledge above? "
        f"Respond with JSON only."
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        raw = response.content if hasattr(response, "content") else str(response)

        trace.set_llm_call(system_prompt, user_message, raw)

        # Parse JSON from response
        json_match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as exc:
        logger.warning("KYC business agent LLM call failed: %s", exc)
        trace.error = str(exc)

    return None
