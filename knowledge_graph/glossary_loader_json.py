"""
Business Glossary Loader
=========================
Loads a KYC domain business glossary from a JSON file and ingests
BusinessTerm nodes and MAPS_TO edges into Neo4j.

JSON schema (data/kyc_glossary.json)::

    [
      {
        "term": "Customer Due Diligence",
        "definition": "...",
        "aliases": ["CDD", "due diligence"],
        "domain": "KYC",
        "sensitivity_level": "INTERNAL",
        "mappings": [
          {
            "fqn": "KYC.CUSTOMERS",
            "label": "Table",
            "confidence": 1.0,
            "mapping_type": "manual"
          },
          ...
        ]
      }
    ]
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from neo4j import Session

from knowledge_graph.models import BusinessTermNode, MapsToRel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cypher
# ---------------------------------------------------------------------------

_UPSERT_BUSINESS_TERM = """
UNWIND $rows AS row
MERGE (bt:BusinessTerm {term: row.term})
SET bt.definition       = row.definition,
    bt.aliases          = row.aliases,
    bt.domain           = row.domain,
    bt.sensitivity_level = row.sensitivity_level,
    bt.last_updated     = timestamp()
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
# Loader
# ---------------------------------------------------------------------------

class GlossaryLoader:
    """
    Loads the KYC business glossary into Neo4j.

    Usage::

        loader = GlossaryLoader(session, glossary_path="data/kyc_glossary.json")
        loader.load()
    """

    def __init__(self, session: Session, glossary_path: str = "data/kyc_glossary.json") -> None:
        self._session = session
        self._path = Path(glossary_path)

    def load(self) -> Dict[str, int]:
        """Read the glossary file and upsert all terms and mappings."""
        if not self._path.exists():
            logger.warning("Glossary file not found: %s — skipping", self._path)
            return {"terms": 0, "mappings": 0}

        with self._path.open(encoding="utf-8") as fh:
            glossary: List[Dict[str, Any]] = json.load(fh)

        term_rows: List[Dict[str, Any]] = []
        mapping_rows: List[Dict[str, Any]] = []

        for entry in glossary:
            term_node = BusinessTermNode(
                term=entry["term"],
                definition=entry.get("definition", ""),
                aliases=entry.get("aliases", []),
                domain=entry.get("domain", "KYC"),
                sensitivity_level=entry.get("sensitivity_level", "INTERNAL"),
            )
            term_rows.append(term_node.to_cypher_params())

            for mapping in entry.get("mappings", []):
                rel = MapsToRel(
                    term=entry["term"],
                    target_fqn=mapping["fqn"].upper(),
                    target_label=mapping.get("label", "Table"),
                    confidence=float(mapping.get("confidence", 1.0)),
                    mapping_type=mapping.get("mapping_type", "manual"),
                )
                mapping_rows.append(rel.to_cypher_params())

        if term_rows:
            self._session.run(_UPSERT_BUSINESS_TERM, rows=term_rows)
        if mapping_rows:
            self._session.run(_UPSERT_MAPS_TO, rows=mapping_rows)

        logger.info(
            "GlossaryLoader: ingested %d terms, %d mappings",
            len(term_rows), len(mapping_rows),
        )
        return {"terms": len(term_rows), "mappings": len(mapping_rows)}

    @staticmethod
    def load_raw(glossary_path: str) -> List[Dict[str, Any]]:
        """Return the raw parsed glossary JSON for inspection."""
        path = Path(glossary_path)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)