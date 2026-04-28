"""
KYC Knowledge Store
===================
Manages two pools of knowledge used by the KYC Business Agent:

1. **Static entries** — loaded once from business documents
   (``kyc_business_knowledge_agentic/business_json_template.txt``,
   ``kyc_business_knowledge_agentic/business_table_relation.txt``,
   and any other ``.txt`` files in ``kyc_business_knowledge_agentic/``).

2. **Learned patterns** — accumulated at runtime from clarification answers,
   candidate selections, and execution confirmations.

Persistence: JSON file written atomically (tmp + os.replace). Located in the
same directory as the graph cache (``GRAPH_CACHE_PATH`` or
``~/.cache/knowledgeql``).

Pruning strategy:
  score = confidence * 0.4 + recency * 0.3 + frequency * 0.3
  Triggered at 400 patterns; keeps top 350; hard cap 500.
  Patterns with confidence >= 0.9 AND use_count >= 5 are protected.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_DOCKER_CACHE_DIR = "/data/graph_cache"
_LOCAL_CACHE_DIR = os.path.expanduser("~/.cache/knowledgeql")

MAX_LEARNED_PATTERNS = 500
PRUNE_TRIGGER = 400
PRUNE_KEEP = 350
PRUNE_RECENCY_DAYS = 90

SESSION_MATCH_THRESHOLD = 0.65


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeEntry:
    """A static knowledge entry loaded from business documents."""
    id: str
    source: str                 # business_json_template | business_table_relation | document | manual
    content: str
    category: str               # table_info | column_values | relationships | business_rule
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeEntry":
        return cls(**d)


@dataclass
class LearnedPattern:
    """A pattern learned from user interactions."""
    id: str
    question_pattern: str       # The clarification question
    answer: str                 # Auto or user answer
    original_user_query: str
    resulting_sql: str
    user_confirmed: bool        # Did user accept execution?
    confidence: float           # 0.0–1.0
    category: str               # filter_value | scope | join_path | time_range | aggregation
    created_at: float
    last_used_at: float
    use_count: int
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LearnedPattern":
        return cls(**d)


@dataclass
class VerifiedPattern:
    """A SQL skeleton promoted to verified status after multiple curator accepts.

    Distinct from ``LearnedPattern`` (clarification-Q&A flavor) — verified patterns
    are SQL templates that have been independently confirmed across ≥3 sessions
    and become first-line candidates for future queries.
    """
    pattern_id: str             # vp_<hash>
    sql_skeleton: str           # canonical normalized SQL (literals stripped)
    exemplar_query: str         # canonical phrasing (most recent accepted)
    exemplar_sql: str           # full SQL with literals from the exemplar accept
    tables_used: List[str] = field(default_factory=list)
    accept_count: int = 0       # distinct curator-accept sessions backing this pattern
    consumer_uses: int = 0
    negative_signals: int = 0
    score: float = 0.0
    promoted_at: float = 0.0
    source_entry_ids: List[str] = field(default_factory=list)
    manual_promotion: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VerifiedPattern":
        return cls(**d)


# ---------------------------------------------------------------------------
# KYCKnowledgeStore
# ---------------------------------------------------------------------------

class KYCKnowledgeStore:
    """Thread-safe store for static knowledge entries and learned patterns."""

    def __init__(self, persist_path: Optional[str] = None):
        self.static_entries: List[KnowledgeEntry] = []
        self.learned_patterns: List[LearnedPattern] = []
        self.patterns: List[VerifiedPattern] = []
        self._lock = threading.Lock()
        self._persist_path = persist_path or self._default_persist_path()
        self._load_from_disk()

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _default_persist_path() -> str:
        env = os.getenv("GRAPH_CACHE_PATH", "").strip()
        if env:
            return os.path.join(env, "kyc_knowledge_store.json")
        if os.path.isdir(_DOCKER_CACHE_DIR):
            return os.path.join(_DOCKER_CACHE_DIR, "kyc_knowledge_store.json")
        os.makedirs(_LOCAL_CACHE_DIR, exist_ok=True)
        return os.path.join(_LOCAL_CACHE_DIR, "kyc_knowledge_store.json")

    # --------------------------------------------------------------- persist
    def _load_from_disk(self) -> None:
        if not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r") as f:
                data = json.load(f)
            self.learned_patterns = [
                LearnedPattern.from_dict(p) for p in data.get("learned_patterns", [])
            ]
            self.patterns = [
                VerifiedPattern.from_dict(p) for p in data.get("patterns", [])
            ]
            # Static entries are loaded fresh from docs at startup, but we
            # also restore any manually-added ones and accepted query sessions.
            for e in data.get("manual_entries", []):
                self.static_entries.append(KnowledgeEntry.from_dict(e))
            for e in data.get("session_entries", []):
                self.static_entries.append(KnowledgeEntry.from_dict(e))
            logger.info(
                "Knowledge store loaded: %d learned patterns, %d manual entries, %d session entries from %s",
                len(self.learned_patterns),
                len(data.get("manual_entries", [])),
                len(data.get("session_entries", [])),
                self._persist_path,
            )
        except Exception as exc:
            logger.warning("Could not load knowledge store from %s: %s", self._persist_path, exc)

    def save_to_disk(self) -> None:
        """Atomic write: tmp file → os.replace."""
        data = {
            "version": "1",
            "saved_at": time.time(),
            "learned_patterns": [p.to_dict() for p in self.learned_patterns],
            "patterns": [p.to_dict() for p in self.patterns],
            "manual_entries": [
                e.to_dict() for e in self.static_entries if e.source == "manual"
            ],
            "session_entries": [
                e.to_dict() for e in self.static_entries if e.source == "query_session"
            ],
        }
        tmp_path = self._persist_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, self._persist_path)
        except Exception as exc:
            logger.error("Failed to save knowledge store: %s", exc)

    # -------------------------------------------------------- static entries
    def add_static_entries(self, entries: List[KnowledgeEntry]) -> None:
        """Bulk-load static entries (from business docs). Deduplicates by id."""
        with self._lock:
            existing_ids: Set[str] = {e.id for e in self.static_entries}
            for entry in entries:
                if entry.id not in existing_ids:
                    self.static_entries.append(entry)
                    existing_ids.add(entry.id)

    def replace_entries_by_source(self, source: str, entries: List[KnowledgeEntry]) -> None:
        """Remove all entries with the given source and add new ones.

        Used by the LLM knowledge analyzer to swap in richer entries
        without duplicating alongside the old ones.
        """
        with self._lock:
            self.static_entries = [e for e in self.static_entries if e.source != source]
            existing_ids: Set[str] = {e.id for e in self.static_entries}
            for entry in entries:
                if entry.id not in existing_ids:
                    self.static_entries.append(entry)
                    existing_ids.add(entry.id)

    def add_manual_entry(self, content: str, category: str, metadata: Optional[Dict] = None) -> KnowledgeEntry:
        """Add a user-created knowledge entry and persist."""
        entry = KnowledgeEntry(
            id=str(uuid.uuid4()),
            source="manual",
            content=content,
            category=category,
            metadata=metadata or {},
        )
        with self._lock:
            self.static_entries.append(entry)
            self.save_to_disk()
        return entry

    def add_session_entry(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        """Persist a query_session entry. Stored alongside manual entries."""
        with self._lock:
            existing_ids: Set[str] = {e.id for e in self.static_entries}
            if entry.id not in existing_ids:
                self.static_entries.append(entry)
            self.save_to_disk()
        return entry

    def find_session_match(self, enriched_query: str, graph) -> Optional[KnowledgeEntry]:
        """Find a prior query_session whose original/enriched query matches the
        current input above SESSION_MATCH_THRESHOLD AND whose referenced tables
        all still exist in `graph`.

        Tiebreak: higher Jaccard score wins; on equal score, newer created_at.
        """
        if not enriched_query or not enriched_query.strip():
            return None

        query_tokens = _tokenize(enriched_query)
        if not query_tokens:
            return None

        with self._lock:
            best: Optional[KnowledgeEntry] = None
            best_score = -1.0
            best_created = -1.0
            for e in self.static_entries:
                if e.source != "query_session" or e.category != "query_session":
                    continue
                meta = e.metadata or {}
                hay = (meta.get("original_query", "") + " " + meta.get("enriched_query", "")).strip()
                if not hay:
                    continue
                score = _jaccard(query_tokens, _tokenize(hay))
                if score < SESSION_MATCH_THRESHOLD:
                    continue
                # Verify all referenced tables still exist.
                tables = meta.get("tables_used", []) or []
                if tables and not all(graph.get_node("Table", t) for t in tables):
                    continue
                created = float(meta.get("created_at", 0.0) or 0.0)
                if (score > best_score) or (score == best_score and created > best_created):
                    best = e
                    best_score = score
                    best_created = created
            return best

    def update_entry(self, entry_id: str, content: str, category: str, metadata: Optional[Dict] = None) -> bool:
        with self._lock:
            for e in self.static_entries:
                if e.id == entry_id:
                    e.content = content
                    e.category = category
                    if metadata is not None:
                        e.metadata = metadata
                    self.save_to_disk()
                    return True
        return False

    def delete_entry(self, entry_id: str) -> bool:
        with self._lock:
            before = len(self.static_entries)
            self.static_entries = [e for e in self.static_entries if e.id != entry_id]
            if len(self.static_entries) < before:
                self.save_to_disk()
                return True
        return False

    def search_entries(
        self,
        query: str = "",
        category: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[KnowledgeEntry]:
        """Filter + keyword search over static entries."""
        results = self.static_entries
        if category:
            results = [e for e in results if e.category == category]
        if source:
            results = [e for e in results if e.source == source]
        if query:
            q_lower = query.lower()
            results = [e for e in results if q_lower in e.content.lower()]
        return results

    # ------------------------------------------------------ learned patterns
    def record_pattern(
        self,
        question: str,
        answer: str,
        user_query: str,
        sql: str = "",
        confidence: float = 0.5,
        category: str = "filter_value",
        user_confirmed: bool = False,
        tags: Optional[List[str]] = None,
    ) -> LearnedPattern:
        """Record a new learned pattern or update an existing one if similar."""
        with self._lock:
            existing = self._find_matching_pattern_unlocked(question, user_query)
            now = time.time()
            if existing and existing.confidence <= confidence:
                # Update existing pattern
                existing.answer = answer
                existing.confidence = max(existing.confidence, confidence)
                existing.last_used_at = now
                existing.use_count += 1
                existing.resulting_sql = sql or existing.resulting_sql
                existing.user_confirmed = user_confirmed or existing.user_confirmed
                self._prune_if_needed()
                self.save_to_disk()
                return existing

            pattern = LearnedPattern(
                id=str(uuid.uuid4()),
                question_pattern=question,
                answer=answer,
                original_user_query=user_query,
                resulting_sql=sql,
                user_confirmed=user_confirmed,
                confidence=confidence,
                category=category,
                created_at=now,
                last_used_at=now,
                use_count=1,
                tags=tags or [],
            )
            self.learned_patterns.append(pattern)
            self._prune_if_needed()
            self.save_to_disk()
            return pattern

    def bump_confidence(self, pattern_id: str, delta: float = 0.1) -> bool:
        """Increase confidence of a pattern (capped at 1.0)."""
        with self._lock:
            for p in self.learned_patterns:
                if p.id == pattern_id:
                    p.confidence = min(1.0, p.confidence + delta)
                    p.last_used_at = time.time()
                    p.use_count += 1
                    self.save_to_disk()
                    return True
        return False

    def find_matching_pattern(self, question: str, user_query: str) -> Optional[LearnedPattern]:
        """Thread-safe pattern lookup."""
        with self._lock:
            return self._find_matching_pattern_unlocked(question, user_query)

    def _find_matching_pattern_unlocked(self, question: str, user_query: str) -> Optional[LearnedPattern]:
        """Jaccard token similarity on (question + user_query). Threshold >= 0.5."""
        query_tokens = _tokenize(question + " " + user_query)
        if not query_tokens:
            return None

        best: Optional[LearnedPattern] = None
        best_score = 0.0

        for p in self.learned_patterns:
            pattern_tokens = _tokenize(p.question_pattern + " " + p.original_user_query)
            if not pattern_tokens:
                continue
            score = _jaccard(query_tokens, pattern_tokens)
            if score >= 0.5 and score > best_score:
                best_score = score
                best = p

        return best

    def update_pattern(self, pattern_id: str, **updates) -> bool:
        with self._lock:
            for p in self.learned_patterns:
                if p.id == pattern_id:
                    for k, v in updates.items():
                        if hasattr(p, k):
                            setattr(p, k, v)
                    self.save_to_disk()
                    return True
        return False

    def delete_pattern(self, pattern_id: str) -> bool:
        with self._lock:
            before = len(self.learned_patterns)
            self.learned_patterns = [p for p in self.learned_patterns if p.id != pattern_id]
            if len(self.learned_patterns) < before:
                self.save_to_disk()
                return True
        return False

    # ---------------------------------------------------- verified patterns
    def add_pattern(self, pattern: VerifiedPattern) -> None:
        """Insert a verified pattern, replacing any existing entry with the same id."""
        with self._lock:
            self.patterns = [p for p in self.patterns if p.pattern_id != pattern.pattern_id]
            self.patterns.append(pattern)
            self.save_to_disk()

    def find_verified_pattern(self, query: str, graph) -> Optional[VerifiedPattern]:
        """Return the highest-scoring verified pattern whose exemplar_query
        Jaccard-matches ``query`` ≥ SESSION_MATCH_THRESHOLD AND whose
        ``tables_used`` all still exist in the live graph.

        Verify-on-read: tables that have been dropped from the graph cause the
        pattern to be skipped (stale schema reference).
        """
        if not query or not query.strip():
            return None
        qtoks = _tokenize(query)
        if not qtoks:
            return None

        best: Optional[VerifiedPattern] = None
        best_score = -1.0
        with self._lock:
            for p in self.patterns:
                if not p.exemplar_query:
                    continue
                jacc = _jaccard(qtoks, _tokenize(p.exemplar_query))
                if jacc < SESSION_MATCH_THRESHOLD:
                    continue
                if not all(graph.get_node("Table", t) for t in p.tables_used):
                    continue
                if p.score > best_score:
                    best = p
                    best_score = p.score
        return best

    # ----------------------------------------------------------- pruning
    def _prune_if_needed(self) -> None:
        """Prune learned patterns when count exceeds PRUNE_TRIGGER.

        Score formula:
            score = confidence * 0.4 + recency * 0.3 + frequency * 0.3
        where:
            recency  = 1.0 - min(1.0, days_since_last_use / PRUNE_RECENCY_DAYS)
            frequency = min(1.0, use_count / 10)

        Patterns with confidence >= 0.9 AND use_count >= 5 are protected.
        """
        if len(self.learned_patterns) < PRUNE_TRIGGER:
            return

        now = time.time()
        protected: List[LearnedPattern] = []
        candidates: List[tuple] = []  # (score, pattern)

        for p in self.learned_patterns:
            if p.confidence >= 0.9 and p.use_count >= 5:
                protected.append(p)
                continue
            days_since = (now - p.last_used_at) / 86400
            recency = 1.0 - min(1.0, days_since / PRUNE_RECENCY_DAYS)
            frequency = min(1.0, p.use_count / 10.0)
            score = p.confidence * 0.4 + recency * 0.3 + frequency * 0.3
            candidates.append((score, p))

        candidates.sort(key=lambda x: x[0], reverse=True)
        keep_count = max(0, PRUNE_KEEP - len(protected))
        kept = [p for _, p in candidates[:keep_count]]

        old_count = len(self.learned_patterns)
        self.learned_patterns = protected + kept
        pruned = old_count - len(self.learned_patterns)
        if pruned > 0:
            logger.info("Pruned %d learned patterns (%d → %d)", pruned, old_count, len(self.learned_patterns))

    # ----------------------------------------------------------- metrics
    def get_metrics(self) -> Dict[str, Any]:
        """Return summary metrics for the tuning UI."""
        with self._lock:
            total_patterns = len(self.learned_patterns)
            total_entries = len(self.static_entries)
            categories: Dict[str, int] = {}
            sources: Dict[str, int] = {}
            avg_confidence = 0.0
            auto_answered = 0

            for p in self.learned_patterns:
                categories[p.category] = categories.get(p.category, 0) + 1
                if p.confidence >= 0.6:
                    auto_answered += 1
                avg_confidence += p.confidence

            for e in self.static_entries:
                sources[e.source] = sources.get(e.source, 0) + 1

            return {
                "total_learned_patterns": total_patterns,
                "total_static_entries": total_entries,
                "avg_confidence": round(avg_confidence / total_patterns, 3) if total_patterns else 0,
                "auto_answer_eligible": auto_answered,
                "pattern_categories": categories,
                "entry_sources": sources,
            }

    # ----------------------------------------------------------- export/import
    def export_json(self) -> Dict[str, Any]:
        """Full export for download."""
        with self._lock:
            return {
                "version": "1",
                "exported_at": time.time(),
                "static_entries": [e.to_dict() for e in self.static_entries],
                "learned_patterns": [p.to_dict() for p in self.learned_patterns],
            }

    def import_json(self, data: Dict[str, Any], mode: str = "merge") -> Dict[str, int]:
        """Import from JSON. mode='merge' (add new) or 'replace' (overwrite all)."""
        with self._lock:
            counts = {"entries_added": 0, "patterns_added": 0}

            if mode == "replace":
                self.static_entries = [
                    KnowledgeEntry.from_dict(e) for e in data.get("static_entries", [])
                ]
                self.learned_patterns = [
                    LearnedPattern.from_dict(p) for p in data.get("learned_patterns", [])
                ]
                counts["entries_added"] = len(self.static_entries)
                counts["patterns_added"] = len(self.learned_patterns)
            else:
                existing_entry_ids = {e.id for e in self.static_entries}
                for e_dict in data.get("static_entries", []):
                    entry = KnowledgeEntry.from_dict(e_dict)
                    if entry.id not in existing_entry_ids:
                        self.static_entries.append(entry)
                        existing_entry_ids.add(entry.id)
                        counts["entries_added"] += 1

                existing_pattern_ids = {p.id for p in self.learned_patterns}
                for p_dict in data.get("learned_patterns", []):
                    pattern = LearnedPattern.from_dict(p_dict)
                    if pattern.id not in existing_pattern_ids:
                        self.learned_patterns.append(pattern)
                        existing_pattern_ids.add(pattern.id)
                        counts["patterns_added"] += 1

            self._prune_if_needed()
            self.save_to_disk()
            return counts


# ---------------------------------------------------------------------------
# Token helpers (Jaccard similarity)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> Set[str]:
    """Lowercase word tokenization, removing common stop words."""
    _STOP = {"the", "a", "an", "is", "are", "was", "were", "do", "does", "did",
             "to", "of", "in", "for", "on", "with", "at", "by", "from", "this",
             "that", "it", "and", "or", "but", "not", "as", "be", "has", "have",
             "had", "what", "which", "who", "whom", "how", "when", "where", "why",
             "i", "you", "we", "they", "he", "she", "my", "your", "our", "their"}
    tokens = set()
    for word in text.lower().split():
        # Strip punctuation
        cleaned = "".join(c for c in word if c.isalnum() or c == "_")
        if cleaned and cleaned not in _STOP:
            tokens.add(cleaned)
    return tokens


def _jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity coefficient."""
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0
