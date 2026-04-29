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
        if self.config.thick_mode and oracledb.is_thin_mode():
            try:
                oracledb.init_oracle_client()
                logger.info("oracledb thick mode enabled")
            except Exception as exc:
                logger.warning(
                    "Cannot enable thick mode (thin mode already active — "
                    "another connection was made first). Falling back to thin mode. %s", exc
                )
        conn = oracledb.connect(
            user=self.config.user,
            password=self.config.password,
            dsn=self.config.dsn,
        )
        # In thick mode (OCI), LONG columns (ALL_VIEWS.text) default to a
        # 32 767-byte C buffer. Any view definition longer than that causes OCI
        # to write past the buffer boundary → SIGSEGV signal 11.
        # Setting a connection-level output-type handler increases that buffer.
        # IMPORTANT: cursor.var(typ, size) defaults arraysize=1. In thin mode
        # oracledb requires arraysize >= cursor.arraysize (100 by default) or it
        # raises DPY-2016 on fetch. Must pass cursor.arraysize explicitly.
        _LONG_BUFFER = 1024 * 1024  # 1 MB — covers every realistic view definition
        _db_type_long = getattr(oracledb, "DB_TYPE_LONG", None)

        def _long_output_handler(cursor, name, default_type, size, precision, scale):
            if _db_type_long is not None and default_type == _db_type_long:
                return cursor.var(_db_type_long, _LONG_BUFFER, cursor.arraysize)

        conn.outputtypehandler = _long_output_handler
        try:
            return self._extract_all(conn)
        finally:
            conn.close()

    def check_connectivity(self) -> bool:
        """Verify that the Oracle connection can be established."""
        try:
            if self.config.thick_mode and oracledb.is_thin_mode():
                try:
                    oracledb.init_oracle_client()
                except Exception:
                    pass  # fall back to thin mode silently
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

        try:
            schemas = self._resolve_schemas_with_fallback(conn)
        except Exception as exc:
            logger.error("Failed to resolve schemas — aborting extraction: %s", exc)
            return meta

        logger.info("Extracting from schemas: %s (view_prefix=%s)", schemas, self._prefix)
        meta.schemas = [SchemaNode(name=s) for s in schemas]

        meta.tables = self._safe_extract("tables", self._extract_tables, conn, schemas, default=[])
        meta.columns = self._safe_extract("columns", self._extract_columns, conn, schemas, default=[])
        meta.primary_keys = self._safe_extract("primary_keys", self._extract_primary_keys, conn, schemas, default=[])
        meta.foreign_keys = self._safe_extract("foreign_keys", self._extract_foreign_keys, conn, schemas, default=[])
        meta.views = self._safe_extract("views", self._extract_views, conn, schemas, default=[])
        meta.indexes = self._safe_extract("indexes", self._extract_indexes, conn, schemas, default=[])
        meta.constraints = self._safe_extract("constraints", self._extract_constraints, conn, schemas, default=[])
        meta.procedures = self._safe_extract("procedures", self._extract_procedures, conn, schemas, default=[])
        meta.synonyms = self._safe_extract("synonyms", self._extract_synonyms, conn, schemas, default=[])
        meta.sequences = self._safe_extract("sequences", self._extract_sequences, conn, schemas, default=[])
        meta.view_dependencies = self._safe_extract("view_dependencies", self._extract_view_dependencies, conn, schemas, default={})
        meta.indexed_columns = self._build_indexed_column_map(meta.indexes)

        # Flag FK and indexed columns on ColumnNode objects
        self._flag_columns(meta)

        # Collect sample data for each table
        meta.sample_data = self._collect_sample_data(conn, meta.tables, meta.columns)
        self._attach_sample_data(meta)

        logger.info("Extraction complete. %s", meta.summary())
        return meta

    def _safe_extract(self, label: str, fn, *args, default):
        """Call fn(*args); on ORA-00942 (DBA_* not visible) permanently flip
        the view prefix from DBA→ALL and retry once before giving up.
        """
        try:
            return fn(*args)
        except Exception as exc:
            if self._is_dba_priv_error(exc) and self._prefix == "DBA":
                logger.warning(
                    "%s: DBA_* views not accessible (%s). Falling back to ALL_* "
                    "for the rest of this extraction.",
                    label, self._first_error_line(exc),
                )
                self._prefix = "ALL"
                try:
                    return fn(*args)
                except Exception as exc2:
                    logger.warning("Skipping %s after ALL_* retry: %s", label, exc2)
                    return default
            logger.warning("Skipping %s extraction due to error: %s", label, exc)
            return default

    @staticmethod
    def _is_dba_priv_error(exc: Exception) -> bool:
        """ORA-00942 = table/view does not exist (or no SELECT privilege)."""
        msg = str(exc)
        return "ORA-00942" in msg or "ORA-01031" in msg  # 01031 = insufficient privs

    @staticmethod
    def _first_error_line(exc: Exception) -> str:
        return str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)

    # ------------------------------------------------------------------
    # Schema resolution
    # ------------------------------------------------------------------

    def _resolve_schemas_with_fallback(self, conn: oracledb.Connection) -> List[str]:
        """Resolve schemas, downgrading DBA_*→ALL_* on permission errors."""
        try:
            return self._resolve_schemas(conn)
        except Exception as exc:
            if self._is_dba_priv_error(exc) and self._prefix == "DBA":
                logger.warning(
                    "Schema resolution: DBA_TABLES not accessible (%s). "
                    "Falling back to ALL_* for the rest of this extraction.",
                    self._first_error_line(exc),
                )
                self._prefix = "ALL"
                return self._resolve_schemas(conn)
            raise

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
                try:
                    default_raw = row[8]
                    default_str = str(default_raw).strip() if default_raw else None
                    data_type = str(row[3]) if row[3] else "UNKNOWN"
                    columns.append(ColumnNode(
                        schema=row[0],
                        table_name=row[1],
                        name=row[2],
                        data_type=data_type,
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
                except Exception as exc:
                    logger.warning(
                        "Skipping column %s.%s.%s: %s", row[0], row[1], row[2], exc
                    )
        logger.debug("Extracted %d columns", len(columns))
        return columns

    # ------------------------------------------------------------------
    # Primary Keys
    # ------------------------------------------------------------------

    def _extract_primary_keys(
        self, conn: oracledb.Connection, schemas: List[str]
    ) -> List[HasPrimaryKeyRel]:
        # Always use ALL_CONSTRAINTS — accessible to any schema owner without DBA.
        schema_clause = self._in_clause(schemas, "a.owner")
        sql = f"""
            SELECT
                a.owner,
                a.table_name,
                a.constraint_name,
                b.column_name,
                b.position
            FROM ALL_CONSTRAINTS a
            JOIN ALL_CONS_COLUMNS b
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
        # Always use ALL_CONSTRAINTS — accessible to any schema owner without DBA.
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
            FROM ALL_CONSTRAINTS a
            JOIN ALL_CONS_COLUMNS b
                ON b.owner = a.owner AND b.constraint_name = a.constraint_name
            JOIN ALL_CONSTRAINTS c
                ON c.constraint_name = a.r_constraint_name AND c.owner = a.r_owner
            JOIN ALL_CONS_COLUMNS d
                ON d.owner = c.owner
               AND d.constraint_name = c.constraint_name
               AND d.position = b.position
            WHERE {schema_clause}
              AND a.constraint_type = 'R'
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
        # Always use ALL_VIEWS — works for any user privilege level and is more
        # reliable than DBA_VIEWS for standard application accounts.
        # Use v.text directly (always present) instead of DBMS_METADATA.GET_DDL
        # to avoid per-view privilege or object-type errors.
        schema_clause = self._in_clause(schemas, "v.owner")
        sql = f"""
            SELECT
                v.owner,
                v.view_name,
                v.text AS view_text,
                tc.comments
            FROM ALL_VIEWS v
            LEFT JOIN ALL_TAB_COMMENTS tc
                ON tc.owner = v.owner AND tc.table_name = v.view_name
            WHERE {schema_clause}
            ORDER BY v.owner, v.view_name
        """
        views: List[ViewNode] = []
        sql_fallback = sql.replace(
            "v.text AS view_text",
            "SUBSTR(v.text, 1, 32767) AS view_text",
        )
        sql_names_only = f"""
            SELECT v.owner, v.view_name, NULL AS view_text, tc.comments
            FROM ALL_VIEWS v
            LEFT JOIN ALL_TAB_COMMENTS tc
                ON tc.owner = v.owner AND tc.table_name = v.view_name
            WHERE {schema_clause}
            ORDER BY v.owner, v.view_name
        """

        def _build_view_nodes(rows_: list) -> None:
            for row in rows_:
                try:
                    raw_text = row[2]
                    view_text = str(raw_text).strip() if raw_text else None
                    views.append(ViewNode(
                        schema=row[0],
                        name=row[1],
                        view_text=view_text,
                        comments=row[3],
                    ))
                except Exception as exc:
                    logger.warning("Skipping view %s.%s: %s", row[0], row[1], exc)

        fetched = False
        for attempt_sql, label in [
            (sql, "full v.text"),
            (sql_fallback, "SUBSTR(v.text, 32767)"),
            (sql_names_only, "names only (no text)"),
        ]:
            if fetched:
                break
            try:
                with conn.cursor() as cur:
                    cur.execute(attempt_sql, self._bind_schemas(schemas))
                    rows = cur.fetchall()
                _build_view_nodes(rows)
                fetched = True
                if label != "full v.text":
                    logger.warning("Views extracted with fallback strategy: %s", label)
            except Exception as exc:
                logger.warning("View fetch attempt '%s' failed: %s — trying next strategy", label, exc)

        if not fetched:
            logger.error("All view fetch strategies failed; no views will be in the graph")

        # Materialized views
        mview_sql = f"""
            SELECT
                mv.owner,
                mv.mview_name,
                mv.query,
                mv.refresh_mode,
                TO_CHAR(mv.last_refresh_date, 'YYYY-MM-DD HH24:MI:SS') AS last_refresh
            FROM ALL_MVIEWS mv
            WHERE {self._in_clause(schemas, 'mv.owner')}
            ORDER BY mv.owner, mv.mview_name
        """
        try:
            with conn.cursor() as cur:
                cur.execute(mview_sql, self._bind_schemas(schemas))
                for row in cur:
                    try:
                        views.append(ViewNode(
                            schema=row[0],
                            name=row[1],
                            view_text=str(row[2])[:4000] if row[2] else None,
                            is_materialized=True,
                            refresh_mode=row[3],
                            last_refresh=row[4],
                        ))
                    except Exception as exc:
                        logger.warning("Skipping mview %s.%s: %s", row[0], row[1], exc)
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
        # Always use ALL_CONSTRAINTS — accessible to any schema owner without DBA.
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
            FROM ALL_CONSTRAINTS a
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
                NVL(o.status, 'VALID') AS status
            FROM {prefix}_PROCEDURES p
            LEFT JOIN {prefix}_OBJECTS o
                ON o.owner = p.owner
               AND o.object_name = p.object_name
               AND o.object_type = p.object_type
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
        binds = self._bind_schemas(schemas)
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
        self, conn: oracledb.Connection, tables: List[TableNode], columns: List["ColumnNode"]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch a small sample of rows from each table using an explicit column list.

        SELECT * is intentionally avoided: tables may contain XMLTYPE, BLOB,
        LONG, SDO_GEOMETRY, or other driver-unsupported types that cause
        oracledb's C layer to segfault inside fetchall() before Python's
        exception handler can intervene.  By restricting to well-known primitive
        types we guarantee the query is safe to fetch.
        """
        # Types whose values are safe to fetch as plain Python scalars.
        # Anything not in this set (BLOB, CLOB, XMLTYPE, LONG, RAW, object
        # types, etc.) is silently excluded from the sample SELECT.
        _SAFE_PREFIXES = frozenset({
            "CHAR", "NCHAR", "VARCHAR", "NVARCHAR",        # string family
            "NUMBER", "FLOAT", "INTEGER", "SMALLINT",       # numeric family
            "BINARY_FLOAT", "BINARY_DOUBLE",
            "DATE",                                          # date/time family
            "TIMESTAMP", "INTERVAL",
            "BOOLEAN",
        })

        def _is_safe(data_type: str) -> bool:
            dt = (data_type or "").upper().split("(")[0].strip()
            return any(dt.startswith(p) for p in _SAFE_PREFIXES)

        # Build table_fqn → [safe column names] from the already-extracted metadata
        from collections import defaultdict
        safe_col_map: Dict[str, List[str]] = defaultdict(list)
        for col in columns:
            if _is_safe(col.data_type or ""):
                safe_col_map[col.table_fqn].append(col.name)

        result: Dict[str, List[Dict[str, Any]]] = {}
        n = self.config.sample_rows
        for table in tables:
            safe_cols = safe_col_map.get(table.fqn, [])
            if not safe_cols:
                logger.debug("No safe columns to sample for %s.%s", table.schema, table.name)
                result[table.fqn] = []
                continue

            col_list = ", ".join(f'"{c}"' for c in safe_cols[:50])  # cap at 50 cols
            sql = (
                f'SELECT {col_list} FROM "{table.schema}"."{table.name}" '
                f"FETCH FIRST {n} ROWS ONLY"
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    col_names = [d[0] for d in cur.description]
                    rows = []
                    for row in cur.fetchall():
                        row_dict: Dict[str, Any] = {}
                        for col_name, val in zip(col_names, row):
                            if isinstance(val, str) and len(val) > 200:
                                val = val[:200] + "..."
                            row_dict[col_name] = val
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
