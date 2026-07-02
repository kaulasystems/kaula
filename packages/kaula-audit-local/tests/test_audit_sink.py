import sqlite3
from pathlib import Path

from kaula.audit_local import GENESIS_HASH, SqliteAuditSink


def test_appends_are_chained() -> None:
    sink = SqliteAuditSink()
    first = sink.append("failure_detected", {"tool_name": "t"})
    second = sink.append("repair_proposed", {"candidate_id": "c-1"})

    assert first.sequence == 1
    assert first.prev_hash == GENESIS_HASH
    assert second.sequence == 2
    assert second.prev_hash == first.hash
    assert len(first.hash) == 64


def test_events_reads_back_in_order_with_payloads() -> None:
    sink = SqliteAuditSink()
    sink.append("a", {"n": 1})
    sink.append("b", {"n": 2})
    sink.append("c", {"n": 3})

    events = list(sink.events())
    assert [e.event_type for e in events] == ["a", "b", "c"]
    assert [e.payload["n"] for e in events] == [1, 2, 3]

    tail = list(sink.events(since_sequence=2))
    assert [e.event_type for e in tail] == ["c"]


def test_verify_passes_on_untouched_chain(tmp_path: Path) -> None:
    sink = SqliteAuditSink(tmp_path / "audit.db")
    for n in range(5):
        sink.append("event", {"n": n})
    assert sink.verify()


def test_verify_detects_payload_tampering(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    sink = SqliteAuditSink(db)
    sink.append("hot_swap", {"to_version": 2})
    sink.append("healing_succeeded", {"attempts": 1})
    assert sink.verify()

    raw = sqlite3.connect(db)  # an attacker editing the file directly
    raw.execute("UPDATE audit_events SET payload = ? WHERE sequence = 1", ('{"to_version":99}',))
    raw.commit()
    raw.close()

    assert not sink.verify()


def test_verify_detects_deletion(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    sink = SqliteAuditSink(db)
    for n in range(3):
        sink.append("event", {"n": n})

    raw = sqlite3.connect(db)
    raw.execute("DELETE FROM audit_events WHERE sequence = 2")
    raw.commit()
    raw.close()

    assert not sink.verify()


def test_verify_detects_forged_append(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    sink = SqliteAuditSink(db)
    sink.append("event", {"n": 0})

    raw = sqlite3.connect(db)
    raw.execute(
        "INSERT INTO audit_events VALUES (2, 'evt-forged', 'hot_swap', '{}', 'now', ?, ?)",
        ("f" * 64, "f" * 64),
    )
    raw.commit()
    raw.close()

    assert not sink.verify()


def test_empty_chain_verifies() -> None:
    assert SqliteAuditSink().verify()


def test_persistence_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    SqliteAuditSink(db).append("event", {"n": 1})

    reopened = SqliteAuditSink(db)
    reopened.append("event", {"n": 2})
    assert reopened.verify()
    assert len(list(reopened.events())) == 2
