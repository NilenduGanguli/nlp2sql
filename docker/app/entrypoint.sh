#!/usr/bin/env bash
# =============================================================================
# KnowledgeQL — App Container Entrypoint
#
# Startup sequence:
#   1. Wait for Oracle to accept connections
#   2. Ensure KYC schema (tables + data) exists
#   3. Build and validate the knowledge graph from live Oracle metadata
#   4. Launch Streamlit
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*" >&2; }
hdr()  { echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }

cd /app

# =============================================================================
# Stage 1 — Wait for Oracle
# =============================================================================
hdr "Stage 1: Waiting for Oracle"
python docker/app/wait_for_oracle.py
ok "Oracle reachable"

# =============================================================================
# Stage 2 — Schema initialisation
# =============================================================================
hdr "Stage 2: KYC schema check"
python docker/app/init_schema.py
ok "Schema ready"

# =============================================================================
# Stage 3 — Build knowledge graph (validates everything end-to-end)
# =============================================================================
hdr "Stage 3: Building knowledge graph"
if python -m knowledge_graph.init_graph; then
    ok "Knowledge graph built and validated"
else
    warn "Graph build reported issues — Streamlit will re-attempt on first request"
fi

# =============================================================================
# Stage 4 — Launch FastAPI backend
# =============================================================================
hdr "Stage 4: Starting FastAPI backend"
ok "API will be available at http://0.0.0.0:${API_PORT:-8000}"

exec uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port "${API_PORT:-8000}" \
    --workers 1 \
    --log-level info
