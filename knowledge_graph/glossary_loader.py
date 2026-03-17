"""
Inferred Business Glossary Builder
====================================
Derives BusinessTerm nodes and MAPS_TO edges directly from Oracle column and
table metadata already captured in OracleMetadata — no external file required.

Sources used
------------
1. DBA_COL_COMMENTS / ALL_COL_COMMENTS  — column-level business definitions
   (``ColumnNode.comments``)
2. DBA_TAB_COMMENTS / ALL_TAB_COMMENTS  — table-level business descriptions
   (``TableNode.comments``)
3. ``ColumnNode.sample_values``          — enriches definitions with a valid-
   value enumeration for low-cardinality / categorical columns
4. Column name (humanized UPPER_SNAKE → Title Case) — term label when no
   data-dictionary comment is present

Confidence scoring
------------------
  0.95  column has a data-dictionary comment  (DBA_COL_COMMENTS)
  0.80  table has a data-dictionary comment   (DBA_TAB_COMMENTS)
  0.65  term inferred from column name + sample values (no comment)
  0.50  term inferred from column name alone

Sensitivity inference
---------------------
Column names matching known PII / financial keyword patterns are
automatically tagged RESTRICTED or CONFIDENTIAL; all others default to
INTERNAL.

Deduplication
-------------
Multiple columns may share the same humanized term name (e.g. CUSTOMER_ID
exists in CUSTOMERS, ACCOUNTS, KYC_REVIEWS …).  The term definition is
kept from the highest-confidence source; a MAPS_TO edge is created for
every matching column or table.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from neo4j import Session

from knowledge_graph.oracle_extractor import OracleMetadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensitivity keyword sets  (matched against individual words in a column name)
# ---------------------------------------------------------------------------

_RESTRICTED_KEYWORDS: frozenset = frozenset({
    "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL",
    "SSN", "NIN", "TIN", "PASSPORT", "NRIC",
    "DOB", "BIRTH",
})

_CONFIDENTIAL_KEYWORDS: frozenset = frozenset({
    "SALARY", "WAGE", "INCOME", "COMPENSATION",
    "BALANCE", "AMOUNT", "CREDIT", "DEBIT",
    "RISK", "RATING",
    "EXPIRY", "EXPIRATION",
    "NATIONALITY", "RACE", "ETHNICITY", "RELIGION",
    "MEDICAL", "HEALTH",
    "PEP", "SANCTION",
})

# Column names that are purely structural — skip as standalone business terms
_SKIP_PURE_NAMES: frozenset = frozenset({
    "ID", "SEQ", "NUM", "NO", "FLAG", "IND", "YN", "FLG",
    "CREATED", "UPDATED", "MODIFIED", "DELETED",
    "CREATED_AT", "UPDATED_AT", "MODIFIED_AT", "DELETED_AT",
    "CREATED_BY", "UPDATED_BY", "MODIFIED_BY", "DELETED_BY",
    "ROW_VERSION", "ROWVERSION", "VERSION",
})

# Abbreviations that should remain uppercase in the humanized term
_ABBREVIATIONS: frozenset = frozenset({
    "ID", "FK", "PK", "SSN", "DOB", "DOC", "KYC", "PEP", "AML",
    "NIN", "TIN", "CDD", "EDD", "SAR", "CTR", "UUID", "REF",
    "NO", "UK", "US", "EU", "ISO",
})

# Categorical column: list sample values in definition if num_distinct ≤ this
_CATEGORICAL_THRESHOLD = 30
_MAX_ENUM_VALUES = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _humanize(snake_name: str) -> str:
    """Convert UPPER_SNAKE_CASE column / table name to a business term label.

    Examples::

        RISK_RATING       → "Risk Rating"
        CUSTOMER_ID       → "Customer ID"
        DOB               → "DOB"
        ACCOUNT_MANAGER_ID → "Account Manager ID"
    """
    words = snake_name.upper().split("_")
    result = []
    for word in words:
        if not word:
            continue
        if word in _ABBREVIATIONS:
            result.append(word)
        else:
            result.append(word.capitalize())
    return " ".join(result)


def _infer_sensitivity(col_name: str) -> str:
    """Return RESTRICTED | CONFIDENTIAL | INTERNAL based on column name tokens."""
    tokens = set(col_name.upper().split("_"))
    if tokens & _RESTRICTED_KEYWORDS:
        return "RESTRICTED"
    if tokens & _CONFIDENTIAL_KEYWORDS:
        return "CONFIDENTIAL"
    return "INTERNAL"


def _build_definition(
    col_name: str,
    comment: Optional[str],
    sample_values: Optional[List[Any]],
    num_distinct: Optional[int],
    table_comment: Optional[str],
    table_fqn: str,
) -> tuple[str, float]:
    """
    Return (definition_text, confidence) for a column-level business term.
    """
    if comment and comment.strip():
        definition = comment.strip()
        confidence = 0.95
    elif table_comment and table_comment.strip():
        # Synthetic: "<HumanName> in <table description>"
        definition = f"{_humanize(col_name)} in {table_comment.strip().lower()}"
        confidence = 0.65
    else:
        definition = f"{_humanize(col_name)} in {table_fqn}"
        confidence = 0.50

    # Augment with enumerated valid values for categorical columns
    if (
        sample_values
        and num_distinct is not None
        and 1 < num_distinct <= _CATEGORICAL_THRESHOLD
    ):
        vals = ", ".join(str(v) for v in sample_values[:_MAX_ENUM_VALUES])
        definition += f". Valid values: {vals}."
        confidence = max(confidence, 0.65)  # sample values add signal

    return definition, confidence


# ---------------------------------------------------------------------------
# Cypher
# ---------------------------------------------------------------------------

_UPSERT_BUSINESS_TERM = """
UNWIND $rows AS row
MERGE (bt:BusinessTerm {term: row.term})
SET bt.definition        = row.definition,
    bt.aliases           = row.aliases,
    bt.domain            = row.domain,
    bt.sensitivity_level = row.sensitivity_level,
    bt.last_updated      = timestamp()
"""

_UPSERT_MAPS_TO = """
UNWIND $rows AS row
MATCH (bt:BusinessTerm {term: row.term})
MATCH (target {fqn: row.target_fqn})
MERGE (bt)-[m:MAPS_TO {target_fqn: row.target_fqn}]->(target)
SET m.confidence    = row.confidence,
    m.mapping_type  = row.mapping_type
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class InferredGlossaryBuilder:
    """
    Builds BusinessTerm nodes and MAPS_TO edges by mining Oracle metadata.

    No external glossary file is needed — terms are inferred from:
    * ``ColumnNode.comments``   (DBA_COL_COMMENTS)
    * ``TableNode.comments``    (DBA_TAB_COMMENTS)
    * ``ColumnNode.sample_values`` for categorical columns
    * Humanized column / table names when no comment is available

    Usage::

        builder = InferredGlossaryBuilder(session)
        stats = builder.build(oracle_metadata)
        # stats == {"terms": N, "mappings": M}
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(self, metadata: OracleMetadata) -> Dict[str, int]:
        """
        Infer all business terms from *metadata* and upsert them into Neo4j.

        Returns a dict with ``terms`` (distinct BusinessTerm nodes written)
        and ``mappings`` (MAPS_TO edges written).
        """
        table_by_fqn = {t.fqn: t for t in metadata.tables}

        # term_defs: term_label → best-definition params seen so far
        term_defs: Dict[str, Dict[str, Any]] = {}
        mapping_rows: List[Dict[str, Any]] = []

        # ---- Phase 1: column-level terms ---------------------------------
        for col in metadata.columns:
            col_upper = col.name.upper()

            # Skip purely structural columns that carry no business meaning
            if col_upper in _SKIP_PURE_NAMES:
                continue

            term_label = _humanize(col.name)
            if not term_label:
                continue

            table = table_by_fqn.get(col.table_fqn)
            table_comment = table.comments if table else None

            definition, confidence = _build_definition(
                col_name=col.name,
                comment=col.comments,
                sample_values=col.sample_values,
                num_distinct=col.num_distinct,
                table_comment=table_comment,
                table_fqn=col.table_fqn,
            )

            sensitivity = _infer_sensitivity(col.name)
            # Infer domain from schema name (first segment of fqn)
            domain = col.schema.upper() if col.schema else "UNKNOWN"

            # Keep the highest-confidence definition for this term
            existing = term_defs.get(term_label)
            if existing is None or confidence > existing["confidence"]:
                term_defs[term_label] = {
                    "term": term_label,
                    "definition": definition,
                    # aliases: original snake_case name variants
                    "aliases": list({
                        col.name.lower(),
                        col.name.upper(),
                        col.name.replace("_", " ").title(),
                    }),
                    "domain": domain,
                    "sensitivity_level": sensitivity,
                    "confidence": confidence,
                }

            mapping_rows.append({
                "term": term_label,
                "target_fqn": col.fqn,
                "confidence": confidence,
                "mapping_type": "inferred",
            })

        # ---- Phase 2: table-level terms (only if comment present) --------
        for table in metadata.tables:
            if not table.comments or not table.comments.strip():
                continue

            term_label = _humanize(table.name)
            if not term_label:
                continue

            confidence = 0.80
            definition = table.comments.strip()
            domain = table.schema.upper() if table.schema else "UNKNOWN"

            existing = term_defs.get(term_label)
            if existing is None or confidence > existing["confidence"]:
                term_defs[term_label] = {
                    "term": term_label,
                    "definition": definition,
                    "aliases": list({
                        table.name.lower(),
                        table.name.replace("_", " ").title(),
                    }),
                    "domain": domain,
                    "sensitivity_level": "INTERNAL",
                    "confidence": confidence,
                }

            mapping_rows.append({
                "term": term_label,
                "target_fqn": table.fqn,
                "confidence": confidence,
                "mapping_type": "inferred",
            })

        # ---- Upsert to Neo4j ---------------------------------------------
        # Strip internal confidence key before writing (not a graph property)
        term_rows = [
            {k: v for k, v in params.items() if k != "confidence"}
            for params in term_defs.values()
        ]

        if term_rows:
            for i in range(0, len(term_rows), 500):
                self._session.run(_UPSERT_BUSINESS_TERM, rows=term_rows[i:i + 500])

        if mapping_rows:
            for i in range(0, len(mapping_rows), 500):
                self._session.run(_UPSERT_MAPS_TO, rows=mapping_rows[i:i + 500])

        logger.info(
            "InferredGlossaryBuilder: %d terms, %d MAPS_TO edges",
            len(term_rows), len(mapping_rows),
        )
        return {"terms": len(term_rows), "mappings": len(mapping_rows)}
