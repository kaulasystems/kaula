"""The pause ledger: durable record of runs waiting on a human.

When healing fails, the run must stop *and stay visible* — a paused run that
only exists as a raised exception is easy to lose. The ledger persists one
record per pause (SQLite, file or in-memory), mirrors it to the audit trail
when a sink is attached, and tracks resolution: a human either ships a fix
(new verified tool version, rollback) and resumes the run, or abandons it —
explicitly, either way.

Framework-free by design; the CrewAI-facing helpers live in
``kaula.runtime.crewai_adapter``.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from kaula.core import AuditSink, new_id, utcnow

__all__ = ["PauseRecord", "SqlitePauseLedger", "UnknownPauseError"]


class UnknownPauseError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class PauseRecord:
    """One paused run. Metadata only — argument values never land here."""

    record_id: str
    tool_name: str
    failure_id: str
    reason: str
    run_id: str | None = None
    created_at: str = ""
    resolved_at: str | None = None
    resolution: str | None = None

    @property
    def pending(self) -> bool:
        return self.resolved_at is None


class SqlitePauseLedger:
    def __init__(self, path: str | Path = ":memory:", *, audit: AuditSink | None = None) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._audit = audit
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pauses (
                record_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                failure_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                run_id TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution TEXT
            )
            """)
        self._conn.commit()

    def record_pause(
        self,
        *,
        tool_name: str,
        failure_id: str,
        reason: str,
        run_id: str | None = None,
    ) -> PauseRecord:
        record = PauseRecord(
            record_id=new_id("pause"),
            tool_name=tool_name,
            failure_id=failure_id,
            reason=reason,
            run_id=run_id,
            created_at=utcnow().isoformat(),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO pauses VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    record.record_id,
                    record.tool_name,
                    record.failure_id,
                    record.reason,
                    record.run_id,
                    record.created_at,
                ),
            )
            self._conn.commit()
        if self._audit is not None:
            self._audit.append(
                "run_paused",
                {
                    "pause_record_id": record.record_id,
                    "tool_name": tool_name,
                    "failure_id": failure_id,
                    "reason": reason,
                    "run_id": run_id,
                },
            )
        return record

    def resolve(self, record_id: str, *, resolution: str) -> PauseRecord:
        """Mark a pause handled (fix shipped and resumed, rolled back, abandoned)."""
        resolved_at = utcnow().isoformat()
        with self._lock:
            updated = self._conn.execute(
                "UPDATE pauses SET resolved_at = ?, resolution = ? "
                "WHERE record_id = ? AND resolved_at IS NULL",
                (resolved_at, resolution, record_id),
            )
            self._conn.commit()
            if updated.rowcount == 0:
                raise UnknownPauseError(f"no pending pause with id {record_id!r}")
        if self._audit is not None:
            self._audit.append(
                "pause_resolved",
                {"pause_record_id": record_id, "resolution": resolution},
            )
        return self.get(record_id)

    def get(self, record_id: str) -> PauseRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pauses WHERE record_id = ?", (record_id,)
            ).fetchone()
        if row is None:
            raise UnknownPauseError(f"no pause with id {record_id!r}")
        return _row_to_record(row)

    def pending(self) -> list[PauseRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pauses WHERE resolved_at IS NULL ORDER BY created_at"
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def close(self) -> None:
        self._conn.close()


def _row_to_record(row: tuple[str, ...]) -> PauseRecord:
    record_id, tool_name, failure_id, reason, run_id, created_at, resolved_at, resolution = row
    return PauseRecord(
        record_id=record_id,
        tool_name=tool_name,
        failure_id=failure_id,
        reason=reason,
        run_id=run_id,
        created_at=created_at,
        resolved_at=resolved_at,
        resolution=resolution,
    )
