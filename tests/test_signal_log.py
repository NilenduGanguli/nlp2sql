import json

from agent.signal_log import SignalLog, SignalEvent


def test_append_writes_jsonl(tmp_path):
    log = SignalLog(persist_dir=str(tmp_path))
    log.append(SignalEvent(
        event="ran_unchanged",
        session_id="s1",
        entry_id="e1",
        mode="curator",
        sql_hash="abc",
        metadata={"row_count": 42},
    ))
    files = list(tmp_path.glob("signals-*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "ran_unchanged"
    assert rec["session_id"] == "s1"
    assert rec["mode"] == "curator"
    assert "timestamp" in rec


def test_append_multiple_events(tmp_path):
    log = SignalLog(persist_dir=str(tmp_path))
    for i in range(3):
        log.append(SignalEvent(
            event="copied_sql", session_id=f"s{i}", entry_id=None,
            mode="consumer", sql_hash="x", metadata={},
        ))
    files = list(tmp_path.glob("signals-*.jsonl"))
    assert len(files) == 1
    assert len(files[0].read_text().strip().splitlines()) == 3


def test_load_filters_by_event_and_session(tmp_path):
    log = SignalLog(persist_dir=str(tmp_path))
    log.append(SignalEvent(event="copied_sql", session_id="s1", entry_id=None,
                           mode="curator", sql_hash="x", metadata={}))
    log.append(SignalEvent(event="ran_unchanged", session_id="s1", entry_id=None,
                           mode="curator", sql_hash="x", metadata={}))
    log.append(SignalEvent(event="copied_sql", session_id="s2", entry_id=None,
                           mode="curator", sql_hash="y", metadata={}))

    by_session = log.load(session_id="s1")
    assert len(by_session) == 2

    by_event = log.load(event="copied_sql")
    assert len(by_event) == 2
