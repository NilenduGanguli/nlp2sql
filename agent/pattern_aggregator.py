"""Pattern Aggregator — clusters similar accepted query_session entries
and promotes them to VerifiedPattern when thresholds met.

Triggered after each curator (and debounced consumer) accept-query.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import List, Optional

from agent.knowledge_store import (
    KYCKnowledgeStore, KnowledgeEntry, VerifiedPattern,
    _tokenize,
)
from agent.signal_log import SignalLog
from agent.sql_skeleton import sql_skeleton

logger = logging.getLogger(__name__)

MIN_ACCEPT_COUNT = 3
MIN_DISTINCT_SESSIONS = 2

_SIGNAL_WEIGHTS_CURATOR = {
    "ran_unchanged": 1.0, "opened_in_editor": 0.5, "copied_sql": 0.3,
    "abandoned_session": -0.5, "zero_rows_retry": -0.7, "edited_then_ran": 0.0,
}
_SIGNAL_WEIGHTS_CONSUMER = {k: v * 0.1 for k, v in _SIGNAL_WEIGHTS_CURATOR.items()}


def _pattern_id(skeleton: str) -> str:
    return "vp_" + hashlib.sha1(skeleton.encode("utf-8")).hexdigest()[:12]


def _accepted_sql(entry: KnowledgeEntry) -> Optional[str]:
    accepted = (entry.metadata or {}).get("accepted_candidates", []) or []
    if not accepted:
        return None
    return accepted[0].get("sql", "")


def aggregate_patterns(
    store: KYCKnowledgeStore,
    accepted_entry: KnowledgeEntry,
    signals: SignalLog,
    mode: str = "curator",
    manual_promotion: bool = False,
) -> Optional[VerifiedPattern]:
    """Cluster sessions matching the just-accepted entry and promote if eligible.
    Returns the promoted (or updated) pattern, or None if not eligible.
    """
    accepted_sql = _accepted_sql(accepted_entry)
    if not accepted_sql:
        return None

    skel = sql_skeleton(accepted_sql)
    if not skel:
        return None

    accepted_q = (accepted_entry.metadata or {}).get("original_query", "")
    qtoks = _tokenize(accepted_q)

    cluster: List[KnowledgeEntry] = []
    distinct_sessions = set()
    for e in store.static_entries:
        if e.source != "query_session" or e.category != "query_session":
            continue
        sql = _accepted_sql(e)
        if not sql or sql_skeleton(sql) != skel:
            continue
        meta = e.metadata or {}
        other_toks = _tokenize(meta.get("original_query", ""))
        if qtoks and other_toks and not (qtoks & other_toks):
            continue
        tables = set(meta.get("tables_used", []) or [])
        accepted_tables = set((accepted_entry.metadata or {}).get("tables_used", []) or [])
        if accepted_tables and not (tables & accepted_tables):
            continue
        cluster.append(e)
        distinct_sessions.add(e.id)

    if accepted_entry.id not in distinct_sessions:
        cluster.append(accepted_entry)
        distinct_sessions.add(accepted_entry.id)

    accept_count = len(cluster)

    pos = neg = 0.0
    for e in cluster:
        for evname in _SIGNAL_WEIGHTS_CURATOR:
            for sig in signals.load(event=evname, entry_id=e.id):
                w = (_SIGNAL_WEIGHTS_CURATOR if sig.mode == "curator"
                     else _SIGNAL_WEIGHTS_CONSUMER)[evname]
                if w >= 0:
                    pos += w
                else:
                    neg += -w

    score = accept_count + pos - neg

    eligible = manual_promotion or (
        accept_count >= MIN_ACCEPT_COUNT
        and len(distinct_sessions) >= MIN_DISTINCT_SESSIONS
        and neg < accept_count / 2
    )

    if not eligible:
        return None

    pid = _pattern_id(skel)
    consumer_uses = sum(
        1 for e in cluster
        for sig in signals.load(entry_id=e.id)
        if sig.mode == "consumer"
    )

    pattern = VerifiedPattern(
        pattern_id=pid,
        sql_skeleton=skel,
        exemplar_query=accepted_q,
        exemplar_sql=accepted_sql,
        tables_used=list((accepted_entry.metadata or {}).get("tables_used", []) or []),
        accept_count=accept_count,
        consumer_uses=consumer_uses,
        negative_signals=int(neg),
        score=float(score),
        promoted_at=time.time(),
        source_entry_ids=[e.id for e in cluster],
        manual_promotion=manual_promotion,
    )
    store.add_pattern(pattern)
    logger.info("pattern promoted: %s (score=%.2f, accepts=%d)", pid, score, accept_count)
    return pattern
