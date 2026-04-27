import os
from fastapi.testclient import TestClient


def test_admin_config_returns_default_user_mode(monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_MODE", "curator")
    from backend.main import app
    from app_config import AppConfig

    # Bypass the heavy graph/LLM lifespan for this lightweight endpoint test
    app.state.config = AppConfig()

    client = TestClient(app)
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    assert r.json().get("default_user_mode") == "curator"
