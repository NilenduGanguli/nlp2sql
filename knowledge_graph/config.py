"""
Configuration for the KnowledgeQL knowledge graph pipeline.

All values are sourced from environment variables (or a .env file).
Use GraphConfig as the single entry point — it composes OracleConfig and
tuning knobs for the in-memory graph algorithms.
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
    # Enable oracledb thick mode (requires Oracle Instant Client on the host).
    # When enabled, oracledb.init_oracle_client() is called with no lib_dir so
    # it discovers the client libraries via LD_LIBRARY_PATH / PATH automatically.
    thick_mode: bool = field(
        default_factory=lambda: os.getenv("ORACLE_THICK_MODE", "false").lower() == "true"
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
class ValueCacheConfig:
    """
    Configuration for the column-value cache.

    Drives the precomputed distinct-value lookup that grounds SQL WHERE
    clauses in real database values rather than LLM-inferred guesses.
    """

    enabled: bool = field(
        default_factory=lambda: os.getenv("VALUE_CACHE_ENABLED", "true").lower()
        not in ("false", "0", "no")
    )
    max_values: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_MAX_VALUES", "30"))
    )
    probe_workers: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_PROBE_WORKERS", "8"))
    )
    probe_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_PROBE_TIMEOUT_MS", "5000"))
    )
    llm_nominate: bool = field(
        default_factory=lambda: os.getenv("VALUE_CACHE_LLM_NOMINATE", "true").lower()
        not in ("false", "0", "no")
    )
    llm_batch_size: int = field(
        default_factory=lambda: int(os.getenv("VALUE_CACHE_LLM_BATCH_SIZE", "50"))
    )


@dataclass
class GraphConfig:
    """
    Top-level configuration for the knowledge graph pipeline.

    Composes OracleConfig and adds tuning knobs for relationship inference
    (JOIN_PATH depth, SIMILAR_TO threshold). No external graph database
    configuration is needed — the graph is stored in-memory using Python.
    """

    oracle: OracleConfig = field(default_factory=OracleConfig)

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
    # Column-value cache (Phase 1 of value-grounded WHERE clauses)
    value_cache: ValueCacheConfig = field(default_factory=ValueCacheConfig)

    def validate(self) -> None:
        self.oracle.validate()
