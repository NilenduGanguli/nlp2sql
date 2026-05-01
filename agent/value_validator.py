"""
Literal-grounding validator (Phase 2 / Layer 3)
================================================
Reads `WHERE col = 'literal'` / `col IN (...)` triples out of a generated
SQL statement and checks them against the precomputed ValueCache.

Three outcomes per literal:

1. Exact match in cached values → silently pass.
2. Confident fuzzy match (case-insensitive equal, unique prefix, unique
   token-contains, or difflib ratio ≥ threshold) → emit a *Rewrite* the
   caller can apply to the SQL.
3. Otherwise → emit a *Finding*; the caller is expected to push it into
   ``state["validation_errors"]`` so the existing retry path regenerates
   the SQL with the explicit allowed-value list.

The module does NOT mutate the SQL itself; ``apply_rewrites`` is provided
as a convenience.

Coverage (deliberately narrow):
- Validates: ``col = 'lit'``, ``col != 'lit'``, ``col IN ('a','b',…)``,
  HAVING the same way as WHERE.
- Skips: LIKE, BETWEEN, IS NULL, sub-query / bind-variable RHS, EXISTS,
  CASE WHEN literals, columns whose cache entry is too_many or error.
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_FUZZY_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WhereLiteral:
    """One (alias, column, operator, literal) triple extracted from SQL."""

    alias: str       # alias as written in SQL ('A', 'a', '' if unqualified)
    column: str      # column name as written in SQL (uppercased)
    operator: str    # '=', '!=', or 'IN'
    literal: str     # literal value as written, with quotes stripped


@dataclass
class Rewrite:
    """A confident auto-fix the caller can apply to the SQL."""

    table_fqn: str
    column: str
    original: str
    replacement: str
    reason: str


@dataclass
class Finding:
    """A literal that does not match any cached value and is not fuzzy-fixable."""

    table_fqn: str
    column: str
    bad_literal: str
    allowed_values: List[str]


# ---------------------------------------------------------------------------
# Fuzzy match
# ---------------------------------------------------------------------------

def fuzzy_score(literal, cached) -> float:
    """
    Return a similarity score in [0.0, 1.0] for *literal* vs *cached*.

    Layered rules — first hit wins, scores chosen so caller's threshold of
    0.85 admits all the desirable mappings without admitting random noise:

    | rule                                                   | score |
    |--------------------------------------------------------|-------|
    | case-insensitive equal after strip                     | 1.00  |
    | one is a strict prefix of the other (case-insensitive,
    |   shorter is at least 1 char and ≤ 0.5 of the longer
    |   length OR the longer string starts with shorter+'_'
    |   or shorter+' ')                                       | 0.95  |
    | cached appears as a whole-word token of literal
    |   (split on space/_/-)                                  | 0.90  |
    | difflib.SequenceMatcher ratio (case-insensitive)        | ratio |
    | otherwise                                               | 0.00  |
    """
    a = str(literal).strip()
    b = str(cached).strip()
    if not a or not b:
        return 0.0

    al, bl = a.lower(), b.lower()
    if al == bl:
        return 1.0

    # Prefix rule — but require the shorter one to be substantially shorter
    # (or to end on a word boundary) so 'ACTIVE' doesn't fuzzy-match 'A'
    # alongside 'ARCHIVED'. We let the *caller* decide ambiguity by counting
    # how many cached values clear the threshold.
    short, long_ = (al, bl) if len(al) <= len(bl) else (bl, al)
    if len(short) >= 1 and long_.startswith(short):
        # Either the short string is a clear abbreviation
        # (long is more than twice as long) OR the long string has a word
        # break right after the prefix.
        if len(long_) >= 2 * len(short) or (
            len(long_) > len(short) and long_[len(short)] in (" ", "_", "-")
        ):
            return 0.95

    # Token-contains: cached appears as a whole token in the literal (or vv).
    def _tokens(s: str) -> List[str]:
        return [t for t in re.split(r"[\s_\-]+", s) if t]

    a_tokens = set(_tokens(al))
    b_tokens = set(_tokens(bl))
    if a_tokens and (bl in a_tokens or any(t == bl for t in a_tokens)):
        return 0.90
    if b_tokens and (al in b_tokens or any(t == al for t in b_tokens)):
        return 0.90

    ratio = difflib.SequenceMatcher(None, al, bl).ratio()
    return ratio


def _best_match(
    literal,
    cached_values: List[str],
    threshold: float,
) -> Tuple[Optional[str], float, int]:
    """
    Return ``(best_match, best_score, n_at_or_above_threshold)``.

    * ``best_match`` is the highest-scoring cached value, or None if all
      scores are 0.
    * ``best_score`` is its score.
    * ``n_at_or_above_threshold`` is how many cached values score >= threshold —
      the caller uses it to detect ambiguity (>= 2 means "not unambiguous").
    """
    best_match: Optional[str] = None
    best_score: float = 0.0
    above: int = 0
    for v in cached_values:
        s = fuzzy_score(literal, v)
        if s >= threshold:
            above += 1
        if s > best_score:
            best_score = s
            best_match = v
    return best_match, best_score, above


# ---------------------------------------------------------------------------
# SQL literal extraction
# ---------------------------------------------------------------------------

def extract_where_literals(sql: str) -> List[WhereLiteral]:
    """
    Extract (alias, column, op, literal) triples from WHERE/HAVING.

    Uses sqlglot when available; falls back to an empty list when sqlglot
    is missing or parsing fails (no false positives — this validator can
    only fix or flag, never break a correct query).
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        logger.debug("sqlglot unavailable — extract_where_literals returns []")
        return []

    try:
        statement = sqlglot.parse_one(sql, read="oracle")
    except Exception as exc:
        logger.debug("extract_where_literals: parse failed: %s", exc)
        return []
    if statement is None:
        return []

    literals: List[WhereLiteral] = []

    def _capture_eq(node, op_label: str) -> None:
        col = node.this
        rhs = node.expression
        if not isinstance(col, exp.Column) or not isinstance(rhs, exp.Literal):
            return
        if not rhs.is_string and not _is_numeric_literal(rhs):
            return
        alias = (col.table or "").upper()
        col_name = (col.name or "").upper()
        if not col_name:
            return
        literals.append(WhereLiteral(
            alias=alias,
            column=col_name,
            operator=op_label,
            literal=str(rhs.this if rhs.is_string else rhs.this),
        ))

    # Walk WHERE and HAVING subtrees only — avoids picking up SELECT-list literals.
    candidate_clauses = []
    for clause_cls in (exp.Where, exp.Having):
        candidate_clauses.extend(statement.find_all(clause_cls))

    for clause in candidate_clauses:
        # = and !=
        for eq in clause.find_all(exp.EQ):
            _capture_eq(eq, "=")
        for ne in clause.find_all(exp.NEQ):
            _capture_eq(ne, "!=")
        # IN (...)
        for in_expr in clause.find_all(exp.In):
            col = in_expr.this
            if not isinstance(col, exp.Column):
                continue
            alias = (col.table or "").upper()
            col_name = (col.name or "").upper()
            if not col_name:
                continue
            for item in in_expr.expressions:
                if not isinstance(item, exp.Literal):
                    continue
                if not item.is_string and not _is_numeric_literal(item):
                    continue
                literals.append(WhereLiteral(
                    alias=alias,
                    column=col_name,
                    operator="IN",
                    literal=str(item.this),
                ))

    return literals


def _is_numeric_literal(lit) -> bool:
    """sqlglot Literal.is_string is False for numerics; check it can be parsed."""
    try:
        float(lit.this)
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Alias → FQN resolution
# ---------------------------------------------------------------------------

def _build_alias_to_fqn(sql: str) -> Dict[str, str]:
    """Return a mapping of alias (UPPER) → FQN ('SCHEMA.TABLE') for the SQL."""
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return {}
    try:
        statement = sqlglot.parse_one(sql, read="oracle")
    except Exception:
        return {}
    if statement is None:
        return {}

    cte_names = {cte.alias.upper() for cte in statement.find_all(exp.CTE) if cte.alias}
    out: Dict[str, str] = {}
    for table_expr in statement.find_all(exp.Table):
        t_name = (table_expr.name or "").upper()
        if not t_name or t_name in cte_names:
            continue
        t_schema = (table_expr.db or "").upper()
        if not t_schema:
            continue
        fqn = f"{t_schema}.{t_name}"
        alias = (table_expr.alias or "").upper()
        out[alias or t_name] = fqn
        if alias:
            out[t_name] = fqn  # bare-table reference also resolves
    return out


# ---------------------------------------------------------------------------
# Top-level validator
# ---------------------------------------------------------------------------

def validate_where_literals(
    sql: str,
    value_cache,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> Tuple[List[Finding], List[Rewrite]]:
    """
    Validate every WHERE/HAVING literal against the cached values.

    Parameters
    ----------
    sql
        The generated SQL.
    value_cache
        A populated :class:`~knowledge_graph.value_cache.ValueCache` or None.
        When None (or empty), the validator is a no-op.
    fuzzy_threshold
        Minimum score required for an auto-fix rewrite (default 0.85).

    Returns
    -------
    (findings, rewrites)
        ``findings``  — literals that need a regenerate-with-hint retry.
        ``rewrites``  — confident auto-fixes the caller can apply via
                        :func:`apply_rewrites`.
    """
    if value_cache is None or not sql:
        return [], []

    literals = extract_where_literals(sql)
    if not literals:
        return [], []

    alias_to_fqn = _build_alias_to_fqn(sql)
    findings: List[Finding] = []
    rewrites: List[Rewrite] = []

    for lit in literals:
        if not lit.alias:
            continue   # cannot resolve unqualified column to a graph FQN
        fqn = alias_to_fqn.get(lit.alias)
        if not fqn:
            continue
        try:
            schema, table = fqn.split(".", 1)
        except ValueError:
            continue
        entry = value_cache.get(schema, table, lit.column)
        if entry is None:
            continue                       # column not in cache (not a filter candidate)
        if entry.too_many or entry.error or not entry.values:
            continue                       # no ground truth — let it pass

        if lit.literal in entry.values:
            continue                       # exact hit

        match, score, above = _best_match(lit.literal, entry.values, fuzzy_threshold)
        if match is not None and score >= fuzzy_threshold and above == 1:
            rewrites.append(Rewrite(
                table_fqn=fqn,
                column=lit.column,
                original=lit.literal,
                replacement=match,
                reason=_reason_for_score(score),
            ))
        else:
            findings.append(Finding(
                table_fqn=fqn,
                column=lit.column,
                bad_literal=lit.literal,
                allowed_values=list(entry.values),
            ))

    return findings, rewrites


def _reason_for_score(score: float) -> str:
    if score >= 1.0:
        return "case-insensitive equal"
    if score >= 0.95:
        return "unique prefix match"
    if score >= 0.9:
        return "unique token match"
    return f"difflib ratio={score:.2f}"


# ---------------------------------------------------------------------------
# Apply rewrites
# ---------------------------------------------------------------------------

def apply_rewrites(sql: str, rewrites: List[Rewrite]) -> str:
    """
    Apply *rewrites* to *sql* by swapping each ``original`` quoted literal
    with its ``replacement``.

    Implementation is intentionally conservative: only quoted literals that
    match the *exact* original string (within single quotes) are swapped.
    A literal embedded inside a longer quoted string is left alone.
    """
    out = sql
    for rw in rewrites:
        # Single-quoted exact-match: 'original' (Oracle escapes ' as '' but
        # we only swap simple atomic literals — the regex requires that the
        # original is the entire content between the quotes).
        pattern = "'" + re.escape(rw.original) + "'"
        # Match the exact quoted form, not embedded inside a longer string.
        out = re.sub(pattern, "'" + rw.replacement.replace("'", "''") + "'", out)
        # Numeric (unquoted): match as a standalone token if original is numeric.
        if _looks_numeric(rw.original):
            out = re.sub(
                r"(?<![\w.'])" + re.escape(rw.original) + r"(?![\w.'])",
                rw.replacement,
                out,
            )
    return out


def _looks_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False
