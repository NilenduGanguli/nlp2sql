#!/usr/bin/env bash
# =============================================================================
# scripts/e2e_test.sh
# Starts Oracle in Docker, waits for it to be ready, then runs E2E tests.
# =============================================================================
set -euo pipefail

COMPOSE_FILE="$(cd "$(dirname "$0")/.." && pwd)/docker/docker-compose.yml"
MAX_WAIT=180   # seconds to wait for Oracle to be healthy

export ORACLE_DSN="${ORACLE_DSN:-localhost:1521/FREEPDB1}"
export ORACLE_USER="${ORACLE_USER:-kyc}"
export ORACLE_PASSWORD="${ORACLE_PASSWORD:-KycPassword1}"
export ORACLE_SCHEMA="${ORACLE_SCHEMA:-KYC}"

echo "=== KnowledgeQL E2E Test Runner ==="
echo "Oracle: ${ORACLE_DSN}  user=${ORACLE_USER}"
echo ""

# ── 1. Start Oracle container ─────────────────────────────────────────────────
echo "▶ Starting Oracle Free container..."
docker compose -f "$COMPOSE_FILE" up -d

# ── 2. Wait for healthy status ────────────────────────────────────────────────
echo "▶ Waiting for Oracle to be healthy (max ${MAX_WAIT}s)..."
SECONDS_WAITED=0
while true; do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' nlp2sql_oracle 2>/dev/null || echo "missing")
    if [ "$STATUS" = "healthy" ]; then
        echo "   ✓ Oracle is healthy (${SECONDS_WAITED}s waited)"
        break
    fi
    if [ "$SECONDS_WAITED" -ge "$MAX_WAIT" ]; then
        echo "   ✗ Timed out waiting for Oracle health check"
        docker compose -f "$COMPOSE_FILE" logs --tail=30 oracle
        exit 1
    fi
    echo "   … status=${STATUS} (${SECONDS_WAITED}s)"
    sleep 10
    SECONDS_WAITED=$((SECONDS_WAITED + 10))
done

# Extra settling time for PDB startup and init scripts
echo "▶ Allowing 20s for PDB init scripts to complete..."
sleep 20

# ── 3. Verify connectivity and ensure tables exist ────────────────────────────
echo "▶ Verifying Python oracledb connection and table setup..."
python - <<'PYEOF'
import oracledb, os, sys

user     = os.environ["ORACLE_USER"]
password = os.environ["ORACLE_PASSWORD"]
dsn      = os.environ["ORACLE_DSN"]
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
except Exception as e:
    print(f"   ✗ Connection failed: {e}", file=sys.stderr)
    sys.exit(1)

with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM user_tables")
    table_count = cur.fetchone()[0]

if table_count < 8:
    print(f"   ⚠ Only {table_count} tables found — running setup scripts manually...")
    import subprocess
    for script in ["01_create_tables.sql", "02_load_data.sql"]:
        path = os.path.join(repo_root, "docker", "init", script)
        result = subprocess.run(
            ["docker", "exec", "nlp2sql_oracle",
             "sqlplus", "-S", f"{user}/{password}@localhost:1521/FREEPDB1",
             f"@/docker-entrypoint-initdb.d/{script}"],
            capture_output=True, text=True,
        )
        print(f"   … {script}: {'OK' if result.returncode == 0 else 'WARN'}")

with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM customers")
    count = cur.fetchone()[0]

conn.close()
print(f"   ✓ Connected — customers has {count} rows")
PYEOF

# ── 4. Run unit tests (make sure nothing regressed) ───────────────────────────
echo ""
echo "▶ Running unit test suite..."
python -m pytest tests/ \
    --ignore=tests/test_e2e.py \
    -q --tb=short

# ── 5. Run E2E tests ──────────────────────────────────────────────────────────
echo ""
echo "▶ Running end-to-end tests against live Oracle..."
python -m pytest tests/test_e2e.py -v --tb=short

echo ""
echo "=== All tests complete ==="
echo ""
echo "Oracle container is still running. To stop it:"
echo "  docker compose -f docker/docker-compose.yml down"
