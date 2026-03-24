"""
Ensure the KYC schema exists in Oracle.
If fewer than 8 tables are found the DDL + DML init scripts are applied
directly via the oracledb Python driver (no sqlplus required).

Called by entrypoint.sh after Oracle is reachable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import oracledb

DSN  = os.environ.get("ORACLE_DSN", "oracle:1521/FREEPDB1")
USER = os.environ.get("ORACLE_USER", "kyc")
PW   = os.environ.get("ORACLE_PASSWORD", "KycPassword1")

if os.environ.get("ORACLE_THICK_MODE", "false").lower() == "true" and oracledb.is_thin_mode():
    oracledb.init_oracle_client(lib_dir=os.environ.get("ORACLE_LIB_DIR") or None)

INIT_DIR    = Path(__file__).parent.parent / "init"
SQL_SCRIPTS = ["01_create_tables.sql", "02_load_data.sql"]
MIN_TABLES  = 8


def connect() -> oracledb.Connection:
    return oracledb.connect(user=USER, password=PW, dsn=DSN)


def count_tables(conn: oracledb.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM user_tables")
        return cur.fetchone()[0]


def parse_statements(sql: str) -> list[str]:
    """Split SQL file content into individual executable statements.

    Strips single-line comments, joins continuation lines, splits on ';'.
    Handles Oracle DDL/DML — not PL/SQL blocks (those use /).
    """
    lines = []
    for raw in sql.splitlines():
        without_comment = raw.split("--")[0].rstrip()
        if without_comment.strip():
            lines.append(without_comment)

    full = " ".join(lines)
    stmts = []
    for stmt in full.split(";"):
        stmt = stmt.strip().rstrip("/").strip()
        if stmt:
            stmts.append(stmt)
    return stmts


def run_script(conn: oracledb.Connection, script_path: Path) -> None:
    sql = script_path.read_text()
    stmts = parse_statements(sql)
    with conn.cursor() as cur:
        for stmt in stmts:
            try:
                cur.execute(stmt)
            except oracledb.DatabaseError as exc:
                (error,) = exc.args
                # ORA-00955 = name already used (table exists) — safe to ignore
                # ORA-02292 = FK violation on re-insert — safe to ignore
                if error.code in (955, 2292):
                    continue
                print(f"    WARNING [{error.code}]: {error.message.strip()}", flush=True)
    conn.commit()


def main() -> None:
    conn = connect()
    n = count_tables(conn)
    conn.close()

    if n >= MIN_TABLES:
        print(f"Schema ready — {n} tables found.", flush=True)
        return

    print(f"Only {n}/{MIN_TABLES} tables found — initialising KYC schema...", flush=True)

    for script_name in SQL_SCRIPTS:
        path = INIT_DIR / script_name
        if not path.exists():
            print(f"ERROR: init script not found: {path}", file=sys.stderr)
            sys.exit(1)
        print(f"  Running {script_name}...", flush=True)
        conn = connect()
        run_script(conn, path)
        conn.close()
        print(f"  {script_name}: done", flush=True)

    # Verify
    conn = connect()
    final = count_tables(conn)
    conn.close()

    if final < MIN_TABLES:
        print(f"ERROR: schema init failed — only {final} tables after init.", file=sys.stderr)
        sys.exit(1)

    print(f"Schema initialised — {final} tables ready.", flush=True)


if __name__ == "__main__":
    main()
