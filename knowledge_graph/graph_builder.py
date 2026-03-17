"""
Neo4j Knowledge Graph Builder
===============================
Takes an OracleMetadata snapshot and constructs (or refreshes) the Neo4j
knowledge graph using idempotent MERGE operations.

Build sequence
--------------
1.  Schema constraints & indexes     – ensures uniqueness, speeds up MERGEs
2.  Schema nodes                     – top-level namespaces
3.  Table nodes + BELONGS_TO         – tables with schema containment
4.  Column nodes + HAS_COLUMN        – columns with ordinal ordering
5.  PK edges                         – HAS_PRIMARY_KEY + is_pk flag
6.  FK edges                         – HAS_FOREIGN_KEY between Column nodes
7.  Index nodes + HAS_INDEX/INDEXED_BY
8.  Constraint nodes + HAS_CONSTRAINT
9.  View nodes + BELONGS_TO + DEPENDS_ON
10. Procedure nodes + BELONGS_TO
11. Synonym nodes + BELONGS_TO
12. Sequence nodes + BELONGS_TO
13. JOIN_PATH edges                  – BFS over FK graph (NetworkX)
14. SIMILAR_TO edges                 – name-based column similarity

All write operations are batched (configurable batch_size, default 500)
and wrapped in explicit transactions for atomicity.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
from Levenshtein import distance as levenshtein_distance
from neo4j import GraphDatabase, Driver, Session

from knowledge_graph.config import Neo4jConfig, GraphConfig
from knowledge_graph.models import (
    SchemaNode, TableNode, ColumnNode, ViewNode, IndexNode,
    ConstraintNode, ProcedureNode, SynonymNode, SequenceNode,
    JoinPathRel, SimilarToRel,
)
from knowledge_graph.oracle_extractor import OracleMetadata

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cypher templates
# ---------------------------------------------------------------------------

# --- Constraints ---
_CONSTRAINTS_DDL = [
    "CREATE CONSTRAINT schema_name_unique IF NOT EXISTS FOR (s:Schema) REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT table_fqn_unique IF NOT EXISTS FOR (t:Table) REQUIRE t.fqn IS UNIQUE",
    "CREATE CONSTRAINT column_fqn_unique IF NOT EXISTS FOR (c:Column) REQUIRE c.fqn IS UNIQUE",
    "CREATE CONSTRAINT view_fqn_unique IF NOT EXISTS FOR (v:View) REQUIRE v.fqn IS UNIQUE",
    "CREATE CONSTRAINT index_fqn_unique IF NOT EXISTS FOR (i:Index) REQUIRE i.fqn IS UNIQUE",
    "CREATE CONSTRAINT constraint_fqn_unique IF NOT EXISTS FOR (con:Constraint) REQUIRE con.fqn IS UNIQUE",
    "CREATE CONSTRAINT procedure_fqn_unique IF NOT EXISTS FOR (p:Procedure) REQUIRE p.fqn IS UNIQUE",
    "CREATE CONSTRAINT synonym_fqn_unique IF NOT EXISTS FOR (syn:Synonym) REQUIRE syn.fqn IS UNIQUE",
    "CREATE CONSTRAINT sequence_fqn_unique IF NOT EXISTS FOR (seq:Sequence) REQUIRE seq.fqn IS UNIQUE",
    "CREATE CONSTRAINT business_term_unique IF NOT EXISTS FOR (bt:BusinessTerm) REQUIRE bt.term IS UNIQUE",
    "CREATE CONSTRAINT query_pattern_unique IF NOT EXISTS FOR (qp:QueryPattern) REQUIRE qp.pattern_id IS UNIQUE",
]

_INDEXES_DDL = [
    "CREATE INDEX table_name_idx IF NOT EXISTS FOR (t:Table) ON (t.name)",
    "CREATE INDEX column_name_idx IF NOT EXISTS FOR (c:Column) ON (c.name)",
    "CREATE INDEX column_data_type_idx IF NOT EXISTS FOR (c:Column) ON (c.data_type)",
    "CREATE FULLTEXT INDEX table_fulltext IF NOT EXISTS FOR (t:Table) ON EACH [t.name, t.comments]",
    "CREATE FULLTEXT INDEX column_fulltext IF NOT EXISTS FOR (c:Column) ON EACH [c.name, c.comments]",
    "CREATE FULLTEXT INDEX business_term_fulltext IF NOT EXISTS FOR (bt:BusinessTerm) ON EACH [bt.term, bt.definition]",
]

# --- Nodes ---
_UPSERT_SCHEMA = """
UNWIND $rows AS row
MERGE (s:Schema {name: row.name})
SET s.owner = row.owner,
    s.created_date = row.created_date,
    s.last_updated = timestamp()
"""

_UPSERT_TABLE = """
UNWIND $rows AS row
MERGE (t:Table {fqn: row.fqn})
SET t.name = row.name,
    t.schema = row.schema,
    t.row_count = row.row_count,
    t.avg_row_length = row.avg_row_length,
    t.last_analyzed = row.last_analyzed,
    t.table_type = row.table_type,
    t.partitioned = row.partitioned,
    t.temporary = row.temporary,
    t.comments = row.comments,
    t.last_updated = timestamp()
WITH t, row
MATCH (s:Schema {name: row.schema})
MERGE (t)-[:BELONGS_TO]->(s)
"""

_UPSERT_COLUMN = """
UNWIND $rows AS row
MERGE (c:Column {fqn: row.fqn})
SET c.name = row.name,
    c.table_name = row.table_name,
    c.schema = row.schema,
    c.data_type = row.data_type,
    c.data_length = row.data_length,
    c.precision = row.precision,
    c.scale = row.scale,
    c.nullable = row.nullable,
    c.default_value = row.default_value,
    c.column_id = row.column_id,
    c.comments = row.comments,
    c.num_distinct = row.num_distinct,
    c.histogram_type = row.histogram_type,
    c.sample_values = row.sample_values,
    c.is_pk = row.is_pk,
    c.is_fk = row.is_fk,
    c.is_indexed = row.is_indexed,
    c.last_updated = timestamp()
WITH c, row
MATCH (t:Table {fqn: row.table_fqn})
MERGE (t)-[r:HAS_COLUMN]->(c)
SET r.ordinal_position = row.column_id
"""

_UPSERT_PK = """
UNWIND $rows AS row
MATCH (t:Table {fqn: row.table_fqn})
MATCH (c:Column {fqn: row.column_fqn})
MERGE (t)-[r:HAS_PRIMARY_KEY]->(c)
SET r.constraint_name = row.constraint_name,
    r.key_position = row.key_position
SET c.is_pk = true
"""

_UPSERT_FK = """
UNWIND $rows AS row
MATCH (src:Column {fqn: row.source_col_fqn})
MATCH (tgt:Column {fqn: row.target_col_fqn})
MERGE (src)-[fk:HAS_FOREIGN_KEY {constraint_name: row.constraint_name}]->(tgt)
SET fk.on_delete_action = row.on_delete_action
SET src.is_fk = true
"""

_UPSERT_INDEX = """
UNWIND $rows AS row
MERGE (idx:Index {fqn: row.fqn})
SET idx.name = row.name,
    idx.schema = row.schema,
    idx.table_name = row.table_name,
    idx.index_type = row.index_type,
    idx.uniqueness = row.uniqueness,
    idx.columns_list = row.columns_list,
    idx.tablespace = row.tablespace,
    idx.compression = row.compression,
    idx.last_updated = timestamp()
WITH idx, row
MATCH (t:Table {fqn: row.table_fqn})
MERGE (t)-[:HAS_INDEX]->(idx)
"""

_UPSERT_INDEXED_BY = """
UNWIND $rows AS row
MATCH (c:Column {fqn: row.column_fqn})
MATCH (idx:Index {fqn: row.index_fqn})
MERGE (c)-[r:INDEXED_BY]->(idx)
SET r.column_position = row.column_position
SET c.is_indexed = true
"""

_UPSERT_CONSTRAINT = """
UNWIND $rows AS row
MERGE (con:Constraint {fqn: row.fqn})
SET con.name = row.name,
    con.schema = row.schema,
    con.table_name = row.table_name,
    con.type = row.type,
    con.condition = row.condition,
    con.status = row.status,
    con.validated = row.validated
WITH con, row
MATCH (t:Table {fqn: row.table_fqn})
MERGE (t)-[:HAS_CONSTRAINT]->(con)
"""

_UPSERT_VIEW = """
UNWIND $rows AS row
MERGE (v:View {fqn: row.fqn})
SET v.name = row.name,
    v.schema = row.schema,
    v.view_text = row.view_text,
    v.is_materialized = row.is_materialized,
    v.refresh_mode = row.refresh_mode,
    v.last_refresh = row.last_refresh,
    v.comments = row.comments,
    v.last_updated = timestamp()
WITH v, row
MATCH (s:Schema {name: row.schema})
MERGE (v)-[:BELONGS_TO]->(s)
"""

_UPSERT_VIEW_DEPENDS_ON = """
UNWIND $rows AS row
MATCH (v:View {fqn: row.view_fqn})
MATCH (t {fqn: row.table_fqn})
MERGE (v)-[d:DEPENDS_ON {dependency_type: row.dependency_type}]->(t)
"""

_UPSERT_PROCEDURE = """
UNWIND $rows AS row
MERGE (p:Procedure {fqn: row.fqn})
SET p.name = row.name,
    p.schema = row.schema,
    p.type = row.type,
    p.parameters = row.parameters,
    p.return_type = row.return_type,
    p.body_summary = row.body_summary,
    p.status = row.status,
    p.last_updated = timestamp()
WITH p, row
MATCH (s:Schema {name: row.schema})
MERGE (p)-[:BELONGS_TO]->(s)
"""

_UPSERT_SYNONYM = """
UNWIND $rows AS row
MERGE (syn:Synonym {fqn: row.fqn})
SET syn.name = row.name,
    syn.schema = row.schema,
    syn.target_schema = row.target_schema,
    syn.target_object = row.target_object
"""

_UPSERT_SEQUENCE = """
UNWIND $rows AS row
MERGE (seq:Sequence {fqn: row.fqn})
SET seq.name = row.name,
    seq.schema = row.schema,
    seq.min_value = row.min_value,
    seq.max_value = row.max_value,
    seq.increment_by = row.increment_by,
    seq.cache_size = row.cache_size
WITH seq, row
MATCH (s:Schema {name: row.schema})
MERGE (seq)-[:BELONGS_TO]->(s)
"""

_UPSERT_JOIN_PATH = """
UNWIND $rows AS row
MATCH (src:Table {fqn: row.source_table_fqn})
MATCH (tgt:Table {fqn: row.target_table_fqn})
MERGE (src)-[jp:JOIN_PATH {path_key: row.path_key}]->(tgt)
SET jp.join_columns = row.join_columns,
    jp.join_type = row.join_type,
    jp.cardinality = row.cardinality,
    jp.weight = row.weight
"""

_UPSERT_SIMILAR_TO = """
UNWIND $rows AS row
MATCH (c1:Column {fqn: row.source_col_fqn})
MATCH (c2:Column {fqn: row.target_col_fqn})
MERGE (c1)-[st:SIMILAR_TO]->(c2)
SET st.similarity_score = row.similarity_score,
    st.match_type = row.match_type
"""

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class GraphBuilder:
    """
    Constructs and refreshes the KnowledgeQL Neo4j knowledge graph.

    Usage::

        config = GraphConfig()
        builder = GraphBuilder(config)
        builder.build(metadata)   # metadata: OracleMetadata
    """

    # Column suffix patterns that imply FK candidate columns
    _FK_SUFFIXES = ("_ID", "_CODE", "_KEY", "_NO", "_NUM", "_REF")

    def __init__(self, config: GraphConfig) -> None:
        self.config = config
        self._driver: Optional[Driver] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        cfg = self.config.neo4j
        self._driver = GraphDatabase.driver(
            cfg.uri,
            auth=(cfg.user, cfg.password),
        )
        self._driver.verify_connectivity()
        logger.info("Connected to Neo4j at %s", cfg.uri)

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def check_connectivity(self) -> bool:
        try:
            driver = GraphDatabase.driver(
                self.config.neo4j.uri,
                auth=(self.config.neo4j.user, self.config.neo4j.password),
            )
            driver.verify_connectivity()
            driver.close()
            return True
        except Exception as exc:
            logger.error("Neo4j connectivity check failed: %s", exc)
            return False

    def __enter__(self) -> "GraphBuilder":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public build API
    # ------------------------------------------------------------------

    def build(self, metadata: OracleMetadata) -> Dict[str, int]:
        """
        Execute the full graph construction pipeline.
        Returns a dict of {step_name: nodes_written} for diagnostics.
        """
        if not self._driver:
            self.connect()

        stats: Dict[str, int] = {}
        db = self.config.neo4j.database

        with self._driver.session(database=db) as session:
            logger.info("Step 1/13: Creating schema constraints and indexes")
            self._setup_schema(session)

            logger.info("Step 2/13: Upserting Schema nodes")
            stats["schemas"] = self._upsert_batch(session, _UPSERT_SCHEMA,
                [n.to_cypher_params() for n in metadata.schemas])

            logger.info("Step 3/13: Upserting Table nodes + BELONGS_TO")
            stats["tables"] = self._upsert_batch(session, _UPSERT_TABLE,
                [t.to_cypher_params() for t in metadata.tables])

            logger.info("Step 4/13: Upserting Column nodes + HAS_COLUMN")
            stats["columns"] = self._upsert_batch(session, _UPSERT_COLUMN,
                [c.to_cypher_params() for c in metadata.columns])

            logger.info("Step 5/13: Creating HAS_PRIMARY_KEY edges")
            stats["primary_keys"] = self._upsert_batch(session, _UPSERT_PK,
                [{"table_fqn": pk.table_fqn, "column_fqn": pk.column_fqn,
                  "constraint_name": pk.constraint_name, "key_position": pk.key_position}
                 for pk in metadata.primary_keys])

            logger.info("Step 6/13: Creating HAS_FOREIGN_KEY edges")
            stats["foreign_keys"] = self._upsert_batch(session, _UPSERT_FK,
                [fk.to_cypher_params() for fk in metadata.foreign_keys])

            logger.info("Step 7/13: Upserting Index nodes + HAS_INDEX / INDEXED_BY")
            stats["indexes"] = self._upsert_indexes(session, metadata)

            logger.info("Step 8/13: Upserting Constraint nodes + HAS_CONSTRAINT")
            stats["constraints"] = self._upsert_batch(session, _UPSERT_CONSTRAINT,
                [c.to_cypher_params() for c in metadata.constraints])

            logger.info("Step 9/13: Upserting View nodes + DEPENDS_ON")
            stats["views"] = self._upsert_views(session, metadata)

            logger.info("Step 10/13: Upserting Procedure nodes")
            stats["procedures"] = self._upsert_batch(session, _UPSERT_PROCEDURE,
                [p.to_cypher_params() for p in metadata.procedures])

            logger.info("Step 11/13: Upserting Synonym and Sequence nodes")
            stats["synonyms"] = self._upsert_batch(session, _UPSERT_SYNONYM,
                [s.to_cypher_params() for s in metadata.synonyms])
            stats["sequences"] = self._upsert_batch(session, _UPSERT_SEQUENCE,
                [s.to_cypher_params() for s in metadata.sequences])

            logger.info("Step 12/13: Computing and storing JOIN_PATH edges")
            join_paths = self._compute_join_paths(metadata)
            stats["join_paths"] = self._upsert_batch(session, _UPSERT_JOIN_PATH,
                [jp.to_cypher_params() for jp in join_paths])

            logger.info("Step 13/13: Computing and storing SIMILAR_TO edges")
            similar_to = self._compute_similar_to(metadata)
            stats["similar_to"] = self._upsert_batch(session, _UPSERT_SIMILAR_TO,
                [st.to_cypher_params() for st in similar_to])

        logger.info("Graph build complete. Stats: %s", stats)
        return stats

    def get_graph_stats(self) -> Dict[str, int]:
        """Return node and relationship counts from the live graph."""
        if not self._driver:
            self.connect()
        stats = {}
        db = self.config.neo4j.database
        labels = ["Schema", "Table", "Column", "View", "Index", "Constraint",
                  "Procedure", "Synonym", "Sequence", "BusinessTerm", "QueryPattern"]
        rel_types = ["BELONGS_TO", "HAS_COLUMN", "HAS_PRIMARY_KEY", "HAS_FOREIGN_KEY",
                     "HAS_INDEX", "INDEXED_BY", "HAS_CONSTRAINT", "DEPENDS_ON",
                     "MAPS_TO", "JOIN_PATH", "SIMILAR_TO"]
        with self._driver.session(database=db) as session:
            for label in labels:
                result = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                stats[label] = result.single()["cnt"]
            for rel_type in rel_types:
                result = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS cnt")
                stats[rel_type] = result.single()["cnt"]
        return stats

    # ------------------------------------------------------------------
    # Schema setup (constraints + indexes)
    # ------------------------------------------------------------------

    def _setup_schema(self, session: Session) -> None:
        for ddl in _CONSTRAINTS_DDL:
            try:
                session.run(ddl)
            except Exception as exc:
                logger.warning("Constraint DDL skipped (%s): %s", ddl[:60], exc)
        for ddl in _INDEXES_DDL:
            try:
                session.run(ddl)
            except Exception as exc:
                logger.warning("Index DDL skipped (%s): %s", ddl[:60], exc)

    # ------------------------------------------------------------------
    # Batch upsert helper
    # ------------------------------------------------------------------

    def _upsert_batch(self, session: Session, cypher: str, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        batch_size = self.config.neo4j.batch_size
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            session.run(cypher, rows=batch)
            total += len(batch)
        return total

    # ------------------------------------------------------------------
    # Index-specific upsert (needs per-column INDEXED_BY rows)
    # ------------------------------------------------------------------

    def _upsert_indexes(self, session: Session, metadata: OracleMetadata) -> int:
        # Upsert Index nodes + HAS_INDEX edges
        idx_rows = [idx.to_cypher_params() for idx in metadata.indexes]
        count = self._upsert_batch(session, _UPSERT_INDEX, idx_rows)

        # Build INDEXED_BY rows
        indexed_by_rows: List[Dict[str, Any]] = []
        for idx in metadata.indexes:
            for pos, col_name in enumerate(idx.columns_list.split(","), start=1):
                col_name = col_name.strip()
                if not col_name:
                    continue
                col_fqn = f"{idx.schema.upper()}.{idx.table_name.upper()}.{col_name.upper()}"
                indexed_by_rows.append({
                    "column_fqn": col_fqn,
                    "index_fqn": idx.fqn,
                    "column_position": pos,
                })
        self._upsert_batch(session, _UPSERT_INDEXED_BY, indexed_by_rows)
        return count

    # ------------------------------------------------------------------
    # View-specific upsert (View node + DEPENDS_ON edges)
    # ------------------------------------------------------------------

    def _upsert_views(self, session: Session, metadata: OracleMetadata) -> int:
        view_rows = [v.to_cypher_params() for v in metadata.views]
        count = self._upsert_batch(session, _UPSERT_VIEW, view_rows)

        # DEPENDS_ON edges
        dep_rows: List[Dict[str, Any]] = []
        for view_fqn, deps in metadata.view_dependencies.items():
            for dep in deps:
                dep_rows.append({
                    "view_fqn": view_fqn,
                    "table_fqn": dep["table_fqn"],
                    "dependency_type": dep.get("dependency_type", "SELECT"),
                })
        self._upsert_batch(session, _UPSERT_VIEW_DEPENDS_ON, dep_rows)
        return count

    # ------------------------------------------------------------------
    # JOIN_PATH computation (BFS over FK graph via NetworkX)
    # ------------------------------------------------------------------

    def _compute_join_paths(self, metadata: OracleMetadata) -> List[JoinPathRel]:
        """
        Build a directed multigraph of Table → Table connected by FK constraints,
        then compute shortest paths up to max_join_path_hops hops.

        Edge data stored on the NetworkX graph:
          src_col, tgt_col, constraint_name, on_delete_action
        """
        max_hops = self.config.max_join_path_hops
        G = nx.MultiDiGraph()

        # Add all table nodes
        table_fqns = {t.fqn for t in metadata.tables}
        G.add_nodes_from(table_fqns)

        # Build column → table map for FK edge resolution
        col_to_table: Dict[str, str] = {}
        for col in metadata.columns:
            col_to_table[col.fqn] = col.table_fqn

        # Add FK edges between tables
        for fk in metadata.foreign_keys:
            src_table = col_to_table.get(fk.source_col_fqn)
            tgt_table = col_to_table.get(fk.target_col_fqn)
            if src_table and tgt_table and src_table != tgt_table:
                G.add_edge(src_table, tgt_table,
                           src_col=fk.source_col_fqn,
                           tgt_col=fk.target_col_fqn,
                           constraint_name=fk.constraint_name)
                # Also add reverse edge (joins can be traversed either direction)
                G.add_edge(tgt_table, src_table,
                           src_col=fk.target_col_fqn,
                           tgt_col=fk.source_col_fqn,
                           constraint_name=fk.constraint_name + "_REV")

        join_paths: List[JoinPathRel] = []
        seen_pairs: Set[Tuple[str, str]] = set()

        table_list = list(table_fqns)
        for i, src in enumerate(table_list):
            for tgt in table_list[i + 1:]:
                if (src, tgt) in seen_pairs:
                    continue
                try:
                    # Find shortest undirected path within max_hops
                    path_nodes = nx.shortest_path(
                        G.to_undirected(as_view=True), src, tgt
                    )
                except nx.NetworkXNoPath:
                    continue
                except Exception:
                    continue

                if len(path_nodes) - 1 > max_hops:
                    continue

                # Collect join columns along the path
                join_cols: List[Dict[str, str]] = []
                for step in range(len(path_nodes) - 1):
                    u, v = path_nodes[step], path_nodes[step + 1]
                    edge_data = self._best_edge(G, u, v)
                    if edge_data:
                        join_cols.append({
                            "src": edge_data.get("src_col", ""),
                            "tgt": edge_data.get("tgt_col", ""),
                            "constraint": edge_data.get("constraint_name", ""),
                        })

                if join_cols:
                    weight = len(path_nodes) - 1
                    join_paths.append(JoinPathRel(
                        source_table_fqn=src,
                        target_table_fqn=tgt,
                        join_columns=join_cols,
                        weight=weight,
                    ))
                    join_paths.append(JoinPathRel(
                        source_table_fqn=tgt,
                        target_table_fqn=src,
                        join_columns=[{"src": jc["tgt"], "tgt": jc["src"],
                                       "constraint": jc["constraint"]}
                                      for jc in reversed(join_cols)],
                        weight=weight,
                    ))
                    seen_pairs.add((src, tgt))
                    seen_pairs.add((tgt, src))

        logger.info("Computed %d JOIN_PATH edges", len(join_paths))
        return join_paths

    def _best_edge(self, G: nx.MultiDiGraph, u: str, v: str) -> Optional[Dict[str, Any]]:
        """Return edge data for the best (lowest key) edge between u and v."""
        edges = G.get_edge_data(u, v)
        if not edges:
            # Try reverse
            edges = G.get_edge_data(v, u)
        if edges:
            return edges[min(edges.keys())]
        return None

    # ------------------------------------------------------------------
    # SIMILAR_TO edge computation
    # ------------------------------------------------------------------

    def _compute_similar_to(self, metadata: OracleMetadata) -> List[SimilarToRel]:
        """
        Infer SIMILAR_TO edges between Column nodes in different tables.

        Three strategies (applied in order, non-overlapping):
          1. Exact name match          – score 1.0, type 'exact'
          2. Common FK suffix pattern  – score 0.9, type 'suffix'
             e.g. ACCOUNTS.CUSTOMER_ID and ORDERS.CUSTOMER_ID
          3. Levenshtein edit distance – score based on length-normalised dist,
             match_type 'levenshtein'

        Columns within the same table are never linked.
        """
        max_dist = self.config.similarity_levenshtein_max
        min_score = self.config.similarity_min_score

        # Group columns by (name, table) for efficient lookup
        by_name: Dict[str, List[ColumnNode]] = defaultdict(list)
        fk_suffix_cols: Dict[str, List[ColumnNode]] = defaultdict(list)

        for col in metadata.columns:
            name_upper = col.name.upper()
            by_name[name_upper].append(col)
            for suffix in self._FK_SUFFIXES:
                if name_upper.endswith(suffix):
                    fk_suffix_cols[name_upper].append(col)

        similar_to: List[SimilarToRel] = []
        seen: Set[Tuple[str, str]] = set()

        def _add(c1: ColumnNode, c2: ColumnNode, score: float, match_type: str) -> None:
            key = (min(c1.fqn, c2.fqn), max(c1.fqn, c2.fqn))
            if key in seen or c1.table_fqn == c2.table_fqn:
                return
            seen.add(key)
            similar_to.append(SimilarToRel(
                source_col_fqn=c1.fqn,
                target_col_fqn=c2.fqn,
                similarity_score=score,
                match_type=match_type,
            ))

        # Strategy 1: Exact name match across tables
        for name, cols in by_name.items():
            if len(cols) < 2:
                continue
            for i, c1 in enumerate(cols):
                for c2 in cols[i + 1:]:
                    _add(c1, c2, 1.0, "exact")

        # Strategy 2: FK suffix columns with the same name
        for name, cols in fk_suffix_cols.items():
            if len(cols) < 2:
                continue
            for i, c1 in enumerate(cols):
                for c2 in cols[i + 1:]:
                    _add(c1, c2, 0.9, "suffix")

        # Strategy 3: Levenshtein distance on all remaining column pairs
        all_cols = metadata.columns
        for i, c1 in enumerate(all_cols):
            for c2 in all_cols[i + 1:]:
                if c1.table_fqn == c2.table_fqn:
                    continue
                n1, n2 = c1.name.upper(), c2.name.upper()
                key = (min(c1.fqn, c2.fqn), max(c1.fqn, c2.fqn))
                if key in seen:
                    continue
                dist = levenshtein_distance(n1, n2)
                if dist <= max_dist:
                    max_len = max(len(n1), len(n2), 1)
                    score = round(1.0 - dist / max_len, 4)
                    if score >= min_score:
                        _add(c1, c2, score, "levenshtein")

        logger.info("Computed %d SIMILAR_TO edges", len(similar_to))
        return similar_to
