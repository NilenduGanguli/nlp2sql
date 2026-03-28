#!/usr/bin/env bash
# =============================================================================
# run.sh — KnowledgeQL full-stack launcher
#
# Stages:
#   1. Load .env and validate prerequisites
#   2. Install / verify Python dependencies
#   3. Start Oracle Free in Docker and wait for healthy status
#   4. Ensure KYC schema and sample data exist
#   5. Run unit test suite (152 tests)
#   6. Run E2E test suite against live Oracle (30 tests)
#   7. Launch Streamlit app
#
# Usage:
#   bash run.sh                  # full run (tests + app)
#   bash run.sh --skip-tests     # skip test suites, just start the app
#   bash run.sh --tests-only     # run tests, do not launch app
#   bash run.sh --no-docker      # skip Docker steps (Oracle already running)
# =============================================================================
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*" >&2; }
hdr()  { echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }

ROOT="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$ROOT/docker/docker-compose.yml"
ENV_FILE="$ROOT/.env"
STREAMLIT_PORT=8501
MAX_ORACLE_WAIT=180   # seconds

# ── Parse flags ───────────────────────────────────────────────────────────────
SKIP_TESTS=false
TESTS_ONLY=false
NO_DOCKER=false
for arg in "$@"; do
  case $arg in
    --skip-tests)  SKIP_TESTS=true ;;
    --tests-only)  TESTS_ONLY=true ;;
    --no-docker)   NO_DOCKER=true  ;;
    --help|-h)
      sed -n '/^# Usage/,/^# ===/p' "$0" | grep -v '==='
      exit 0 ;;
  esac
done

# =============================================================================
# STAGE 1 — Prerequisites & .env
# =============================================================================
hdr "Stage 1: Prerequisites"

# Load .env
if [ -f "$ENV_FILE" ]; then
  set -o allexport
  # shellcheck disable=SC1090
  source <(grep -v '^\s*#' "$ENV_FILE" | grep '=')
  set +o allexport
  ok "Loaded $ENV_FILE"
else
  warn ".env not found — using defaults and environment variables"
fi

# Apply defaults
ORACLE_DSN="${ORACLE_DSN:-localhost:1521/FREEPDB1}"
ORACLE_USER="${ORACLE_USER:-kyc}"
ORACLE_PASSWORD="${ORACLE_PASSWORD:-KycPassword1}"
ORACLE_SCHEMA="${ORACLE_SCHEMA:-${ORACLE_TARGET_SCHEMAS:-KYC}}"

export ORACLE_DSN ORACLE_USER ORACLE_PASSWORD ORACLE_SCHEMA

# Check required tools
for cmd in docker python; do
  if ! command -v "$cmd" &>/dev/null; then
    err "$cmd not found — please install it first"
    exit 1
  fi
done
ok "docker $(docker --version | awk '{print $3}' | tr -d ',')"
ok "python $(python --version | awk '{print $2}')"

# Check .env has LLM key
if [ -z "${LLM_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  warn "No LLM API key found — pipeline will use keyword-based fallback mode"
  LLM_MODE="fallback"
else
  LLM_MODE="llm"
  ok "LLM key present (provider=${LLM_PROVIDER:-openai})"
fi

# =============================================================================
# STAGE 2 — Python Dependencies
# =============================================================================
hdr "Stage 2: Python dependencies"

REQUIRED_PKGS="oracledb streamlit langchain langgraph sqlglot pandas plotly pydantic_settings"
MISSING=""
for pkg in $REQUIRED_PKGS; do
  python -c "import ${pkg//-/_}" 2>/dev/null || MISSING="$MISSING $pkg"
done

if [ -n "$MISSING" ]; then
  warn "Installing missing packages:$MISSING"
  pip install $MISSING -q
  ok "Packages installed"
else
  ok "All required packages present"
fi

# =============================================================================
# STAGE 3 — Oracle Docker
# =============================================================================
if [ "$NO_DOCKER" = false ]; then
  hdr "Stage 3: Oracle Free (Docker)"

  CONTAINER_STATE=$(docker inspect --format='{{.State.Status}}' nlp2sql_oracle 2>/dev/null || echo "missing")

  if [ "$CONTAINER_STATE" = "running" ]; then
    HEALTH=$(docker inspect --format='{{.State.Health.Status}}' nlp2sql_oracle 2>/dev/null || echo "unknown")
    ok "Container already running (health=$HEALTH)"
  else
    echo "  Starting Oracle container..."
    docker compose -f "$COMPOSE_FILE" up -d
    ok "Container started"

    echo "  Waiting for healthy status (max ${MAX_ORACLE_WAIT}s)..."
    ELAPSED=0
    while true; do
      HEALTH=$(docker inspect --format='{{.State.Health.Status}}' nlp2sql_oracle 2>/dev/null || echo "starting")
      [ "$HEALTH" = "healthy" ] && break
      if [ "$ELAPSED" -ge "$MAX_ORACLE_WAIT" ]; then
        err "Oracle failed to become healthy after ${MAX_ORACLE_WAIT}s"
        docker compose -f "$COMPOSE_FILE" logs --tail=20 oracle
        exit 1
      fi
      printf "  … health=%s (%ds)\r" "$HEALTH" "$ELAPSED"
      sleep 10; ELAPSED=$((ELAPSED + 10))
    done
    echo ""
    ok "Oracle healthy after ${ELAPSED}s — settling for 15s..."
    sleep 15
  fi
else
  hdr "Stage 3: Oracle Docker (skipped — --no-docker)"
  ok "Assuming Oracle is already running at $ORACLE_DSN"
fi

# =============================================================================
# STAGE 4 — KYC Schema Setup
# =============================================================================
hdr "Stage 4: KYC schema setup"

python - <<PYEOF
import oracledb, os, sys, subprocess

user     = os.environ["ORACLE_USER"]
password = os.environ["ORACLE_PASSWORD"]
dsn      = os.environ["ORACLE_DSN"]
root     = "$ROOT"

try:
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
except Exception as e:
    print(f"  ✗ Cannot connect to Oracle: {e}", file=sys.stderr)
    sys.exit(1)

with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM user_tables")
    table_count = cur.fetchone()[0]

if table_count >= 8:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM customers")
        rows = cur.fetchone()[0]
    conn.close()
    print(f"  ✓ KYC schema ready — {table_count} tables, {rows} customers")
    sys.exit(0)

print(f"  ⚠ Found {table_count}/8 tables — running init scripts via sqlplus...")
conn.close()

for script in ["01_create_tables.sql", "02_load_data.sql"]:
    result = subprocess.run(
        ["docker", "exec", "nlp2sql_oracle",
         "sqlplus", "-S", f"{user}/{password}@localhost:1521/FREEPDB1",
         f"@/docker-entrypoint-initdb.d/{script}"],
        capture_output=True, text=True,
    )
    status = "OK" if result.returncode == 0 else "WARN"
    print(f"  … {script}: {status}")

conn2 = oracledb.connect(user=user, password=password, dsn=dsn)
with conn2.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM customers")
    rows = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM user_tables")
    tables = cur.fetchone()[0]
conn2.close()

if tables < 8:
    print(f"  ✗ Setup failed — only {tables} tables found", file=sys.stderr)
    sys.exit(1)

print(f"  ✓ KYC schema ready — {tables} tables, {rows} customers")
PYEOF

# =============================================================================
# STAGE 5 — Unit Tests
# =============================================================================
if [ "$SKIP_TESTS" = false ]; then
  hdr "Stage 5: Unit tests (152)"

  if python -m pytest tests/ --ignore=tests/test_e2e.py -q --tb=short 2>&1; then
    ok "All unit tests passed"
  else
    err "Unit tests failed — fix before proceeding"
    exit 1
  fi
else
  hdr "Stage 5: Unit tests (skipped)"
fi

# =============================================================================
# STAGE 6 — E2E Tests
# =============================================================================
if [ "$SKIP_TESTS" = false ]; then
  hdr "Stage 6: E2E tests against live Oracle (30)"

  if python -m pytest tests/test_e2e.py -v --tb=short 2>&1; then
    ok "All E2E tests passed"
  else
    err "E2E tests failed"
    exit 1
  fi
else
  hdr "Stage 6: E2E tests (skipped)"
fi

# =============================================================================
# STAGE 7 — Launch Streamlit App
# =============================================================================
if [ "$TESTS_ONLY" = true ]; then
  hdr "Stage 7: Streamlit (skipped — --tests-only)"
  echo ""
  echo -e "${GREEN}${BOLD}All tests passed.${NC}"
  echo ""
  echo "  Oracle:  $ORACLE_DSN  (user=$ORACLE_USER)"
  echo "  To start the app: streamlit run $ROOT/app.py"
  exit 0
fi

hdr "Stage 7: Streamlit app"

# Kill any existing streamlit on this port
if lsof -ti tcp:$STREAMLIT_PORT &>/dev/null 2>&1; then
  warn "Port $STREAMLIT_PORT in use — stopping existing process"
  lsof -ti tcp:$STREAMLIT_PORT | xargs kill -9 2>/dev/null || true
  sleep 1
fi

LOG_FILE="/tmp/knowledgeql_streamlit.log"

echo "  Launching Streamlit (logs → $LOG_FILE)..."
cd "$ROOT"
streamlit run app.py \
  --server.port "$STREAMLIT_PORT" \
  --server.headless true \
  --browser.gatherUsageStats false \
  > "$LOG_FILE" 2>&1 &
STREAMLIT_PID=$!

# Wait for Streamlit to be ready
SECS=0
while ! curl -sf "http://localhost:$STREAMLIT_PORT/_stcore/health" &>/dev/null; do
  if [ "$SECS" -ge 30 ]; then
    err "Streamlit failed to start within 30s"
    cat "$LOG_FILE"
    exit 1
  fi
  if ! kill -0 "$STREAMLIT_PID" 2>/dev/null; then
    err "Streamlit process exited unexpectedly"
    cat "$LOG_FILE"
    exit 1
  fi
  sleep 2; SECS=$((SECS + 2))
done

ok "Streamlit ready (pid=$STREAMLIT_PID)"

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║        KnowledgeQL is ready                  ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}App URL:${NC}     http://localhost:$STREAMLIT_PORT"
echo -e "  ${BOLD}Oracle:${NC}      $ORACLE_DSN  (schema=$ORACLE_SCHEMA)"
echo -e "  ${BOLD}LLM mode:${NC}    $LLM_MODE (provider=${LLM_PROVIDER:-openai})"
echo -e "  ${BOLD}App log:${NC}     $LOG_FILE"
echo ""
echo "  Press Ctrl-C to stop the app."
echo ""

# Open browser on macOS
if command -v open &>/dev/null; then
  sleep 1 && open "http://localhost:$STREAMLIT_PORT" &
fi

# Tail the log so Ctrl-C stops the app cleanly
trap "echo ''; warn 'Shutting down...'; kill $STREAMLIT_PID 2>/dev/null; exit 0" INT TERM
wait "$STREAMLIT_PID"
