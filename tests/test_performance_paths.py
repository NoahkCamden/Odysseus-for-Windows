import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, _clear_session_access_touch, _should_touch_session_access
from core.perf_metrics import begin_request_metrics, finish_request_metrics, get_perf_snapshot, record_db_query, reset_perf_snapshot
from src.memory import MemoryManager


def test_session_access_touch_is_debounced():
    session_id = "session-debounce-test"
    _clear_session_access_touch(session_id)
    try:
        assert _should_touch_session_access(session_id, min_interval_seconds=60)
        assert not _should_touch_session_access(session_id, min_interval_seconds=60)
        assert _should_touch_session_access(session_id, min_interval_seconds=0)
    finally:
        _clear_session_access_touch(session_id)


def _make_manager(tmp_path):
    db_path = Path(tmp_path) / "memory-test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return MemoryManager(str(tmp_path), db_session_factory=sessionmaker(autocommit=False, autoflush=False, bind=engine))


def test_memory_manager_caches_reads_and_returns_copies(tmp_path):
    manager = _make_manager(tmp_path)
    manager.save([
        {
            "id": "m1",
            "text": "Alice likes local models",
            "timestamp": 1,
            "source": "user",
            "category": "fact",
        }
    ])

    first = manager.load_all()
    first[0]["text"] = "mutated"
    second = manager.load_all()

    assert second[0]["text"] == "Alice likes local models"


def test_memory_manager_imports_legacy_json_once(tmp_path):
    manager = _make_manager(tmp_path)
    with open(manager.memory_file, "w", encoding="utf-8") as handle:
        json.dump([
            {
                "id": "m1",
                "text": "after",
                "timestamp": 1,
                "source": "user",
                "category": "fact",
                "pinned": True,
                "uses": 2,
            }
        ], handle)

    updated = manager.load_all()

    assert updated[0]["text"] == "after"
    assert updated[0]["pinned"] is True
    assert updated[0]["uses"] == 2


def test_memory_manager_reads_from_db_after_save(tmp_path):
    manager = _make_manager(tmp_path)
    manager.save([
        {
            "id": "m1",
            "text": "before",
            "timestamp": 1,
            "source": "user",
            "category": "fact",
        }
    ])
    updated = manager.load_all()

    assert updated[0]["text"] == "before"


def test_perf_metrics_collect_request_and_db_time():
    reset_perf_snapshot()
    token = begin_request_metrics("GET", "/api/test")
    record_db_query(12.5)
    record_db_query(7.5)
    finish_request_metrics(token, 200)

    snapshot = get_perf_snapshot()

    assert snapshot["database"]["query_count"] == 2
    assert snapshot["requests"]["sample_size"] == 1
    route = snapshot["requests"]["routes"]["GET /api/test"]
    assert route["db_query_count"] == 2
    assert route["db_total_ms"] == 20.0