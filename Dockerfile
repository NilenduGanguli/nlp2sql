# =============================================================================
# KnowledgeQL — Application Container
#
# This image runs oracledb in THIN mode (no Oracle Instant Client needed).
# Thick mode is supported on the host via ORACLE_THICK_MODE=true in .env,
# but is overridden to false inside the container (see docker-compose.yml).
# Supports linux/amd64 and linux/arm64 natively.
# Build:  docker build -t knowledgeql-app .
# Run:    docker compose -f docker/docker-compose.yml up
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# curl is used by the healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Create the graph cache directory (Docker named volume will mount here).
# Pre-creating ensures correct permissions even before the volume is attached.
RUN mkdir -p /data/graph_cache

# Install Python dependencies first (layer cache-friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt watchdog

# Copy application source
COPY . .

# Streamlit port
EXPOSE 8502

# Healthcheck — Streamlit exposes /_stcore/health once ready
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=8 \
    CMD curl -sf http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["bash", "docker/app/entrypoint.sh"]
