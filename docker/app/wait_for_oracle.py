"""
Poll Oracle until a connection succeeds, then exit 0.
Called by entrypoint.sh before schema init and graph build.
Timeout: 300 s (60 × 5 s).
"""
import os
import sys
import time

import oracledb

DSN  = os.environ.get("ORACLE_DSN", "oracle:1521/FREEPDB1")
USER = os.environ.get("ORACLE_USER", "kyc")
PW   = os.environ.get("ORACLE_PASSWORD", "KycPassword1")
TIMEOUT_S  = int(os.environ.get("ORACLE_WAIT_TIMEOUT", "300"))
INTERVAL_S = 5

print(f"Waiting for Oracle at {DSN} (timeout={TIMEOUT_S}s)...", flush=True)

deadline = time.monotonic() + TIMEOUT_S
attempt  = 0

while time.monotonic() < deadline:
    attempt += 1
    try:
        conn = oracledb.connect(user=USER, password=PW, dsn=DSN)
        conn.close()
        print(f"  Oracle ready after {attempt} attempt(s).", flush=True)
        sys.exit(0)
    except Exception as exc:
        remaining = int(deadline - time.monotonic())
        print(f"  [{attempt}] not ready ({exc.__class__.__name__}) — {remaining}s left", flush=True)
        time.sleep(INTERVAL_S)

print("ERROR: Oracle did not become ready within the timeout.", file=sys.stderr)
sys.exit(1)
