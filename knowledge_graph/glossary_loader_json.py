"""
Business Glossary Loader (JSON)
================================
Loads a KYC domain business glossary from a JSON file and ingests
BusinessTerm nodes and MAPS_TO edges into the in-memory KnowledgeGraph.

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

from knowledge_graph.graph_store import KnowledgeGraph
from knowledge_graph.models import BusinessTermNode, MapsToRel

logger = logging.getLogger(__name__)


class GlossaryLoader:
    """
    Loads the KYC business glossary from a JSON file into the KnowledgeGraph.

    Usage::

        loader = GlossaryLoader(graph, glossary_path="data/kyc_glossary.json")
        loader.load()
    """

    def __init__(self, graph: KnowledgeGraph, glossary_path: str = "data/kyc_glossary.json") -> None:
        self._graph = graph
        self._path = Path(glossary_path)

    def load(self) -> Dict[str, int]:
        """Read the glossary file and upsert all terms and mappings into the graph."""
        if not self._path.exists():
            logger.warning("Glossary file not found: %s — skipping", self._path)
            return {"terms": 0, "mappings": 0}

        with self._path.open(encoding="utf-8") as fh:
            glossary: List[Dict[str, Any]] = json.load(fh)

        mapping_count = 0

        for entry in glossary:
            term_node = BusinessTermNode(
                term=entry["term"],
                definition=entry.get("definition", ""),
                aliases=entry.get("aliases", []),
                domain=entry.get("domain", "KYC"),
                sensitivity_level=entry.get("sensitivity_level", "INTERNAL"),
            )
            # Upsert BusinessTerm node
            self._graph.merge_node("BusinessTerm", term_node.term, term_node.to_cypher_params())

            for mapping in entry.get("mappings", []):
                rel = MapsToRel(
                    term=entry["term"],
                    target_fqn=mapping["fqn"].upper(),
                    target_label=mapping.get("label", "Table"),
                    confidence=float(mapping.get("confidence", 1.0)),
                    mapping_type=mapping.get("mapping_type", "manual"),
                )
                self._graph.merge_edge(
                    "MAPS_TO",
                    rel.term,
                    rel.target_fqn,
                    confidence=rel.confidence,
                    mapping_type=rel.mapping_type,
                )
                mapping_count += 1

        term_count = len(glossary)
        logger.info(
            "GlossaryLoader: ingested %d terms, %d mappings",
            term_count, mapping_count,
        )
        return {"terms": term_count, "mappings": mapping_count}

    @staticmethod
    def load_raw(glossary_path: str) -> List[Dict[str, Any]]:
        """Return the raw parsed glossary JSON for inspection."""
        path = Path(glossary_path)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
