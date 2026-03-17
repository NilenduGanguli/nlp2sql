"""
KnowledgeQL Knowledge Graph Package
====================================
Manages the Neo4j knowledge graph constructed from Oracle schema metadata.

Submodules:
  config       – Environment-driven configuration dataclasses
  models       – Typed dataclasses for every node and relationship type
  oracle_extractor – Async Oracle data dictionary metadata extraction
  graph_builder    – Idempotent Neo4j graph construction from extracted metadata
  traversal        – Parameterised Cypher queries for runtime schema retrieval
  glossary_loader  – KYC business glossary ingestion (BusinessTerm + MAPS_TO)
  init_graph       – Top-level orchestrator: extract → build → validate → ready
"""

from knowledge_graph.config import GraphConfig, OracleConfig, Neo4jConfig
from knowledge_graph.models import (
    SchemaNode, TableNode, ColumnNode, ViewNode, IndexNode,
    ConstraintNode, ProcedureNode, SynonymNode, SequenceNode,
    BusinessTermNode, QueryPatternNode,
    BelongsToRel, HasColumnRel, HasPrimaryKeyRel, HasForeignKeyRel,
    HasIndexRel, IndexedByRel, HasConstraintRel, DependsOnRel,
    CallsRel, MapsToRel, JoinPathRel, SimilarToRel,
)

__all__ = [
    "GraphConfig", "OracleConfig", "Neo4jConfig",
    "SchemaNode", "TableNode", "ColumnNode", "ViewNode", "IndexNode",
    "ConstraintNode", "ProcedureNode", "SynonymNode", "SequenceNode",
    "BusinessTermNode", "QueryPatternNode",
    "BelongsToRel", "HasColumnRel", "HasPrimaryKeyRel", "HasForeignKeyRel",
    "HasIndexRel", "IndexedByRel", "HasConstraintRel", "DependsOnRel",
    "CallsRel", "MapsToRel", "JoinPathRel", "SimilarToRel",
]
