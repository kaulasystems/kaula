"""The rollback index: version-addressable tool history.

Every registered ``ToolVersion`` is kept; reverting to the parent version is
a single action that is itself written to the audit trail. Audit payloads
reference sources by hash — the full source lives only in this store.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from kaula.core import AuditSink, ToolVersion

__all__ = ["NoPreviousVersionError", "ToolVersionStore", "UnknownToolError"]


class UnknownToolError(LookupError):
    pass


class NoPreviousVersionError(RuntimeError):
    pass


class ToolVersionStore:
    def __init__(self, path: str | Path = ":memory:", *, audit: AuditSink) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._audit = audit
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_versions (
                tool_name TEXT NOT NULL,
                version INTEGER NOT NULL,
                entrypoint TEXT NOT NULL,
                source TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                parent_version INTEGER,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tool_name, version)
            )
            """)
        self._conn.commit()

    def register(self, version: ToolVersion, *, activate: bool = True) -> None:
        with self._lock:
            if activate:
                self._conn.execute(
                    "UPDATE tool_versions SET active = 0 WHERE tool_name = ?",
                    (version.tool_name,),
                )
            self._conn.execute(
                "INSERT INTO tool_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version.tool_name,
                    version.version,
                    version.entrypoint,
                    version.source,
                    version.source_hash,
                    version.parent_version,
                    version.created_at.isoformat(),
                    1 if activate else 0,
                ),
            )
            self._conn.commit()
        self._audit.append(
            "tool_version_registered",
            {
                "tool_name": version.tool_name,
                "version": version.version,
                "source_hash": version.source_hash,
                "parent_version": version.parent_version,
                "active": activate,
            },
        )

    def current(self, tool_name: str) -> ToolVersion:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tool_versions WHERE tool_name = ? AND active = 1",
                (tool_name,),
            ).fetchone()
        if row is None:
            raise UnknownToolError(f"no active version for tool {tool_name!r}")
        return _row_to_version(row)

    def get(self, tool_name: str, version: int) -> ToolVersion:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tool_versions WHERE tool_name = ? AND version = ?",
                (tool_name, version),
            ).fetchone()
        if row is None:
            raise UnknownToolError(f"tool {tool_name!r} has no version {version}")
        return _row_to_version(row)

    def history(self, tool_name: str) -> list[ToolVersion]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tool_versions WHERE tool_name = ? ORDER BY version",
                (tool_name,),
            ).fetchall()
        return [_row_to_version(row) for row in rows]

    def rollback(self, tool_name: str) -> ToolVersion:
        """Revert to the active version's parent — one call, one audit event."""
        current = self.current(tool_name)
        if current.parent_version is None:
            raise NoPreviousVersionError(
                f"tool {tool_name!r} is at its initial version; nothing to roll back to"
            )
        target = self.get(tool_name, current.parent_version)
        with self._lock:
            self._conn.execute(
                "UPDATE tool_versions SET active = 0 WHERE tool_name = ?", (tool_name,)
            )
            self._conn.execute(
                "UPDATE tool_versions SET active = 1 WHERE tool_name = ? AND version = ?",
                (tool_name, target.version),
            )
            self._conn.commit()
        self._audit.append(
            "rollback",
            {
                "tool_name": tool_name,
                "from_version": current.version,
                "to_version": target.version,
                "to_source_hash": target.source_hash,
            },
        )
        return target

    def close(self) -> None:
        self._conn.close()


def _row_to_version(row: sqlite3.Row | tuple[Any, ...]) -> ToolVersion:
    tool_name, version, entrypoint, source, source_hash, parent_version, created_at, _active = row
    return ToolVersion(
        tool_name=tool_name,
        entrypoint=entrypoint,
        source=source,
        version=version,
        source_hash=source_hash,
        parent_version=parent_version,
        created_at=datetime.fromisoformat(created_at),
    )
