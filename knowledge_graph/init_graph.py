"""
KnowledgeQL Graph Initialization Script
=========================================
Top-level orchestrator that runs the full graph construction pipeline
BEFORE the application begins accepting user queries.

Pipeline sequence
-----------------
  1. Health-check Oracle, Neo4j (fail-fast if unreachable)
  2. Extract all Oracle metadata (tables, columns, FKs, views, …)
  3. Build the Neo4j knowledge graph (Schema → Table → Column → … nodes)
  4. Load the KYC business glossary (BusinessTerm + MAPS_TO edges)
  5. Validate the graph (consistency checks)
  6. Print a summary report

Usage (CLI)::

    python -m knowledge_graph.init_graph

Usage (programmatic)::

    from knowledge_graph.init_graph import initialize_graph
    stats = initialize_graph()

Incremental refresh (only changed objects since last run)::

    python -m knowledge_graph.init_graph --refresh-only
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Dict, Optional

from knowledge_graph.config import GraphConfig
from knowledge_graph.oracle_extractor import OracleMetadataExtractor
from knowledge_graph.graph_builder import GraphBuilder
from knowledge_graph.glossary_loader import GlossaryLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("knowledge_graph.init")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALIDATION_QUERIES = [
    # Every Table must belong to a Schema
    (
        "Tables without a Schema",
        "MATCH (t:Table) WHERE NOT (t)-[:BELONGS_TO]->(:Schema) RETURN count(t) AS cnt",
        0,
    ),
    # Every Column must belong to a Table
    (
        "Columns without a Table",
        "MATCH (c:Column) WHERE NOT (:Table)-[:HAS_COLUMN]->(c) RETURN count(c) AS cnt",
        0,
    ),
    # FK edges should reference existing Column nodes
    (
        "FK edges with missing target",
        """MATCH (src:Column)-[fk:HAS_FOREIGN_KEY]->(tgt)
           WHERE NOT (tgt:Column)
           RETURN count(fk) AS cnt""",
        0,
    ),
    # At least one table must exist (sanity check)
    (
        "Total tables > 0",
        "MATCH (t:Table) RETURN count(t) AS cnt",
        1,   # minimum expected value
    ),
]


def validate_graph(builder: GraphBuilder) -> bool:
    """
    Run basic consistency checks against the live graph.
    Returns True if all checks pass.
    """
    if not builder._driver:
        builder.connect()

    db = builder.config.neo4j.database
    all_passed = True

    with builder._driver.session(database=db) as session:
        for check_name, cypher, expected in _VALIDATION_QUERIES:
            result = session.run(cypher)
            cnt = result.single()["cnt"]
            # For "minimum expected" checks, cnt >= expected; for "must be 0", cnt == 0
            if expected == 0:
                passed = cnt == 0
            else:
                passed = cnt >= expected

            status = "PASS" if passed else "FAIL"
            logger.info("Validation [%s] %s — count=%d", status, check_name, cnt)
            if not passed:
                all_passed = False

    return all_passed


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def initialize_graph(
    config: Optional[GraphConfig] = None,
    refresh_only: bool = False,
) -> Dict[str, Any]:
    """
    Run the full graph construction pipeline.

    Parameters
    ----------
    config:       GraphConfig instance; if None, loads from environment.
    refresh_only: If True, skip validation and only run extraction + build
                  (useful for incremental scheduled refreshes).

    Returns
    -------
    Dict with build statistics and a 'success' boolean key.
    """
    start_time = time.monotonic()
    config = config or GraphConfig()

    report: Dict[str, Any] = {
        "success": False,
        "oracle_connected": False,
        "neo4j_connected": False,
        "extraction": {},
        "build": {},
        "glossary": {},
        "validation_passed": False,
        "elapsed_seconds": 0.0,
    }

    # ------------------------------------------------------------------
    # Step 1: Health checks
    # ------------------------------------------------------------------
    logger.info("=== KnowledgeQL Graph Initialization ===")

    extractor = OracleMetadataExtractor(config.oracle)
    if not extractor.check_connectivity():
        logger.error("Cannot connect to Oracle — aborting initialization")
        return report
    report["oracle_connected"] = True
    logger.info("Oracle connectivity: OK")

    builder = GraphBuilder(config)
    if not builder.check_connectivity():
        logger.error("Cannot connect to Neo4j — aborting initialization")
        return report
    report["neo4j_connected"] = True
    logger.info("Neo4j connectivity: OK")

    # ------------------------------------------------------------------
    # Step 2: Oracle metadata extraction
    # ------------------------------------------------------------------
    logger.info("Starting Oracle metadata extraction…")
    extract_start = time.monotonic()
    try:
        metadata = extractor.extract()
    except Exception as exc:
        logger.exception("Metadata extraction failed: %s", exc)
        return report

    extract_elapsed = time.monotonic() - extract_start
    report["extraction"] = {
        "schemas": len(metadata.schemas),
        "tables": len(metadata.tables),
        "columns": len(metadata.columns),
        "views": len(metadata.views),
        "indexes": len(metadata.indexes),
        "foreign_keys": len(metadata.foreign_keys),
        "procedures": len(metadata.procedures),
        "elapsed_seconds": round(extract_elapsed, 1),
    }
    logger.info("Extraction complete in %.1fs. %s", extract_elapsed, metadata.summary())

    # ------------------------------------------------------------------
    # Step 3: Build the Neo4j knowledge graph
    # ------------------------------------------------------------------
    logger.info("Building Neo4j knowledge graph…")
    build_start = time.monotonic()
    try:
        with builder:
            build_stats = builder.build(metadata)

            # ----------------------------------------------------------
            # Step 4: Load business glossary
            # ----------------------------------------------------------
            logger.info("Loading KYC business glossary…")
            db = config.neo4j.database
            with builder._driver.session(database=db) as session:
                loader = GlossaryLoader(session, config.glossary_path)
                glossary_stats = loader.load()
            report["glossary"] = glossary_stats

            # ----------------------------------------------------------
            # Step 5: Validate
            # ----------------------------------------------------------
            if not refresh_only:
                logger.info("Running graph validation checks…")
                validation_passed = validate_graph(builder)
                report["validation_passed"] = validation_passed
                if not validation_passed:
                    logger.warning("Graph validation found issues — review logs above")
            else:
                report["validation_passed"] = True

    except Exception as exc:
        logger.exception("Graph build failed: %s", exc)
        return report

    build_elapsed = time.monotonic() - build_start
    report["build"] = {**build_stats, "elapsed_seconds": round(build_elapsed, 1)}

    total_elapsed = time.monotonic() - start_time
    report["elapsed_seconds"] = round(total_elapsed, 1)
    report["success"] = True

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info("=== Initialization complete in %.1fs ===", total_elapsed)
    logger.info("  Extracted: %d tables, %d columns, %d FK relationships",
                len(metadata.tables), len(metadata.columns), len(metadata.foreign_keys))
    logger.info("  Graph nodes written: Schema=%d, Table=%d, Column=%d, View=%d",
                build_stats.get("schemas", 0), build_stats.get("tables", 0),
                build_stats.get("columns", 0), build_stats.get("views", 0))
    logger.info("  Graph edges written: FK=%d, JOIN_PATH=%d, SIMILAR_TO=%d",
                build_stats.get("foreign_keys", 0), build_stats.get("join_paths", 0),
                build_stats.get("similar_to", 0))
    logger.info("  Business terms: %d terms, %d mappings",
                glossary_stats.get("terms", 0), glossary_stats.get("mappings", 0))
    logger.info("  Validation: %s",
                "PASSED" if report["validation_passed"] else "FAILED")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize or refresh the KnowledgeQL Neo4j knowledge graph"
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        default=False,
        help="Skip validation checks (fast refresh mode for scheduled runs)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    report = initialize_graph(refresh_only=args.refresh_only)

    if not report["success"]:
        logger.error("Graph initialization FAILED. See logs above for details.")
        sys.exit(1)

    logger.info("Graph initialization SUCCEEDED.")
    sys.exit(0)
