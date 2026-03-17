"""
Configuration for the KnowledgeQL knowledge graph pipeline.

All values are sourced from environment variables (or a .env file).
Use GraphConfig as the single entry point — it composes OracleConfig and Neo4jConfig.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class OracleConfig:
    """Connection and extraction settings for the Oracle source database."""

    dsn: str = field(default_factory=lambda: os.getenv("ORACLE_DSN", ""))
    user: str = field(default_factory=lambda: os.getenv("ORACLE_USER", ""))
    password: str = field(default_factory=lambda: os.getenv("ORACLE_PASSWORD", ""))
    # Oracle schemas to introspect; empty list means "all schemas accessible to service user"
    target_schemas: List[str] = field(default_factory=list)
    # Rows to fetch per table for sample_values on Column nodes
    sample_rows: int = field(
        default_factory=lambda: int(os.getenv("ORACLE_SAMPLE_ROWS", "10"))
    )
    # Use DBA_ views (requires DBA or SELECT ANY DICTIONARY privilege).
    # Falls back to ALL_ views when False.
    use_dba_views: bool = field(
        default_factory=lambda: os.getenv("ORACLE_USE_DBA_VIEWS", "true").lower() == "true"
    )

    def __post_init__(self) -> None:
        if not self.target_schemas:
            raw = os.getenv("ORACLE_TARGET_SCHEMAS", "")
            self.target_schemas = [s.strip().upper() for s in raw.split(",") if s.strip()]

    @property
    def view_prefix(self) -> str:
        """Returns 'DBA' or 'ALL' based on privilege mode."""
        return "DBA" if self.use_dba_views else "ALL"

    def validate(self) -> None:
        if not self.dsn:
            raise ValueError("ORACLE_DSN is required")
        if not self.user:
            raise ValueError("ORACLE_USER is required")
        if not self.password:
            raise ValueError("ORACLE_PASSWORD is required")


@dataclass
class Neo4jConfig:
    """Connection settings for the Neo4j knowledge graph database."""

    uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    user: str = field(default_factory=lambda: os.getenv("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", ""))
    database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j"))
    # Number of nodes/relationships to write per Cypher UNWIND batch
    batch_size: int = field(
        default_factory=lambda: int(os.getenv("NEO4J_BATCH_SIZE", "500"))
    )

    def validate(self) -> None:
        if not self.uri:
            raise ValueError("NEO4J_URI is required")
        if not self.password:
            raise ValueError("NEO4J_PASSWORD is required")


@dataclass
class GraphConfig:
    """
    Top-level configuration for the knowledge graph pipeline.

    Composes OracleConfig and Neo4jConfig and adds tuning knobs for
    relationship inference (JOIN_PATH depth, SIMILAR_TO threshold).
    """

    oracle: OracleConfig = field(default_factory=OracleConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)

    # Maximum FK-hop depth for pre-computed JOIN_PATH edges
    max_join_path_hops: int = field(
        default_factory=lambda: int(os.getenv("MAX_JOIN_PATH_HOPS", "4"))
    )
    # Maximum Levenshtein edit distance for SIMILAR_TO column name matching
    similarity_levenshtein_max: int = field(
        default_factory=lambda: int(os.getenv("SIMILARITY_LEVENSHTEIN_MAX", "2"))
    )
    # Minimum normalised similarity score (0–1) to create a SIMILAR_TO edge
    similarity_min_score: float = field(
        default_factory=lambda: float(os.getenv("SIMILARITY_MIN_SCORE", "0.75"))
    )
    # Path to KYC business glossary JSON file
    glossary_path: str = field(
        default_factory=lambda: os.getenv("GLOSSARY_PATH", "data/kyc_glossary.json")
    )

    def validate(self) -> None:
        self.oracle.validate()
        self.neo4j.validate()
