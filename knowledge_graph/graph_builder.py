"""
In-Memory Knowledge Graph Builder
===================================
Takes an OracleMetadata snapshot and constructs (or refreshes) the KnowledgeQL
knowledge graph stored entirely in Python using KnowledgeGraph.

Build sequence
--------------
1.  Schema nodes                     – top-level namespaces
2.  Table nodes + BELONGS_TO         – tables with schema containment
3.  Column nodes + HAS_COLUMN        – columns with ordinal ordering
4.  PK edges                         – HAS_PRIMARY_KEY + is_pk flag on Column
5.  FK edges                         – HAS_FOREIGN_KEY between Column nodes
6.  Index nodes + HAS_INDEX/INDEXED_BY
7.  Constraint nodes + HAS_CONSTRAINT
8.  View nodes + BELONGS_TO + DEPENDS_ON
9.  Procedure nodes + BELONGS_TO
10. Synonym nodes
11. Sequence nodes + BELONGS_TO
12. JOIN_PATH edges                  – BFS over FK graph (NetworkX)
13. SIMILAR_TO edges                 – name-based column similarity

All operations work entirely in memory — no external database is required.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
from Levenshtein import distance as levenshtein_distance

from knowledge_graph.config import GraphConfig
from knowledge_graph.graph_store import KnowledgeGraph
from knowledge_graph.models import (
    ColumnNode, JoinPathRel, SimilarToRel,
)
from knowledge_graph.oracle_extractor import OracleMetadata

logger = logging.getLogger(__name__)


class GraphBuilder:
    """
    Constructs and refreshes the KnowledgeQL in-memory knowledge graph.

    Usage::

        config = GraphConfig()
        builder = GraphBuilder(config)
        builder.build(metadata)   # metadata: OracleMetadata
        graph = builder.graph     # KnowledgeGraph ready for traversal queries
    """

    # Column suffix patterns that imply FK candidate columns
    _FK_SUFFIXES = ("_ID", "_CODE", "_KEY", "_NO", "_NUM", "_REF")

    def __init__(self, config: GraphConfig) -> None:
        self.config = config
        self.graph = KnowledgeGraph()

    # ------------------------------------------------------------------
    # Compatibility shim (no external connection needed)
    # ------------------------------------------------------------------

    def check_connectivity(self) -> bool:
        """Always returns True — no external database to connect to."""
        return True

    def __enter__(self) -> "GraphBuilder":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Public build API
    # ------------------------------------------------------------------

    def build(self, metadata: OracleMetadata) -> Dict[str, int]:
        """
        Execute the full graph construction pipeline.
        Returns a dict of {step_name: nodes_written} for diagnostics.

        Every step is individually guarded: a failure in one step is logged and
        skipped so the rest of the graph is still built.
        """
        stats: Dict[str, int] = {}

        _steps = [
            ("schemas",      "Schema nodes",                  self._build_schemas),
            ("tables",       "Table nodes + BELONGS_TO",      self._build_tables),
            ("columns",      "Column nodes + HAS_COLUMN",     self._build_columns),
            ("primary_keys", "HAS_PRIMARY_KEY edges",         self._build_primary_keys),
            ("foreign_keys", "HAS_FOREIGN_KEY edges",         self._build_foreign_keys),
            ("indexes",      "Index nodes + HAS_INDEX",       self._build_indexes),
            ("constraints",  "Constraint nodes",              self._build_constraints),
            ("views",        "View nodes + DEPENDS_ON",       self._build_views),
            ("procedures",   "Procedure nodes",               self._build_procedures),
            ("synonyms",     "Synonym nodes",                 self._build_synonyms),
            ("sequences",    "Sequence nodes",                self._build_sequences),
        ]
        for i, (key, label, fn) in enumerate(_steps, start=1):
            logger.info("Step %d/%d: %s", i, len(_steps) + 2, label)
            try:
                stats[key] = fn(metadata)
            except Exception as exc:
                logger.warning("Step '%s' failed — skipping: %s", label, exc)
                stats[key] = 0

        logger.info("Step 12/13: Computing and storing JOIN_PATH edges")
        try:
            join_paths = self._compute_join_paths(metadata)
            for jp in join_paths:
                self.graph.merge_edge(
                    "JOIN_PATH",
                    jp.source_table_fqn,
                    jp.target_table_fqn,
                    merge_key="path_key",
                    path_key=f"{jp.source_table_fqn}>>{jp.target_table_fqn}",
                    join_columns=jp.join_columns,
                    join_type=jp.join_type,
                    cardinality=jp.cardinality,
                    weight=jp.weight,
                )
            stats["join_paths"] = len(join_paths)
        except Exception as exc:
            logger.warning("JOIN_PATH computation failed — skipping: %s", exc)
            stats["join_paths"] = 0

        logger.info("Step 13/13: Computing and storing SIMILAR_TO edges")
        try:
            similar_to = self._compute_similar_to(metadata)
            for st in similar_to:
                self.graph.merge_edge(
                    "SIMILAR_TO",
                    st.source_col_fqn,
                    st.target_col_fqn,
                    similarity_score=st.similarity_score,
                    match_type=st.match_type,
                )
            stats["similar_to"] = len(similar_to)
        except Exception as exc:
            logger.warning("SIMILAR_TO computation failed — skipping: %s", exc)
            stats["similar_to"] = 0

        logger.info("Graph build complete. Stats: %s", stats)
        return stats

    def get_graph_stats(self) -> Dict[str, int]:
        """Return node and relationship counts from the in-memory graph."""
        return self.graph.get_stats()

    # ------------------------------------------------------------------
    # Private build steps
    # ------------------------------------------------------------------

    def _build_schemas(self, metadata: OracleMetadata) -> int:
        for schema in metadata.schemas:
            p = schema.to_cypher_params()
            self.graph.merge_node("Schema", p["name"], p)
        return len(metadata.schemas)

    def _build_tables(self, metadata: OracleMetadata) -> int:
        for table in metadata.tables:
            p = table.to_cypher_params()
            self.graph.merge_node("Table", p["fqn"], p)
            self.graph.merge_edge("BELONGS_TO", p["fqn"], p["schema"])
        return len(metadata.tables)

    def _build_columns(self, metadata: OracleMetadata) -> int:
        for col in metadata.columns:
            p = col.to_cypher_params()
            self.graph.merge_node("Column", p["fqn"], p)
            self.graph.merge_edge(
                "HAS_COLUMN",
                p["table_fqn"],
                p["fqn"],
                ordinal_position=p["column_id"],
            )
        return len(metadata.columns)

    def _build_primary_keys(self, metadata: OracleMetadata) -> int:
        for pk in metadata.primary_keys:
            self.graph.merge_edge(
                "HAS_PRIMARY_KEY",
                pk.table_fqn,
                pk.column_fqn,
                constraint_name=pk.constraint_name,
                key_position=pk.key_position,
            )
            self.graph.set_node_prop("Column", pk.column_fqn, "is_pk", True)
        return len(metadata.primary_keys)

    def _build_foreign_keys(self, metadata: OracleMetadata) -> int:
        for fk in metadata.foreign_keys:
            p = fk.to_cypher_params()
            self.graph.merge_edge(
                "HAS_FOREIGN_KEY",
                p["source_col_fqn"],
                p["target_col_fqn"],
                merge_key="constraint_name",
                constraint_name=p["constraint_name"],
                on_delete_action=p["on_delete_action"],
            )
            self.graph.set_node_prop("Column", p["source_col_fqn"], "is_fk", True)
        return len(metadata.foreign_keys)

    def _build_indexes(self, metadata: OracleMetadata) -> int:
        for idx in metadata.indexes:
            p = idx.to_cypher_params()
            self.graph.merge_node("Index", p["fqn"], p)
            self.graph.merge_edge("HAS_INDEX", p["table_fqn"], p["fqn"])

            for pos, col_name in enumerate(idx.columns_list.split(","), start=1):
                col_name = col_name.strip()
                if not col_name:
                    continue
                col_fqn = f"{idx.schema.upper()}.{idx.table_name.upper()}.{col_name.upper()}"
                self.graph.merge_edge(
                    "INDEXED_BY", col_fqn, p["fqn"], column_position=pos
                )
                self.graph.set_node_prop("Column", col_fqn, "is_indexed", True)
        return len(metadata.indexes)

    def _build_constraints(self, metadata: OracleMetadata) -> int:
        for con in metadata.constraints:
            p = con.to_cypher_params()
            self.graph.merge_node("Constraint", p["fqn"], p)
            self.graph.merge_edge("HAS_CONSTRAINT", p["table_fqn"], p["fqn"])
        return len(metadata.constraints)

    def _build_views(self, metadata: OracleMetadata) -> int:
        for view in metadata.views:
            p = view.to_cypher_params()
            self.graph.merge_node("View", p["fqn"], p)
            self.graph.merge_edge("BELONGS_TO", p["fqn"], p["schema"])

        for view_fqn, deps in metadata.view_dependencies.items():
            for dep in deps:
                self.graph.merge_edge(
                    "DEPENDS_ON",
                    view_fqn,
                    dep["table_fqn"],
                    dependency_type=dep.get("dependency_type", "SELECT"),
                )
        return len(metadata.views)

    def _build_procedures(self, metadata: OracleMetadata) -> int:
        for proc in metadata.procedures:
            p = proc.to_cypher_params()
            self.graph.merge_node("Procedure", p["fqn"], p)
            self.graph.merge_edge("BELONGS_TO", p["fqn"], p["schema"])
        return len(metadata.procedures)

    def _build_synonyms(self, metadata: OracleMetadata) -> int:
        for syn in metadata.synonyms:
            p = syn.to_cypher_params()
            self.graph.merge_node("Synonym", p["fqn"], p)
        return len(metadata.synonyms)

    def _build_sequences(self, metadata: OracleMetadata) -> int:
        for seq in metadata.sequences:
            p = seq.to_cypher_params()
            self.graph.merge_node("Sequence", p["fqn"], p)
            self.graph.merge_edge("BELONGS_TO", p["fqn"], p["schema"])
        return len(metadata.sequences)

    # ------------------------------------------------------------------
    # JOIN_PATH computation (BFS over FK graph via NetworkX)
    # ------------------------------------------------------------------

    def _compute_join_paths(self, metadata: OracleMetadata) -> List[JoinPathRel]:
        """
        Build a directed multigraph of Table → Table connected by FK constraints,
        then compute shortest paths up to max_join_path_hops hops.

        Only tables that participate in at least one FK edge are included in
        path computation — isolated tables produce no JOIN_PATH edges.
        """
        max_hops = self.config.max_join_path_hops
        G = nx.MultiDiGraph()

        table_fqns = {t.fqn for t in metadata.tables}
        G.add_nodes_from(table_fqns)

        # Derive table FQN directly from the column FQN (SCHEMA.TABLE.COLUMN →
        # SCHEMA.TABLE) instead of looking it up in col_to_table.  The column
        # FQN is always built as f"{owner}.{table}.{col}" with .upper() applied,
        # so rsplit(".", 1) is reliable.  This makes FK graph construction
        # independent of whether metadata.columns is complete — a partial column
        # extraction (skipped rows, type errors) previously caused all FKs to
        # resolve to None → no linked tables → no JOIN_PATHs.
        fk_edges_added = 0
        for fk in metadata.foreign_keys:
            src_parts = fk.source_col_fqn.rsplit(".", 1)
            tgt_parts = fk.target_col_fqn.rsplit(".", 1)
            if len(src_parts) != 2 or len(tgt_parts) != 2:
                continue
            src_table, tgt_table = src_parts[0], tgt_parts[0]
            if src_table == tgt_table:
                continue
            G.add_edge(src_table, tgt_table,
                       src_col=fk.source_col_fqn,
                       tgt_col=fk.target_col_fqn,
                       constraint_name=fk.constraint_name)
            G.add_edge(tgt_table, src_table,
                       src_col=fk.target_col_fqn,
                       tgt_col=fk.source_col_fqn,
                       constraint_name=fk.constraint_name + "_REV")
            fk_edges_added += 1

        logger.info(
            "FK graph: %d FK relationships → %d directed edges across %d unique tables",
            len(metadata.foreign_keys), fk_edges_added * 2,
            len({e for fk in metadata.foreign_keys
                 for e in [fk.source_col_fqn.rsplit(".", 1)[0],
                            fk.target_col_fqn.rsplit(".", 1)[0]]
                 if "." in fk.source_col_fqn and "." in fk.target_col_fqn}),
        )

        # Only process tables that are actually connected via FK edges.
        linked_tables = [n for n in G.nodes() if G.degree(n) > 0]
        if not linked_tables:
            logger.warning(
                "No FK-linked tables found — JOIN_PATH skipped. "
                "FKs extracted: %d. Check FK extraction logs for errors.",
                len(metadata.foreign_keys),
            )
            return []

        logger.info(
            "Computing JOIN_PATHs for %d FK-linked tables (out of %d total)",
            len(linked_tables), len(table_fqns),
        )

        # Materialize a real undirected copy — not a live view.
        # as_view=True shares internal C-level adjacency dicts with the original
        # MultiDiGraph; repeated shortest_path traversals through the view proxy
        # can dereference stale memory in certain NetworkX/CPython builds.
        G_undirected = G.to_undirected(as_view=False)

        join_paths: List[JoinPathRel] = []
        seen_pairs: Set[Tuple[str, str]] = set()

        for i, src in enumerate(linked_tables):
            for tgt in linked_tables[i + 1:]:
                if (src, tgt) in seen_pairs:
                    continue
                try:
                    path_nodes = nx.shortest_path(G_undirected, src, tgt)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue

                if len(path_nodes) - 1 > max_hops:
                    continue

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
                        join_columns=[
                            {"src": jc["tgt"], "tgt": jc["src"], "constraint": jc["constraint"]}
                            for jc in reversed(join_cols)
                        ],
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
          3. Levenshtein edit distance – score based on length-normalised dist

        Strategy 3 is O(n²) and uses a C extension (python-Levenshtein).
        To prevent memory exhaustion and segfaults on large schemas:
          - Columns with names longer than _MAX_COL_NAME are excluded.
          - The strategy is skipped entirely when remaining candidates exceed
            _MAX_COLS_LEVENSHTEIN (the pair count would be prohibitive).
        """
        _MAX_COL_NAME = 64          # skip derived/expression column names
        _MAX_COLS_LEVENSHTEIN = 500   # ~125K pairs max; beyond this the C heap thrashes

        max_dist = self.config.similarity_levenshtein_max
        min_score = self.config.similarity_min_score

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

        # Strategy 3: Levenshtein distance on remaining column pairs.
        # Exclude columns with very long names (view derived expressions) and
        # bail out entirely if the candidate set is too large.
        lev_candidates = [
            c for c in metadata.columns if len(c.name) <= _MAX_COL_NAME
        ]
        if len(lev_candidates) > _MAX_COLS_LEVENSHTEIN:
            logger.warning(
                "Skipping Levenshtein SIMILAR_TO strategy: %d candidate columns "
                "exceeds threshold %d — would produce ~%d comparisons",
                len(lev_candidates), _MAX_COLS_LEVENSHTEIN,
                len(lev_candidates) * (len(lev_candidates) - 1) // 2,
            )
        else:
            # Smoke-test the C extension with a trivial call before entering the
            # O(n²) loop.  A bad install or ABI mismatch in python-Levenshtein
            # can cause SIGSEGV on the very first call; catching it here means
            # we skip the strategy rather than crashing the whole build.
            try:
                _smoke = levenshtein_distance("a", "b")
            except Exception as exc:
                logger.warning("Levenshtein smoke test failed — skipping strategy 3: %s", exc)
                lev_candidates = []

            for i, c1 in enumerate(lev_candidates):
                for c2 in lev_candidates[i + 1:]:
                    if c1.table_fqn == c2.table_fqn:
                        continue
                    n1, n2 = c1.name.upper(), c2.name.upper()
                    key = (min(c1.fqn, c2.fqn), max(c1.fqn, c2.fqn))
                    if key in seen:
                        continue
                    try:
                        dist = levenshtein_distance(n1, n2)
                    except Exception as exc:
                        logger.debug("levenshtein_distance failed for %s/%s: %s", n1, n2, exc)
                        continue
                    if dist <= max_dist:
                        max_len = max(len(n1), len(n2), 1)
                        score = round(1.0 - dist / max_len, 4)
                        if score >= min_score:
                            _add(c1, c2, score, "levenshtein")

        logger.info("Computed %d SIMILAR_TO edges", len(similar_to))
        return similar_to
