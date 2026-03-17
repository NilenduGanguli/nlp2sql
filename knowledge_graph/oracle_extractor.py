"""
Oracle Metadata Extractor
==========================
Async extraction of Oracle data dictionary metadata into typed Python objects
that are then consumed by the graph builder.

Privilege requirements
-----------------------
With use_dba_views=True (default):   SELECT ANY DICTIONARY or DBA role
With use_dba_views=False (fallback): SELECT on ALL_* views (standard user)

The extractor queries:
  DBA_TABLES / ALL_TABLES
  DBA_TAB_COLUMNS / ALL_TAB_COLUMNS
  DBA_CONSTRAINTS / ALL_CONSTRAINTS
  DBA_CONS_COLUMNS / ALL_CONS_COLUMNS
  DBA_VIEWS / ALL_VIEWS
  DBA_MVIEWS / ALL_MVIEWS
  DBA_DEPENDENCIES / ALL_DEPENDENCIES
  DBA_INDEXES / ALL_INDEXES
  DBA_IND_COLUMNS / ALL_IND_COLUMNS
  DBA_PROCEDURES / ALL_PROCEDURES
  DBA_SYNONYMS / ALL_SYNONYMS + PUBLIC_SYNONYMS
  DBA_SEQUENCES / ALL_SEQUENCES
  DBA_TAB_COL_STATISTICS / ALL_TAB_COL_STATISTICS
  DBA_TAB_COMMENTS / ALL_TAB_COMMENTS
  DBA_COL_COMMENTS / ALL_COL_COMMENTS
  DBA_OBJECTS / ALL_OBJECTS  (for incremental refresh timestamps)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import oracledb
except ModuleNotFoundError:  # pragma: no cover
    oracledb = None  # type: ignore[assignment]

from knowledge_graph.config import OracleConfig
from knowledge_graph.models import (
    SchemaNode, TableNode, ColumnNode, ViewNode, IndexNode,
    ConstraintNode, ProcedureNode, SynonymNode, SequenceNode,
    HasForeignKeyRel, HasPrimaryKeyRel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Aggregate result container
# ---------------------------------------------------------------------------

@dataclass
class OracleMetadata:
    """All metadata extracted from Oracle in one structured container."""
    schemas: List[SchemaNode] = field(default_factory=list)
    tables: List[TableNode] = field(default_factory=list)
    columns: List[ColumnNode] = field(default_factory=list)
    views: List[ViewNode] = field(default_factory=list)
    indexes: List[IndexNode] = field(default_factory=list)
    constraints: List[ConstraintNode] = field(default_factory=list)
    procedures: List[ProcedureNode] = field(default_factory=list)
    synonyms: List[SynonymNode] = field(default_factory=list)
    sequences: List[SequenceNode] = field(default_factory=list)
    foreign_keys: List[HasForeignKeyRel] = field(default_factory=list)
    primary_keys: List[HasPrimaryKeyRel] = field(default_factory=list)
    # owner → table_name → list of column names
    indexed_columns: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    # owner → table_name → list of {"view_name": ..., "dependency_type": ...}
    view_dependencies: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    # Sample rows: fqn → list of row dicts
    sample_data: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"Schemas: {len(self.schemas)}, Tables: {len(self.tables)}, "
            f"Columns: {len(self.columns)}, Views: {len(self.views)}, "
            f"Indexes: {len(self.indexes)}, FKs: {len(self.foreign_keys)}, "
            f"PKs: {len(self.primary_keys)}, Procedures: {len(self.procedures)}"
        )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class OracleMetadataExtractor:
    """
    Synchronous Oracle metadata extractor.

    Usage::

        config = OracleConfig(dsn="...", user="...", password="...")
        extractor = OracleMetadataExtractor(config)
        metadata = extractor.extract()
    """

    # Suffix patterns that suggest a column is a FK candidate
    FK_SUFFIX_PATTERNS = ("_ID", "_CODE", "_KEY", "_NO", "_NUM", "_REF")

    def __init__(self, config: OracleConfig) -> None:
        self.config = config
        self._prefix = config.view_prefix

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self) -> OracleMetadata:
        """Run the full extraction pipeline and return an OracleMetadata object."""
        logger.info("Connecting to Oracle DSN=%s USER=%s", self.config.dsn, self.config.user)
        conn = oracledb.connect(
            user=self.config.user,
            password=self.config.password,
            dsn=self.config.dsn,
        )
        try:
            return self._extract_all(conn)
        finally:
            conn.close()

    def check_connectivity(self) -> bool:
        """Verify that the Oracle connection can be established."""
        try:
            conn = oracledb.connect(
                user=self.config.user,
                password=self.config.password,
                dsn=self.config.dsn,
            )
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM DUAL")
            cursor.close()
            conn.close()
            return True
        except Exception as exc:
            logger.error("Oracle connectivity check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _extract_all(self, conn: oracledb.Connection) -> OracleMetadata:
        meta = OracleMetadata()

        schemas = self._resolve_schemas(conn)
        logger.info("Extracting from schemas: %s", schemas)

        meta.schemas = [SchemaNode(name=s) for s in schemas]
        meta.tables = self._extract_tables(conn, schemas)
        meta.columns = self._extract_columns(conn, schemas)
        meta.primary_keys = self._extract_primary_keys(conn, schemas)
        meta.foreign_keys = self._extract_foreign_keys(conn, schemas)
        meta.views = self._extract_views(conn, schemas)
        meta.indexes = self._extract_indexes(conn, schemas)
        meta.constraints = self._extract_constraints(conn, schemas)
        meta.procedures = self._extract_procedures(conn, schemas)
        meta.synonyms = self._extract_synonyms(conn, schemas)
        meta.sequences = self._extract_sequences(conn, schemas)
        meta.view_dependencies = self._extract_view_dependencies(conn, schemas)
        meta.indexed_columns = self._build_indexed_column_map(meta.indexes)

        # Flag FK and indexed columns on ColumnNode objects
        self._flag_columns(meta)

        # Collect sample data for each table
        meta.sample_data = self._collect_sample_data(conn, meta.tables)
        self._attach_sample_data(meta)

        logger.info("Extraction complete. %s", meta.summary())
        return meta

    # ------------------------------------------------------------------
    # Schema resolution
    # ------------------------------------------------------------------

    def _resolve_schemas(self, conn: oracledb.Connection) -> List[str]:
        """Return the list of schema names to introspect."""
        if self.config.target_schemas:
            return [s.upper() for s in self.config.target_schemas]
        # Discover from objects accessible to the service user
        prefix = self._prefix
        sql = f"SELECT DISTINCT owner FROM {prefix}_TABLES ORDER BY owner"
        with conn.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _extract_tables(self, conn: oracledb.Connection, schemas: List[str]) -> List[TableNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "t.owner")
        sql = f"""
            SELECT
                t.owner,
                t.table_name,
                t.num_rows,
                t.avg_row_len,
                TO_CHAR(t.last_analyzed, 'YYYY-MM-DD HH24:MI:SS') AS last_analyzed,
                NVL(t.iot_type, 'TABLE') AS table_type,
                t.partitioned,
                t.temporary,
                tc.comments
            FROM {prefix}_TABLES t
            LEFT JOIN {prefix}_TAB_COMMENTS tc
                ON tc.owner = t.owner AND tc.table_name = t.table_name
                AND tc.table_type = 'TABLE'
            WHERE {schema_clause}
              AND (t.iot_type IS NULL OR t.iot_type != 'IOT_OVERFLOW')
            ORDER BY t.owner, t.table_name
        """
        tables: List[TableNode] = []
        with conn.cursor() as cur:
            cur.execute(sql, self._bind_schemas(schemas))
            for row in cur:
                tables.append(TableNode(
                    name=row[1],
                    schema=row[0],
                    row_count=row[2],
                    avg_row_length=row[3],
                    last_analyzed=row[4],
                    table_type=row[5],
                    partitioned=row[6] or "NO",
                    temporary=row[7] or "N",
                    comments=row[8],
                ))
        logger.debug("Extracted %d tables", len(tables))
        return tables

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------

    def _extract_columns(self, conn: oracledb.Connection, schemas: List[str]) -> List[ColumnNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "c.owner")
        sql = f"""
            SELECT
                c.owner,
                c.table_name,
                c.column_name,
                c.data_type,
                c.data_length,
                c.data_precision,
                c.data_scale,
                c.nullable,
                c.data_default,
                c.column_id,
                cc.comments,
                s.num_distinct,
                s.histogram
            FROM {prefix}_TAB_COLUMNS c
            LEFT JOIN {prefix}_COL_COMMENTS cc
                ON cc.owner = c.owner
               AND cc.table_name = c.table_name
               AND cc.column_name = c.column_name
            LEFT JOIN {prefix}_TAB_COL_STATISTICS s
                ON s.owner = c.owner
               AND s.table_name = c.table_name
               AND s.column_name = c.column_name
            WHERE {schema_clause}
            ORDER BY c.owner, c.table_name, c.column_id
        """
        columns: List[ColumnNode] = []
        with conn.cursor() as cur:
            cur.execute(sql, self._bind_schemas(schemas))
            for row in cur:
                default_raw = row[8]
                default_str = str(default_raw).strip() if default_raw else None
                columns.append(ColumnNode(
                    schema=row[0],
                    table_name=row[1],
                    name=row[2],
                    data_type=row[3],
                    data_length=row[4],
                    precision=row[5],
                    scale=row[6],
                    nullable=row[7],
                    default_value=default_str,
                    column_id=row[9] or 0,
                    comments=row[10],
                    num_distinct=row[11],
                    histogram_type=row[12],
                ))
        logger.debug("Extracted %d columns", len(columns))
        return columns

    # ------------------------------------------------------------------
    # Primary Keys
    # ------------------------------------------------------------------

    def _extract_primary_keys(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> List[HasPrimaryKeyRel]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "a.owner")
        sql = f"""
            SELECT
                a.owner,
                a.table_name,
                a.constraint_name,
                b.column_name,
                b.position
            FROM {prefix}_CONSTRAINTS a
            JOIN {prefix}_CONS_COLUMNS b
                ON b.owner = a.owner AND b.constraint_name = a.constraint_name
            WHERE {schema_clause}
              AND a.constraint_type = 'P'
              AND a.status = 'ENABLED'
            ORDER BY a.owner, a.table_name, b.position
        """
        pks: List[HasPrimaryKeyRel] = []
        with conn.cursor() as cur:
            cur.execute(sql, self._bind_schemas(schemas))
            for row in cur:
                owner, table_name, con_name, col_name, position = row
                table_fqn = f"{owner.upper()}.{table_name.upper()}"
                col_fqn = f"{owner.upper()}.{table_name.upper()}.{col_name.upper()}"
                pks.append(HasPrimaryKeyRel(
                    table_fqn=table_fqn,
                    column_fqn=col_fqn,
                    constraint_name=con_name,
                    key_position=position or 1,
                ))
        logger.debug("Extracted %d PK columns", len(pks))
        return pks

    # ------------------------------------------------------------------
    # Foreign Keys
    # ------------------------------------------------------------------

    def _extract_foreign_keys(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> List[HasForeignKeyRel]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "a.owner")
        sql = f"""
            SELECT
                a.owner                     AS fk_owner,
                a.table_name                AS fk_table,
                a.constraint_name,
                NVL(a.delete_rule, 'NO ACTION') AS delete_rule,
                b.column_name               AS fk_column,
                b.position,
                c.owner                     AS ref_owner,
                c.table_name                AS ref_table,
                d.column_name               AS ref_column
            FROM {prefix}_CONSTRAINTS a
            JOIN {prefix}_CONS_COLUMNS b
                ON b.owner = a.owner AND b.constraint_name = a.constraint_name
            JOIN {prefix}_CONSTRAINTS c
                ON c.constraint_name = a.r_constraint_name AND c.owner = a.r_owner
            JOIN {prefix}_CONS_COLUMNS d
                ON d.owner = c.owner
               AND d.constraint_name = c.constraint_name
               AND d.position = b.position
            WHERE {schema_clause}
              AND a.constraint_type = 'R'
              AND a.status = 'ENABLED'
            ORDER BY a.owner, a.table_name, a.constraint_name, b.position
        """
        fks: List[HasForeignKeyRel] = []
        with conn.cursor() as cur:
            cur.execute(sql, self._bind_schemas(schemas))
            for row in cur:
                fk_owner, fk_table, con_name, delete_rule, fk_col, _, ref_owner, ref_table, ref_col = row
                src_fqn = f"{fk_owner.upper()}.{fk_table.upper()}.{fk_col.upper()}"
                tgt_fqn = f"{ref_owner.upper()}.{ref_table.upper()}.{ref_col.upper()}"
                fks.append(HasForeignKeyRel(
                    source_col_fqn=src_fqn,
                    target_col_fqn=tgt_fqn,
                    constraint_name=con_name,
                    on_delete_action=delete_rule,
                ))
        logger.debug("Extracted %d FK relationships", len(fks))
        return fks

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def _extract_views(self, conn: oracledb.Connection, schemas: List[str]) -> List[ViewNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "v.owner")
        sql = f"""
            SELECT
                v.owner,
                v.view_name,
                DBMS_METADATA.GET_DDL('VIEW', v.view_name, v.owner) AS view_text,
                tc.comments
            FROM {prefix}_VIEWS v
            LEFT JOIN {prefix}_TAB_COMMENTS tc
                ON tc.owner = v.owner AND tc.table_name = v.view_name
            WHERE {schema_clause}
            ORDER BY v.owner, v.view_name
        """
        views: List[ViewNode] = []
        with conn.cursor() as cur:
            # DBMS_METADATA can fail on some views; fall back to v.text
            try:
                cur.execute(sql, self._bind_schemas(schemas))
                rows = cur.fetchall()
            except Exception:
                sql_fallback = sql.replace(
                    "DBMS_METADATA.GET_DDL('VIEW', v.view_name, v.owner) AS view_text",
                    "SUBSTR(v.text, 1, 4000) AS view_text",
                )
                cur.execute(sql_fallback, self._bind_schemas(schemas))
                rows = cur.fetchall()

            for row in rows:
                raw_text = row[2]
                view_text = str(raw_text) if raw_text else None
                views.append(ViewNode(
                    schema=row[0],
                    name=row[1],
                    view_text=view_text,
                    comments=row[3],
                ))

        # Materialized views
        mview_sql = f"""
            SELECT
                mv.owner,
                mv.mview_name,
                mv.query,
                mv.refresh_mode,
                TO_CHAR(mv.last_refresh_date, 'YYYY-MM-DD HH24:MI:SS') AS last_refresh
            FROM {prefix}_MVIEWS mv
            WHERE {self._in_clause(schemas, 'mv.owner')}
            ORDER BY mv.owner, mv.mview_name
        """
        with conn.cursor() as cur:
            try:
                cur.execute(mview_sql, self._bind_schemas(schemas))
                for row in cur:
                    views.append(ViewNode(
                        schema=row[0],
                        name=row[1],
                        view_text=str(row[2])[:4000] if row[2] else None,
                        is_materialized=True,
                        refresh_mode=row[3],
                        last_refresh=row[4],
                    ))
            except Exception as exc:
                logger.warning("Could not extract materialized views: %s", exc)

        logger.debug("Extracted %d views", len(views))
        return views

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------

    def _extract_indexes(self, conn: oracledb.Connection, schemas: List[str]) -> List[IndexNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "i.owner")
        sql = f"""
            SELECT
                i.owner,
                i.index_name,
                i.table_name,
                i.index_type,
                i.uniqueness,
                i.tablespace_name,
                i.compression
            FROM {prefix}_INDEXES i
            WHERE {schema_clause}
              AND i.index_type NOT IN ('LOB', 'CLUSTER')
            ORDER BY i.owner, i.table_name, i.index_name
        """
        indexes: List[IndexNode] = []
        index_map: Dict[str, IndexNode] = {}

        with conn.cursor() as cur:
            cur.execute(sql, self._bind_schemas(schemas))
            for row in cur:
                owner, idx_name, table_name, idx_type, uniqueness, ts, compression = row
                idx = IndexNode(
                    name=idx_name,
                    schema=owner,
                    table_name=table_name,
                    index_type=idx_type or "NORMAL",
                    uniqueness=uniqueness or "NONUNIQUE",
                    tablespace=ts,
                    compression=compression or "DISABLED",
                )
                indexes.append(idx)
                index_map[idx.fqn] = idx

        # Attach column list
        col_sql = f"""
            SELECT
                ic.index_owner,
                ic.index_name,
                ic.column_name,
                ic.column_position
            FROM {prefix}_IND_COLUMNS ic
            WHERE {self._in_clause(schemas, 'ic.index_owner')}
            ORDER BY ic.index_owner, ic.index_name, ic.column_position
        """
        from collections import defaultdict
        idx_columns: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        with conn.cursor() as cur:
            cur.execute(col_sql, self._bind_schemas(schemas))
            for row in cur:
                idx_owner, idx_name, col_name, col_pos = row
                fqn = f"{idx_owner.upper()}.{idx_name.upper()}"
                idx_columns[fqn].append((col_name, col_pos))

        for fqn, cols in idx_columns.items():
            if fqn in index_map:
                sorted_cols = sorted(cols, key=lambda x: x[1])
                index_map[fqn].columns_list = ",".join(c[0] for c in sorted_cols)

        logger.debug("Extracted %d indexes", len(indexes))
        return indexes

    # ------------------------------------------------------------------
    # Constraints (all types for Constraint nodes)
    # ------------------------------------------------------------------

    def _extract_constraints(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> List[ConstraintNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "a.owner")
        sql = f"""
            SELECT
                a.owner,
                a.table_name,
                a.constraint_name,
                a.constraint_type,
                a.search_condition,
                a.status,
                a.validated
            FROM {prefix}_CONSTRAINTS a
            WHERE {schema_clause}
              AND a.constraint_type IN ('P', 'R', 'U', 'C')
              AND a.constraint_name NOT LIKE 'SYS_%'
            ORDER BY a.owner, a.table_name, a.constraint_type, a.constraint_name
        """
        constraints: List[ConstraintNode] = []
        with conn.cursor() as cur:
            cur.execute(sql, self._bind_schemas(schemas))
            for row in cur:
                owner, table_name, con_name, con_type, condition, status, validated = row
                cond_str = None
                if condition:
                    try:
                        cond_str = str(condition).strip()[:500]
                    except Exception:
                        pass
                constraints.append(ConstraintNode(
                    schema=owner,
                    table_name=table_name,
                    name=con_name,
                    constraint_type=con_type,
                    condition=cond_str,
                    status=status or "ENABLED",
                    validated=validated or "VALIDATED",
                ))
        logger.debug("Extracted %d constraints", len(constraints))
        return constraints

    # ------------------------------------------------------------------
    # Procedures
    # ------------------------------------------------------------------

    def _extract_procedures(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> List[ProcedureNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "p.owner")
        sql = f"""
            SELECT DISTINCT
                p.owner,
                p.object_name,
                p.object_type,
                p.status
            FROM {prefix}_PROCEDURES p
            WHERE {schema_clause}
              AND p.object_type IN ('PROCEDURE', 'FUNCTION', 'PACKAGE')
            ORDER BY p.owner, p.object_type, p.object_name
        """
        procedures: List[ProcedureNode] = []
        with conn.cursor() as cur:
            cur.execute(sql, self._bind_schemas(schemas))
            for row in cur:
                owner, obj_name, obj_type, status = row
                procedures.append(ProcedureNode(
                    schema=owner,
                    name=obj_name,
                    proc_type=obj_type,
                    status=status or "VALID",
                ))
        logger.debug("Extracted %d procedures/functions/packages", len(procedures))
        return procedures

    # ------------------------------------------------------------------
    # Synonyms
    # ------------------------------------------------------------------

    def _extract_synonyms(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> List[SynonymNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "s.owner")
        sql = f"""
            SELECT
                s.owner,
                s.synonym_name,
                s.table_owner,
                s.table_name
            FROM {prefix}_SYNONYMS s
            WHERE ({schema_clause} OR s.owner = 'PUBLIC')
              AND s.table_owner IN ({self._placeholder_list(len(schemas))})
            ORDER BY s.owner, s.synonym_name
        """
        synonyms: List[SynonymNode] = []
        binds = self._bind_schemas(schemas) + self._bind_schemas(schemas)
        with conn.cursor() as cur:
            try:
                cur.execute(sql, binds)
                for row in cur:
                    owner, syn_name, tgt_owner, tgt_obj = row
                    if tgt_owner and tgt_obj:
                        synonyms.append(SynonymNode(
                            schema=owner,
                            name=syn_name,
                            target_schema=tgt_owner,
                            target_object=tgt_obj,
                        ))
            except Exception as exc:
                logger.warning("Could not extract synonyms: %s", exc)
        logger.debug("Extracted %d synonyms", len(synonyms))
        return synonyms

    # ------------------------------------------------------------------
    # Sequences
    # ------------------------------------------------------------------

    def _extract_sequences(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> List[SequenceNode]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "s.sequence_owner")
        sql = f"""
            SELECT
                s.sequence_owner,
                s.sequence_name,
                s.min_value,
                s.max_value,
                s.increment_by,
                s.cache_size
            FROM {prefix}_SEQUENCES s
            WHERE {schema_clause}
            ORDER BY s.sequence_owner, s.sequence_name
        """
        sequences: List[SequenceNode] = []
        with conn.cursor() as cur:
            try:
                cur.execute(sql, self._bind_schemas(schemas))
                for row in cur:
                    owner, seq_name, min_v, max_v, inc, cache = row
                    sequences.append(SequenceNode(
                        schema=owner,
                        name=seq_name,
                        min_value=int(min_v) if min_v is not None else None,
                        max_value=int(max_v) if max_v is not None else None,
                        increment_by=int(inc) if inc else 1,
                        cache_size=int(cache) if cache else 20,
                    ))
            except Exception as exc:
                logger.warning("Could not extract sequences: %s", exc)
        logger.debug("Extracted %d sequences", len(sequences))
        return sequences

    # ------------------------------------------------------------------
    # View Dependencies
    # ------------------------------------------------------------------

    def _extract_view_dependencies(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> Dict[str, List[Dict[str, str]]]:
        prefix = self._prefix
        schema_clause = self._in_clause(schemas, "d.owner")
        sql = f"""
            SELECT
                d.owner,
                d.name           AS view_name,
                d.referenced_owner,
                d.referenced_name AS table_name,
                d.referenced_type
            FROM {prefix}_DEPENDENCIES d
            WHERE {schema_clause}
              AND d.type = 'VIEW'
              AND d.referenced_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
            ORDER BY d.owner, d.name
        """
        result: Dict[str, List[Dict[str, str]]] = {}
        with conn.cursor() as cur:
            try:
                cur.execute(sql, self._bind_schemas(schemas))
                for row in cur:
                    owner, view_name, ref_owner, ref_table, ref_type = row
                    view_fqn = f"{owner.upper()}.{view_name.upper()}"
                    table_fqn = f"{ref_owner.upper()}.{ref_table.upper()}"
                    if view_fqn not in result:
                        result[view_fqn] = []
                    result[view_fqn].append({
                        "table_fqn": table_fqn,
                        "dependency_type": ref_type,
                    })
            except Exception as exc:
                logger.warning("Could not extract view dependencies: %s", exc)
        return result

    # ------------------------------------------------------------------
    # Sample Data
    # ------------------------------------------------------------------

    def _collect_sample_data(
        self, conn: oracledb.Connection, tables: List[TableNode]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch a small sample of rows from each table.
        Uses FETCH FIRST N ROWS ONLY (Oracle 12c+).
        Rows are returned as dicts keyed by column name.
        """
        result: Dict[str, List[Dict[str, Any]]] = {}
        n = self.config.sample_rows
        for table in tables:
            sql = (
                f'SELECT * FROM "{table.schema}"."{table.name}" '
                f"FETCH FIRST {n} ROWS ONLY"
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    col_names = [d[0] for d in cur.description]
                    rows = []
                    for row in cur.fetchall():
                        row_dict: Dict[str, Any] = {}
                        for col, val in zip(col_names, row):
                            # Truncate large values to keep graph lean
                            if isinstance(val, str) and len(val) > 200:
                                val = val[:200] + "..."
                            row_dict[col] = val
                        rows.append(row_dict)
                    result[table.fqn] = rows
            except Exception as exc:
                logger.debug(
                    "Could not sample %s.%s: %s", table.schema, table.name, exc
                )
                result[table.fqn] = []
        return result

    # ------------------------------------------------------------------
    # Post-processing helpers
    # ------------------------------------------------------------------

    def _flag_columns(self, meta: OracleMetadata) -> None:
        """Set is_pk, is_fk, is_indexed flags on ColumnNode objects."""
        pk_fqns = {pk.column_fqn for pk in meta.primary_keys}
        fk_fqns = {fk.source_col_fqn for fk in meta.foreign_keys}
        indexed_fqns: set = set()
        for idx in meta.indexes:
            schema = idx.schema
            table = idx.table_name
            for col_name in idx.columns_list.split(","):
                if col_name.strip():
                    indexed_fqns.add(f"{schema.upper()}.{table.upper()}.{col_name.strip().upper()}")

        for col in meta.columns:
            col.is_pk = col.fqn in pk_fqns
            col.is_fk = col.fqn in fk_fqns
            col.is_indexed = col.fqn in indexed_fqns

    def _build_indexed_column_map(
        self, indexes: List[IndexNode]
    ) -> Dict[str, Dict[str, List[str]]]:
        result: Dict[str, Dict[str, List[str]]] = {}
        for idx in indexes:
            schema = idx.schema.upper()
            table = idx.table_name.upper()
            if schema not in result:
                result[schema] = {}
            if table not in result[schema]:
                result[schema][table] = []
            for col in idx.columns_list.split(","):
                if col.strip():
                    result[schema][table].append(col.strip().upper())
        return result

    def _attach_sample_data(self, meta: OracleMetadata) -> None:
        """Attach sample data rows to TableNode objects."""
        for table in meta.tables:
            table.sample_data = meta.sample_data.get(table.fqn, [])
            # Populate sample_values on ColumnNode objects
            if table.sample_data:
                col_map = {
                    col.name.upper(): col
                    for col in meta.columns
                    if col.schema == table.schema and col.table_name == table.name
                }
                for col_name, col_node in col_map.items():
                    values = [
                        row[col_name] for row in table.sample_data
                        if col_name in row and row[col_name] is not None
                    ]
                    col_node.sample_values = [str(v) for v in values[:5]]

    # ------------------------------------------------------------------
    # SQL helpers
    # ------------------------------------------------------------------

    def _in_clause(self, schemas: List[str], column: str) -> str:
        """Build: column IN (:s1, :s2, ...)"""
        placeholders = ", ".join(f":s{i}" for i in range(len(schemas)))
        return f"{column} IN ({placeholders})"

    def _placeholder_list(self, n: int) -> str:
        return ", ".join(f":s{i}" for i in range(n))

    def _bind_schemas(self, schemas: List[str]) -> Dict[str, str]:
        return {f"s{i}": s.upper() for i, s in enumerate(schemas)}
