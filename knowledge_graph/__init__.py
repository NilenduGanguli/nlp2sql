"""
KnowledgeQL Knowledge Graph Package
====================================
Manages the in-memory knowledge graph constructed from Oracle schema metadata.

Submodules:
  config           – Environment-driven configuration dataclasses
  models           – Typed dataclasses for every node and relationship type
  oracle_extractor – Oracle data dictionary metadata extraction
  graph_store      – In-memory property graph (KnowledgeGraph)
  graph_builder    – Builds the KnowledgeGraph from extracted metadata
  traversal        – Query functions for runtime schema retrieval
  glossary_loader  – BusinessTerm inference from Oracle column/table metadata
  init_graph       – Top-level orchestrator: extract → build → validate → ready
"""

from knowledge_graph.config import GraphConfig, OracleConfig
from knowledge_graph.graph_store import KnowledgeGraph
from knowledge_graph.models import (
    SchemaNode, TableNode, ColumnNode, ViewNode, IndexNode,
    ConstraintNode, ProcedureNode, SynonymNode, SequenceNode,
    BusinessTermNode, QueryPatternNode,
    BelongsToRel, HasColumnRel, HasPrimaryKeyRel, HasForeignKeyRel,
    HasIndexRel, IndexedByRel, HasConstraintRel, DependsOnRel,
    CallsRel, MapsToRel, JoinPathRel, SimilarToRel,
)

__all__ = [
    "GraphConfig", "OracleConfig", "KnowledgeGraph",
    "SchemaNode", "TableNode", "ColumnNode", "ViewNode", "IndexNode",
    "ConstraintNode", "ProcedureNode", "SynonymNode", "SequenceNode",
    "BusinessTermNode", "QueryPatternNode",
    "BelongsToRel", "HasColumnRel", "HasPrimaryKeyRel", "HasForeignKeyRel",
    "HasIndexRel", "IndexedByRel", "HasConstraintRel", "DependsOnRel",
    "CallsRel", "MapsToRel", "JoinPathRel", "SimilarToRel",
]
