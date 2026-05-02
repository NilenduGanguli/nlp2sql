"""Tests for POST /api/teach/bulk — auto-detect JSON / CSV / SQL / ZIP-of-SQL."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app_config import AppConfig
from backend.main import app
from agent.knowledge_store import KYCKnowledgeStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_CACHE_PATH", str(tmp_path))
    app.state.config = AppConfig()
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    app.state.knowledge_store = store
    if hasattr(app.state, "llm"):
        delattr(app.state, "llm")     # force fully-manual mode
    return TestClient(app)


def test_bulk_json(client):
    payload = json.dumps([
        {
            "user_input": "active customers",
            "expected_sql": "SELECT * FROM KYC.CUSTOMERS WHERE STATUS='A'",
            "description": "list of currently active customers",
            "tags": ["customer", "status"],
        },
        {
            "user_input": "transactions today",
            "expected_sql": "SELECT * FROM KYC.TRANSACTIONS WHERE TXN_DATE = SYSDATE",
        },
    ]).encode("utf-8")

    r = client.post(
        "/api/teach/bulk",
        files={"file": ("teach.json", payload, "application/json")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format_detected"] == "json"
    assert body["total"] == 2
    assert body["saved"] == 2
    assert body["failed"] == 0
    assert all(item["status"] == "saved" for item in body["items"])

    # First item carried the description override.
    store: KYCKnowledgeStore = app.state.knowledge_store
    sid = body["items"][0]["session_entry_id"]
    e = next((x for x in store.static_entries if x.id == sid), None)
    assert e is not None
    assert "active customers" in e.metadata["description"]


def test_bulk_csv(client):
    csv_bytes = (
        b"user_input,expected_sql,tags,notes\n"
        b'active customers,"SELECT * FROM KYC.CUSTOMERS WHERE STATUS=\'A\'",customer;status,owner=alice\n'
        b'risk high,"SELECT * FROM KYC.RISK_ASSESSMENTS WHERE RISK_LEVEL=\'HIGH\'",risk,\n'
    )
    # Note: tags use semicolons here intentionally — our parser splits on commas
    # but the CSV cell already encodes the value 'customer;status' so we handle
    # it gracefully (one tag).
    r = client.post(
        "/api/teach/bulk",
        files={"file": ("teach.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format_detected"] == "csv"
    assert body["total"] == 2
    assert body["saved"] == 2


def test_bulk_single_sql(client):
    sql_bytes = (
        b"-- @question: How many active customers per region?\n"
        b"-- @tags: customer, status-filter\n"
        b"-- @notes: monthly report\n"
        b"SELECT REGION, COUNT(*) FROM KYC.CUSTOMERS WHERE STATUS='A' GROUP BY REGION\n"
    )
    r = client.post(
        "/api/teach/bulk",
        files={"file": ("teach.sql", sql_bytes, "application/sql")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format_detected"] == "sql"
    assert body["total"] == 1
    assert body["saved"] == 1


def test_bulk_zip_of_sql(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "q1.sql",
            "-- @question: active customers\nSELECT * FROM KYC.CUSTOMERS WHERE STATUS='A'\n",
        )
        zf.writestr(
            "q2.sql",
            "-- @question: dormant accounts\nSELECT * FROM KYC.ACCOUNTS WHERE STATUS='D'\n",
        )
        zf.writestr("README.txt", "ignored — not a .sql file\n")
    r = client.post(
        "/api/teach/bulk",
        files={"file": ("teach.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format_detected"] == "zip-of-sql"
    assert body["total"] == 2
    assert body["saved"] == 2


def test_bulk_unrecognised_format_returns_400(client):
    r = client.post(
        "/api/teach/bulk",
        files={"file": ("teach.weird", b"some random text", "application/octet-stream")},
    )
    assert r.status_code == 400


def test_bulk_partial_failure_continues(client):
    """Bad rows are reported as errors but the rest still save."""
    payload = json.dumps([
        {"user_input": "ok one", "expected_sql": "SELECT 1 FROM DUAL"},
        # Missing expected_sql — should be silently skipped (parser drops it)
        {"user_input": "missing sql"},
        {"user_input": "ok two", "expected_sql": "SELECT 2 FROM DUAL"},
    ]).encode("utf-8")
    r = client.post(
        "/api/teach/bulk",
        files={"file": ("teach.json", payload, "application/json")},
    )
    assert r.status_code == 200
    body = r.json()
    # Parser drops the missing-sql row; only 2 valid pairs reach the saver.
    assert body["total"] == 2
    assert body["saved"] == 2
