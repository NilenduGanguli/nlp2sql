#!/bin/bash
# =============================================================================
# 09_run_in_pdb.sh
# Runs the KYC DDL and data SQL scripts explicitly as the kyc user in FREEPDB1.
# This is a fallback because the gvenzl/oracle-free:latest image may run .sql
# files as SYSTEM in the CDB context rather than as APP_USER in the PDB.
# =============================================================================
set -e

echo ">>> Running KYC schema setup in FREEPDB1 as kyc user..."

TABLE_COUNT=$(echo "SELECT COUNT(*) FROM user_tables;" | \
    sqlplus -S kyc/KycPassword1@localhost:1521/FREEPDB1 | \
    grep -E '^[[:space:]]*[0-9]+' | tr -d ' ')

if [ "${TABLE_COUNT:-0}" -ge 8 ]; then
    echo ">>> KYC tables already exist (${TABLE_COUNT} tables) — skipping setup."
    exit 0
fi

echo ">>> Creating KYC tables..."
sqlplus -S kyc/KycPassword1@localhost:1521/FREEPDB1 \
    @/docker-entrypoint-initdb.d/01_create_tables.sql

echo ">>> Loading KYC sample data..."
sqlplus -S kyc/KycPassword1@localhost:1521/FREEPDB1 \
    @/docker-entrypoint-initdb.d/02_load_data.sql

echo ">>> KYC setup complete."
