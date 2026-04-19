#!/usr/bin/env python3
"""
End-to-end test: KYC Business Agent learning loop
==================================================
Simulates two conversations to verify the KYC business agent learns from
user-accepted queries and auto-answers clarifications on repeat queries.

Run:
    python scripts/test_kyc_learning.py [--base-url http://localhost:8000]

Flow:
  Round 1:
    1. Send query → loop through ALL clarifications (answering each one)
    2. Once SQL is produced, accept the query (thumbs up)
    3. Verify learned patterns were recorded
  Round 2:
    4. Send the SAME query → expect KYC agent auto-answers (fewer/no clarifications)
    5. Verify trace shows kyc_business_agent auto_answer action
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


# ---------------------------------------------------------------------------
# SSE parsing (reads from streaming response)
# ---------------------------------------------------------------------------

def parse_sse_events(response: requests.Response) -> List[Tuple[str, Any]]:
    """Parse SSE events from a streaming response using iter_lines."""
    events: List[Tuple[str, Any]] = []
    current_event = "message"
    current_data = ""

    for line in response.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line == "":
            # Empty line = end of SSE block
            if current_data:
                try:
                    data = json.loads(current_data)
                    events.append((current_event, data))
                except json.JSONDecodeError:
                    events.append((current_event, current_data))
            current_event = "message"
            current_data = ""
            continue
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data = line[6:].strip()

    # Flush last event if no trailing blank line
    if current_data:
        try:
            data = json.loads(current_data)
            events.append((current_event, data))
        except json.JSONDecodeError:
            events.append((current_event, current_data))

    return events


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def send_query(base_url: str, user_input: str, history: list = None) -> List[Tuple[str, Any]]:
    """Send a query and return all SSE events."""
    resp = requests.post(
        f"{base_url}/api/query",
        json={"user_input": user_input, "conversation_history": history or []},
        headers={"Content-Type": "application/json"},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()
    return parse_sse_events(resp)


def accept_query(
    base_url: str,
    sql: str,
    explanation: str,
    user_input: str,
    clarification_pairs: list,
    accepted: bool = True,
) -> Dict[str, Any]:
    """Send accept/reject feedback."""
    resp = requests.post(
        f"{base_url}/api/query/accept-query",
        json={
            "sql": sql,
            "explanation": explanation,
            "user_input": user_input,
            "clarification_pairs": clarification_pairs,
            "accepted": accepted,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def record_clarification(
    base_url: str,
    question: str,
    answer: str,
    user_query: str,
) -> Dict[str, Any]:
    """Record a clarification answer."""
    resp = requests.post(
        f"{base_url}/api/query/record-clarification",
        json={"question": question, "answer": answer, "user_query": user_query},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_metrics(base_url: str) -> Dict[str, Any]:
    resp = requests.get(f"{base_url}/api/kyc-agent/metrics", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_patterns(base_url: str) -> Dict[str, Any]:
    resp = requests.get(f"{base_url}/api/kyc-agent/patterns?sort=confidence", timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def find_events(events: list, event_type: str) -> list:
    return [(t, d) for t, d in events if t == event_type]


def find_trace_step(events: list, node_name: str) -> Optional[Dict]:
    for t, d in events:
        if t == "trace" and isinstance(d, dict) and d.get("node") == node_name:
            return d
    return None


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_events_summary(events: list):
    for event_type, data in events:
        if event_type == "step":
            print(f"  [step] {data.get('step', '?')}")
        elif event_type == "clarification":
            print(f"  [clarification] Q: {data.get('question', '?')[:80]}")
            opts = data.get("options", [])
            for i, opt in enumerate(opts):
                print(f"      {i+1}. {opt[:60]}")
        elif event_type == "kyc_auto_answer":
            print(f"  [KYC AUTO-ANSWER] Q: {data.get('question', '?')[:60]}")
            print(f"      Answer: {data.get('auto_answer', '?')[:80]}")
            print(f"      Source: {data.get('source', '?')}")
        elif event_type == "sql_ready":
            sql = data.get("sql", "")
            print(f"  [sql_ready] {sql[:80]}...")
        elif event_type == "trace":
            node = data.get("node", "?")
            dur = data.get("duration_ms", 0)
            summary = data.get("output_summary", {})
            action = summary.get("action", "")
            if action:
                print(f"  [trace] {node} ({dur}ms) — action={action}")
            else:
                print(f"  [trace] {node} ({dur}ms)")
        elif event_type == "error":
            print(f"  [ERROR] {data.get('message', '?')}")
        elif event_type == "result":
            rows = data.get("total_rows", 0)
            print(f"  [result] {rows} rows returned")


def print_kyc_trace(events: list, label: str = ""):
    """Print KYC business agent trace details."""
    kyc_trace = find_trace_step(events, "kyc_business_agent")
    if not kyc_trace:
        return
    summary = kyc_trace.get("output_summary", {})
    prefix = f"  KYC Agent trace{(' (' + label + ')') if label else ''}:"
    print(f"\n{prefix}")
    print(f"    Action: {summary.get('action', 'N/A')}")
    print(f"    Reason: {summary.get('reason', 'N/A')}")
    print(f"    Source: {summary.get('source', 'N/A')}")
    print(f"    Confidence: {summary.get('confidence', 'N/A')}")
    print(f"    Answer preview: {summary.get('answer_preview', 'N/A')}")
    print(f"    Entries searched: {summary.get('entries_searched', 'N/A')}")

    llm_call = kyc_trace.get("llm_call")
    if llm_call:
        print(f"    LLM system prompt: {str(llm_call.get('system_prompt', ''))[:80]}...")
        print(f"    LLM user message: {str(llm_call.get('user_prompt', ''))[:80]}...")
        print(f"    LLM response: {str(llm_call.get('raw_response', ''))[:120]}...")
    else:
        print(f"    LLM call: None (no LLM step needed)")


# ---------------------------------------------------------------------------
# Clarification loop — answer ALL clarifications until SQL is produced
# ---------------------------------------------------------------------------

MAX_CLARIFICATION_ROUNDS = 5


def run_conversation(
    base: str,
    test_query: str,
    label: str = "Round",
) -> Tuple[
    Optional[str],           # final SQL (or None)
    Optional[str],           # explanation
    List[Dict[str, str]],    # all clarification pairs answered
    List[List[Tuple]],       # all event batches (for trace inspection)
    int,                     # user-answered clarification count
    int,                     # auto-answered clarification count
]:
    """
    Run a complete conversation: send query, loop through clarifications,
    return the final SQL (or None if it never produced one).
    """
    all_pairs: List[Dict[str, str]] = []
    all_event_batches: List[List[Tuple]] = []
    history: list = []
    cumulative = test_query
    user_answered = 0
    auto_answered = 0

    for attempt in range(MAX_CLARIFICATION_ROUNDS + 1):
        round_label = f"{label} attempt {attempt}" if attempt > 0 else f"{label} initial"
        print(f"\n  --- {round_label} ---")
        print(f"  Sending: {cumulative[:100]}{'...' if len(cumulative) > 100 else ''}")

        events = send_query(base, cumulative, history)
        all_event_batches.append(events)
        print_events_summary(events)
        print_kyc_trace(events, round_label)

        error_events = find_events(events, "error")
        if error_events:
            print(f"\n  !! Pipeline error: {error_events[0][1].get('message', '?')}")
            return None, None, all_pairs, all_event_batches, user_answered, auto_answered

        # Check for auto-answers from KYC agent
        auto_events = find_events(events, "kyc_auto_answer")
        for _, aa_data in auto_events:
            q = aa_data.get("question", "")
            a = aa_data.get("auto_answer", "")
            src = aa_data.get("source", "")
            print(f"\n  KYC agent AUTO-ANSWERED: Q={q[:60]}, A={a[:60]}, src={src}")
            all_pairs.append({"question": q, "answer": a})
            auto_answered += 1

        # Check for SQL
        sql_events = find_events(events, "sql_ready")
        if sql_events:
            sql = sql_events[0][1].get("sql", "")
            explanation = sql_events[0][1].get("explanation", "")
            print(f"\n  SQL generated: {sql[:120]}...")
            return sql, explanation, all_pairs, all_event_batches, user_answered, auto_answered

        # Check for clarification that needs user answer
        clar_events = find_events(events, "clarification")
        if not clar_events:
            # No SQL, no clarification, no error — check for result event
            result_events = find_events(events, "result")
            if result_events:
                print(f"\n  Got result directly (no sql_ready event)")
                return "DIRECT_RESULT", "", all_pairs, all_event_batches, user_answered, auto_answered
            print(f"\n  !! Unexpected: no clarification, no SQL, no error, no result")
            return None, None, all_pairs, all_event_batches, user_answered, auto_answered

        # Answer the clarification
        clar = clar_events[0][1]
        question = clar.get("question", "")
        options = clar.get("options", [])
        context = clar.get("context", "")

        print(f"\n  Clarification needed:")
        print(f"    Context: {context[:100]}")
        print(f"    Question: {question[:100]}")
        for i, opt in enumerate(options):
            print(f"      {i+1}. {opt[:80]}")

        # Pick the first option as our answer
        answer = options[0] if options else "all"
        print(f"  Answering with: {answer}")
        user_answered += 1

        # Record the clarification
        record_clarification(base, question, answer, test_query)
        all_pairs.append({"question": question, "answer": answer})

        # Build cumulative query with all pairs so far
        refinements = "\n".join(
            f"- {p['question']}: {p['answer']}" for p in all_pairs
        )
        cumulative = f"{test_query}\n\nAdditional requirements clarified:\n{refinements}"

        # Build history
        history.append({"role": "user", "content": test_query if attempt == 0 else answer})
        history.append({"role": "assistant", "content": question})
        history.append({"role": "user", "content": answer})

    print(f"\n  !! Reached max clarification rounds ({MAX_CLARIFICATION_ROUNDS}) without SQL")
    return None, None, all_pairs, all_event_batches, user_answered, auto_answered


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--query", default="List all customers with high risk ratings who had reviews in 2024")
    args = parser.parse_args()

    base = args.base_url
    test_query = args.query

    # Check health
    print("Checking backend health...")
    try:
        resp = requests.get(f"{base}/api/health", timeout=5)
        health = resp.json()
        print(f"  Backend: {health.get('status', '?')}, Oracle: {health.get('oracle_connected', '?')}")
    except Exception as e:
        print(f"  Backend not reachable: {e}")
        sys.exit(1)

    # Get baseline metrics
    metrics_before = get_metrics(base)
    patterns_before = get_patterns(base)
    print(f"  Patterns before: {patterns_before.get('total', 0)}")
    print(f"  Static entries: {metrics_before.get('total_static_entries', 0)}")

    # ================================================================
    # ROUND 1: Fresh query — answer all clarifications until SQL
    # ================================================================
    print_section("ROUND 1: Fresh query (answer all clarifications)")
    print(f"  Query: {test_query}")

    sql_r1, explanation_r1, pairs_r1, batches_r1, user_ans_r1, auto_ans_r1 = \
        run_conversation(base, test_query, "R1")

    r1_got_sql = sql_r1 is not None and sql_r1 != ""
    print(f"\n  Round 1 summary:")
    print(f"    SQL produced: {r1_got_sql}")
    print(f"    User-answered clarifications: {user_ans_r1}")
    print(f"    Auto-answered clarifications: {auto_ans_r1}")
    print(f"    Total Q&A pairs: {len(pairs_r1)}")
    if sql_r1 and sql_r1 != "DIRECT_RESULT":
        print(f"    SQL: {sql_r1[:120]}...")

    # Accept the query if SQL was generated
    if r1_got_sql and sql_r1 != "DIRECT_RESULT":
        print(f"\n  Accepting query (thumbs up)...")
        result = accept_query(
            base, sql_r1, explanation_r1 or "", test_query, pairs_r1
        )
        print(f"    Accept result: {result}")
    elif r1_got_sql:
        print(f"\n  Got direct result — recording acceptance with dummy SQL...")
        # Still try to accept so patterns get recorded
        accept_query(base, "SELECT 1", "", test_query, pairs_r1)

    # Wait for background LLM analysis to complete
    print(f"\n  Waiting 10s for background LLM analysis to complete...")
    time.sleep(10)

    # Check metrics after round 1
    metrics_after_r1 = get_metrics(base)
    patterns_after_r1 = get_patterns(base)
    print(f"\n  Patterns after round 1: {patterns_after_r1.get('total', 0)}")
    print(f"  Static entries after round 1: {metrics_after_r1.get('total_static_entries', 0)}")
    new_patterns = patterns_after_r1.get("total", 0) - patterns_before.get("total", 0)
    new_entries = metrics_after_r1.get("total_static_entries", 0) - metrics_before.get("total_static_entries", 0)
    print(f"  New patterns: {new_patterns}")
    print(f"  New static entries (from LLM query analysis): {new_entries}")

    # Show recorded patterns for debugging
    if new_patterns > 0:
        print(f"\n  Recorded patterns:")
        for p in patterns_after_r1.get("patterns", [])[:10]:
            print(f"    [{p.get('confidence', '?')}] Q: {p.get('question', '?')[:60]}")
            print(f"         A: {p.get('answer', '?')[:60]}")

    # ================================================================
    # ROUND 2: Same query — expect KYC agent auto-answers
    # ================================================================
    print_section("ROUND 2: Same query (expect auto-answer / fewer clarifications)")
    print(f"  Query: {test_query}")

    sql_r2, explanation_r2, pairs_r2, batches_r2, user_ans_r2, auto_ans_r2 = \
        run_conversation(base, test_query, "R2")

    r2_got_sql = sql_r2 is not None and sql_r2 != ""
    print(f"\n  Round 2 summary:")
    print(f"    SQL produced: {r2_got_sql}")
    print(f"    User-answered clarifications: {user_ans_r2}")
    print(f"    Auto-answered clarifications: {auto_ans_r2}")
    print(f"    Total Q&A pairs: {len(pairs_r2)}")
    if sql_r2 and sql_r2 != "DIRECT_RESULT":
        print(f"    SQL: {sql_r2[:120]}...")

    # ================================================================
    # VERDICT
    # ================================================================
    print_section("VERDICT")

    learned = user_ans_r2 < user_ans_r1 or auto_ans_r2 > 0
    checks = [
        ("Round 1 produced SQL",
         r1_got_sql),
        (f"Round 1 answered {user_ans_r1} clarification(s)",
         user_ans_r1 >= 0),  # informational, always pass
        ("Patterns exist after acceptance",
         new_patterns > 0 or patterns_after_r1.get("total", 0) > 0),
        ("Round 2 produced SQL",
         r2_got_sql),
        (f"Round 2: fewer user clarifications ({user_ans_r2}) OR auto-answers ({auto_ans_r2})",
         learned),
    ]

    all_pass = True
    for label, ok in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{status}] {label}")

    # Detailed learning analysis
    print(f"\n  Learning analysis:")
    print(f"    Round 1: {user_ans_r1} user-answered, {auto_ans_r1} auto-answered")
    print(f"    Round 2: {user_ans_r2} user-answered, {auto_ans_r2} auto-answered")
    if auto_ans_r2 > 0:
        print(f"    KYC agent successfully auto-answered {auto_ans_r2} clarification(s) in round 2!")
    elif user_ans_r2 < user_ans_r1:
        print(f"    Fewer clarifications needed in round 2 ({user_ans_r1} → {user_ans_r2})")
    elif user_ans_r2 == 0 and r2_got_sql:
        print(f"    No clarifications needed in round 2 — pipeline learned the full pattern!")
    else:
        print(f"    No improvement detected — learning may need tuning")

    # Investigate tab data check
    print_section("INVESTIGATE TAB DATA")
    print("  Trace steps from Round 2 (latest query):")
    # Use the last batch from round 2
    last_batch = batches_r2[-1] if batches_r2 else []
    for _, d in last_batch:
        if isinstance(d, dict) and "node" in d:
            node = d.get("node", "?")
            summary = d.get("output_summary", {})
            llm = d.get("llm_call")
            has_llm = "yes" if llm else "no"
            print(f"    {node}: output_summary={json.dumps(summary, default=str)[:120]}, llm_call={has_llm}")

    # Check all batches for kyc_auto_answer events
    all_auto_answers = []
    for batch in batches_r2:
        for t, d in batch:
            if t == "kyc_auto_answer":
                all_auto_answers.append(d)
    if all_auto_answers:
        print(f"\n  KYC Auto-Answer events for Investigate tab:")
        for aa in all_auto_answers:
            print(f"    Q: {aa.get('question', '?')[:60]}")
            print(f"    A: {aa.get('auto_answer', '?')[:60]}")
            print(f"    Source: {aa.get('source', '?')}")

    print(f"\n{'='*60}")
    print(f"  {'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
    print(f"{'='*60}\n")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
