"""Reference AuditSink: append-only, hash-chained records in SQLite.

The public API can only append and read; there is no update or delete. Each
event's hash covers its full content plus the previous hash, so any
after-the-fact modification of the underlying storage breaks ``verify()``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from kaula.core import AuditEvent, new_id, utcnow

__all__ = ["SqliteAuditSink"]

GENESIS_HASH = "0" * 64


class SqliteAuditSink:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            )
            """)
        self._conn.commit()

    @staticmethod
    def compute_hash(
        sequence: int,
        event_id: str,
        event_type: str,
        payload_json: str,
        recorded_at: str,
        prev_hash: str,
    ) -> str:
        content = "|".join(
            [str(sequence), event_id, event_type, payload_json, recorded_at, prev_hash]
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def append(self, event_type: str, payload: Mapping[str, Any]) -> AuditEvent:
        payload_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
        with self._lock:
            row = self._conn.execute(
                "SELECT sequence, hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = (row[0] + 1) if row else 1
            prev_hash = row[1] if row else GENESIS_HASH
            event_id = new_id("evt")
            recorded_at = utcnow().isoformat()
            event_hash = self.compute_hash(
                sequence, event_id, event_type, payload_json, recorded_at, prev_hash
            )
            self._conn.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sequence, event_id, event_type, payload_json, recorded_at, prev_hash, event_hash),
            )
            self._conn.commit()
        return AuditEvent(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            payload=json.loads(payload_json),
            recorded_at=recorded_at,
            prev_hash=prev_hash,
            hash=event_hash,
        )

    def events(self, *, since_sequence: int = 0) -> Iterator[AuditEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT sequence, event_id, event_type, payload, recorded_at, prev_hash, hash "
                "FROM audit_events WHERE sequence > ? ORDER BY sequence",
                (since_sequence,),
            ).fetchall()
        for row in rows:
            sequence, event_id, event_type, payload_json, recorded_at, prev_hash, event_hash = row
            yield AuditEvent(
                sequence=sequence,
                event_id=event_id,
                event_type=event_type,
                payload=json.loads(payload_json),
                recorded_at=recorded_at,
                prev_hash=prev_hash,
                hash=event_hash,
            )

    def verify(self) -> bool:
        """Recompute the entire chain; False on any edit, gap, or reorder."""
        expected_prev = GENESIS_HASH
        expected_sequence = 1
        with self._lock:
            rows = self._conn.execute(
                "SELECT sequence, event_id, event_type, payload, recorded_at, prev_hash, hash "
                "FROM audit_events ORDER BY sequence"
            ).fetchall()
        for row in rows:
            sequence, event_id, event_type, payload_json, recorded_at, prev_hash, event_hash = row
            if sequence != expected_sequence or prev_hash != expected_prev:
                return False
            recomputed = self.compute_hash(
                sequence, event_id, event_type, payload_json, recorded_at, prev_hash
            )
            if recomputed != event_hash:
                return False
            expected_prev = event_hash
            expected_sequence += 1
        return True

    def close(self) -> None:
        self._conn.close()
