"""Mode-gate test: /api/patterns/manual-promote rejects non-curator callers."""
from fastapi.testclient import TestClient


def _client():
    from app_config import AppConfig
    from backend.main import app
    app.state.config = AppConfig()
    return TestClient(app)


def test_manual_promote_rejects_consumer_mode():
    client = _client()
    r = client.post("/api/patterns/manual-promote", json={
        "sql": "SELECT * FROM KYC.CUSTOMERS",
        "user_input": "all customers",
        "tables_used": ["KYC.CUSTOMERS"],
        "mode": "consumer",
    })
    assert r.status_code == 403
    assert "curator" in r.json()["detail"].lower()


def test_manual_promote_default_mode_is_curator():
    """No mode field → defaults to curator → not 403 (may still skip on no store)."""
    client = _client()
    r = client.post("/api/patterns/manual-promote", json={
        "sql": "SELECT * FROM KYC.CUSTOMERS",
        "user_input": "all customers",
        "tables_used": ["KYC.CUSTOMERS"],
    })
    assert r.status_code != 403
