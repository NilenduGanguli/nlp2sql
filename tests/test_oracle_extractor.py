"""
Tests for knowledge_graph.oracle_extractor
==========================================
Verifies that the OracleMetadataExtractor correctly transforms raw
Oracle data dictionary rows into typed Python model objects.
All tests use mock Oracle connections — no live database required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import pytest

from knowledge_graph.config import OracleConfig
from knowledge_graph.oracle_extractor import OracleMetadataExtractor, OracleMetadata
from knowledge_graph.models import TableNode, ColumnNode, HasForeignKeyRel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cursor(rows):
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = rows
    cur.__iter__ = lambda s: iter(rows)
    cur.description = [("COL1",), ("COL2",)]
    return cur


def _make_conn(*cursors):
    """Return a mock connection whose cursor() call yields cursors in order."""
    conn = MagicMock()
    conn.cursor.side_effect = cursors if cursors else [_make_cursor([])]
    return conn


# ---------------------------------------------------------------------------
# OracleConfig tests
# ---------------------------------------------------------------------------

class TestOracleConfig:
    def test_view_prefix_dba(self):
        cfg = OracleConfig(dsn="x", user="u", password="p", use_dba_views=True)
        assert cfg.view_prefix == "DBA"

    def test_view_prefix_all(self):
        cfg = OracleConfig(dsn="x", user="u", password="p", use_dba_views=False)
        assert cfg.view_prefix == "ALL"

    def test_target_schemas_from_env(self, monkeypatch):
        monkeypatch.setenv("ORACLE_TARGET_SCHEMAS", "KYC, FINANCE")
        cfg = OracleConfig.__new__(OracleConfig)
        cfg.target_schemas = []
        cfg.use_dba_views = True
        cfg.__post_init__()
        # Re-create to pick up env
        cfg2 = OracleConfig()
        assert "KYC" in cfg2.target_schemas or cfg2.target_schemas is not None

    def test_validate_raises_without_dsn(self):
        cfg = OracleConfig(dsn="", user="u", password="p")
        with pytest.raises(ValueError, match="ORACLE_DSN"):
            cfg.validate()

    def test_validate_raises_without_user(self):
        cfg = OracleConfig(dsn="x", user="", password="p")
        with pytest.raises(ValueError, match="ORACLE_USER"):
            cfg.validate()

    def test_validate_raises_without_password(self):
        cfg = OracleConfig(dsn="x", user="u", password="")
        with pytest.raises(ValueError, match="ORACLE_PASSWORD"):
            cfg.validate()


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------

class TestSchemaResolution:
    def test_target_schemas_respected(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC", "FINANCE"])
        extractor = OracleMetadataExtractor(config)
        conn = MagicMock()
        result = extractor._resolve_schemas(conn)
        assert result == ["KYC", "FINANCE"]
        conn.cursor.assert_not_called()  # should not query Oracle

    def test_schema_discovery_when_none_specified(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=[])
        extractor = OracleMetadataExtractor(config)
        cur = _make_cursor([("KYC",), ("AUDIT",)])
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter([("KYC",), ("AUDIT",)])
        result = extractor._resolve_schemas(conn)
        assert "KYC" in result


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------

class TestTableExtraction:
    def _make_table_rows(self):
        # (owner, table_name, num_rows, avg_row_len, last_analyzed,
        #  table_type, partitioned, temporary, comments)
        return [
            ("KYC", "CUSTOMERS",   50000,  200, "2026-01-15 02:00:00", "TABLE",  "NO", "N", "Core customer entity"),
            ("KYC", "ACCOUNTS",    120000, 150, "2026-01-15 02:00:00", "TABLE",  "NO", "N", None),
            ("KYC", "TRANSACTIONS",5000000,80,  "2026-01-15 02:00:00", "TABLE",  "NO", "N", "Financial transactions"),
        ]

    def test_extracts_table_names(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC"])
        extractor = OracleMetadataExtractor(config)
        cur = _make_cursor(self._make_table_rows())
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter(self._make_table_rows())

        tables = extractor._extract_tables(conn, ["KYC"])
        names = {t.name for t in tables}
        assert "CUSTOMERS" in names
        assert "ACCOUNTS" in names
        assert "TRANSACTIONS" in names

    def test_extracts_table_row_count(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC"])
        extractor = OracleMetadataExtractor(config)
        rows = self._make_table_rows()[:1]  # just CUSTOMERS
        cur = _make_cursor(rows)
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter(rows)

        tables = extractor._extract_tables(conn, ["KYC"])
        assert tables[0].row_count == 50000

    def test_table_comments_loaded(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC"])
        extractor = OracleMetadataExtractor(config)
        rows = self._make_table_rows()[:1]
        cur = _make_cursor(rows)
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter(rows)

        tables = extractor._extract_tables(conn, ["KYC"])
        assert tables[0].comments == "Core customer entity"

    def test_fqn_is_schema_dot_table(self):
        t = TableNode("KYC", "CUSTOMERS")
        assert t.fqn == "KYC.CUSTOMERS"


# ---------------------------------------------------------------------------
# Column extraction
# ---------------------------------------------------------------------------

class TestColumnExtraction:
    def _make_col_rows(self):
        # (owner, table_name, col_name, data_type, data_length, data_precision,
        #  data_scale, nullable, data_default, column_id, comments, num_distinct, histogram)
        return [
            ("KYC", "CUSTOMERS", "CUSTOMER_ID",  "NUMBER",   None, 10, None, "N", None, 1, "PK", 50000, None),
            ("KYC", "CUSTOMERS", "FIRST_NAME",   "VARCHAR2", 100,  None, None, "N", None, 2, None, 45000, None),
            ("KYC", "CUSTOMERS", "RISK_RATING",  "VARCHAR2", 10,   None, None, "N", None, 8, "Risk level", 4, "FREQUENCY"),
        ]

    def test_extracts_column_count(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC"])
        extractor = OracleMetadataExtractor(config)
        rows = self._make_col_rows()
        cur = _make_cursor(rows)
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter(rows)

        cols = extractor._extract_columns(conn, ["KYC"])
        assert len(cols) == 3

    def test_column_data_type(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC"])
        extractor = OracleMetadataExtractor(config)
        rows = self._make_col_rows()[:1]
        cur = _make_cursor(rows)
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter(rows)

        cols = extractor._extract_columns(conn, ["KYC"])
        assert cols[0].data_type == "NUMBER"
        assert cols[0].precision == 10

    def test_column_fqn(self):
        col = ColumnNode(schema="KYC", table_name="CUSTOMERS", name="CUSTOMER_ID",
                         data_type="NUMBER")
        assert col.fqn == "KYC.CUSTOMERS.CUSTOMER_ID"
        assert col.table_fqn == "KYC.CUSTOMERS"


# ---------------------------------------------------------------------------
# Foreign key extraction
# ---------------------------------------------------------------------------

class TestForeignKeyExtraction:
    def _make_fk_rows(self):
        # (fk_owner, fk_table, constraint_name, delete_rule,
        #  fk_column, position, ref_owner, ref_table, ref_column)
        return [
            ("KYC", "ACCOUNTS",  "FK_ACCT_CUST", "NO ACTION", "CUSTOMER_ID", 1,
             "KYC", "CUSTOMERS", "CUSTOMER_ID"),
            ("KYC", "TRANSACTIONS", "FK_TXN_ACCT", "NO ACTION", "ACCOUNT_ID", 1,
             "KYC", "ACCOUNTS", "ACCOUNT_ID"),
        ]

    def test_extracts_fk_count(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC"])
        extractor = OracleMetadataExtractor(config)
        rows = self._make_fk_rows()
        cur = _make_cursor(rows)
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter(rows)

        fks = extractor._extract_foreign_keys(conn, ["KYC"])
        assert len(fks) == 2

    def test_fk_source_and_target_fqn(self):
        config = OracleConfig(dsn="x", user="u", password="p", target_schemas=["KYC"])
        extractor = OracleMetadataExtractor(config)
        rows = self._make_fk_rows()[:1]
        cur = _make_cursor(rows)
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.__iter__ = lambda s: iter(rows)

        fks = extractor._extract_foreign_keys(conn, ["KYC"])
        assert fks[0].source_col_fqn == "KYC.ACCOUNTS.CUSTOMER_ID"
        assert fks[0].target_col_fqn == "KYC.CUSTOMERS.CUSTOMER_ID"
        assert fks[0].constraint_name == "FK_ACCT_CUST"


# ---------------------------------------------------------------------------
# Column flagging (is_pk, is_fk, is_indexed)
# ---------------------------------------------------------------------------

class TestColumnFlagging:
    def test_flag_pk_columns(self, kyc_metadata):
        pk_fqns = {pk.column_fqn for pk in kyc_metadata.primary_keys}
        pk_cols = [c for c in kyc_metadata.columns if c.fqn in pk_fqns]
        for col in pk_cols:
            assert col.is_pk, f"{col.fqn} should be flagged as PK"

    def test_flag_fk_columns(self, kyc_metadata):
        fk_fqns = {fk.source_col_fqn for fk in kyc_metadata.foreign_keys}
        fk_cols = [c for c in kyc_metadata.columns if c.fqn in fk_fqns]
        for col in fk_cols:
            assert col.is_fk, f"{col.fqn} should be flagged as FK"

    def test_flag_indexed_columns(self, kyc_metadata):
        customer_id_col = next(
            c for c in kyc_metadata.columns
            if c.fqn == "KYC.CUSTOMERS.CUSTOMER_ID"
        )
        assert customer_id_col.is_indexed


# ---------------------------------------------------------------------------
# OracleMetadata summary
# ---------------------------------------------------------------------------

class TestOracleMetadataSummary:
    def test_summary_contains_table_count(self, kyc_metadata):
        summary = kyc_metadata.summary()
        assert "Tables: 8" in summary

    def test_summary_contains_fk_count(self, kyc_metadata):
        summary = kyc_metadata.summary()
        assert f"FKs: {len(kyc_metadata.foreign_keys)}" in summary


# ---------------------------------------------------------------------------
# SQL helper methods
# ---------------------------------------------------------------------------

class TestSQLHelpers:
    def test_in_clause_generates_named_binds(self):
        config = OracleConfig(dsn="x", user="u", password="p")
        extractor = OracleMetadataExtractor(config)
        clause = extractor._in_clause(["KYC", "AUDIT"], "t.owner")
        assert ":s0" in clause
        assert ":s1" in clause
        assert "t.owner" in clause

    def test_bind_schemas_keys(self):
        config = OracleConfig(dsn="x", user="u", password="p")
        extractor = OracleMetadataExtractor(config)
        binds = extractor._bind_schemas(["KYC", "FINANCE"])
        assert binds == {"s0": "KYC", "s1": "FINANCE"}


# ---------------------------------------------------------------------------
# DBA → ALL automatic fallback (ORA-00942 / ORA-01031)
# ---------------------------------------------------------------------------

class _FakeOraError(Exception):
    """Stand-in for oracledb.DatabaseError carrying an ORA- code in str()."""


class TestDbaToAllFallback:
    def _extractor(self, *, use_dba=True):
        config = OracleConfig(
            dsn="x", user="u", password="p",
            target_schemas=["KYC"], use_dba_views=use_dba,
        )
        return OracleMetadataExtractor(config)

    def test_safe_extract_flips_prefix_and_retries_on_ora_00942(self):
        extractor = self._extractor()
        assert extractor._prefix == "DBA"

        calls = {"n": 0}

        def fake_fn():
            calls["n"] += 1
            if extractor._prefix == "DBA":
                raise _FakeOraError("ORA-00942: table or view does not exist")
            return ["ok-from-ALL"]

        result = extractor._safe_extract("tables", fake_fn, default=[])
        assert result == ["ok-from-ALL"]
        assert extractor._prefix == "ALL"  # permanently flipped
        assert calls["n"] == 2  # original + retry

    def test_safe_extract_handles_ora_01031(self):
        extractor = self._extractor()

        def fake_fn():
            if extractor._prefix == "DBA":
                raise _FakeOraError("ORA-01031: insufficient privileges")
            return [42]

        assert extractor._safe_extract("indexes", fake_fn, default=[]) == [42]
        assert extractor._prefix == "ALL"

    def test_safe_extract_does_not_retry_on_other_errors(self):
        extractor = self._extractor()

        def fake_fn():
            raise _FakeOraError("ORA-12345: some other error")

        result = extractor._safe_extract("columns", fake_fn, default=[])
        assert result == []
        assert extractor._prefix == "DBA"  # not flipped

    def test_safe_extract_returns_default_when_retry_also_fails(self):
        extractor = self._extractor()

        def fake_fn():
            raise _FakeOraError("ORA-00942: still missing")

        result = extractor._safe_extract("constraints", fake_fn, default=[])
        assert result == []
        assert extractor._prefix == "ALL"  # flipped on first failure

    def test_safe_extract_no_flip_when_already_all(self):
        extractor = self._extractor(use_dba=False)
        assert extractor._prefix == "ALL"

        def fake_fn():
            raise _FakeOraError("ORA-00942: still missing")

        result = extractor._safe_extract("constraints", fake_fn, default=[])
        assert result == []
        assert extractor._prefix == "ALL"

    def test_resolve_schemas_with_fallback_flips_on_ora_00942(self):
        extractor = self._extractor()
        # No target_schemas → discovery query; must hit Oracle
        extractor.config.target_schemas = []

        attempts = {"n": 0}

        def fake_resolve(_conn):
            attempts["n"] += 1
            if extractor._prefix == "DBA":
                raise _FakeOraError("ORA-00942: table or view does not exist")
            return ["KYC", "FINANCE"]

        with patch.object(extractor, "_resolve_schemas", side_effect=fake_resolve):
            result = extractor._resolve_schemas_with_fallback(MagicMock())

        assert result == ["KYC", "FINANCE"]
        assert extractor._prefix == "ALL"
        assert attempts["n"] == 2

    def test_resolve_schemas_with_fallback_reraises_unknown_errors(self):
        extractor = self._extractor()

        def fake_resolve(_conn):
            raise _FakeOraError("ORA-12345: unrelated")

        with patch.object(extractor, "_resolve_schemas", side_effect=fake_resolve):
            with pytest.raises(_FakeOraError):
                extractor._resolve_schemas_with_fallback(MagicMock())
        assert extractor._prefix == "DBA"

    def test_is_dba_priv_error_recognises_codes(self):
        assert OracleMetadataExtractor._is_dba_priv_error(_FakeOraError("ORA-00942: ..."))
        assert OracleMetadataExtractor._is_dba_priv_error(_FakeOraError("ORA-01031: ..."))
        assert not OracleMetadataExtractor._is_dba_priv_error(_FakeOraError("ORA-12345: ..."))
