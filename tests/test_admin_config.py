from fastapi.testclient import TestClient

from app_config import AppConfig
from backend.main import app


def test_admin_config_returns_default_user_mode(monkeypatch):
    # Use a non-default value so the test proves the env var is actually read
    # (and not just shadowed by the response model's default).
    monkeypatch.setenv("DEFAULT_USER_MODE", "consumer")

    # Bypass the heavy graph/LLM lifespan for this lightweight endpoint test
    app.state.config = AppConfig()

    client = TestClient(app)
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    assert r.json().get("default_user_mode") == "consumer"
