import json
from fastapi.testclient import TestClient


def test_post_signals_writes_event(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_STORE_PATH", str(tmp_path))
    # Reset the cached singleton so the env var takes effect for this test
    import backend.deps as deps_mod
    deps_mod._signal_log_singleton = None

    from app_config import AppConfig
    from backend.main import app
    app.state.config = AppConfig()  # bypass heavy lifespan

    client = TestClient(app)
    r = client.post("/api/signals", json={
        "event": "ran_unchanged",
        "session_id": "abc",
        "entry_id": None,
        "mode": "curator",
        "sql_hash": "h1",
        "metadata": {"row_count": 5},
    })
    assert r.status_code == 200
    assert r.json() == {"status": "logged"}
    files = list(tmp_path.glob("signals/signals-*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().strip().splitlines()[0])
    assert rec["event"] == "ran_unchanged"
    assert rec["session_id"] == "abc"


def test_post_signals_rejects_unknown_event():
    from app_config import AppConfig
    from backend.main import app
    app.state.config = AppConfig()
    client = TestClient(app)
    r = client.post("/api/signals", json={
        "event": "made_up_event",
        "session_id": "x",
        "entry_id": None,
        "mode": "curator",
        "sql_hash": "h",
        "metadata": {},
    })
    assert r.status_code == 422
