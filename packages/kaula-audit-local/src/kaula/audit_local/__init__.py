"""kaula-audit-local: reference AuditSink (hash-chained, append-only) + rollback index."""

from kaula.audit_local.sqlite import GENESIS_HASH, SqliteAuditSink
from kaula.audit_local.versions import (
    NoPreviousVersionError,
    ToolVersionStore,
    UnknownToolError,
)

__all__ = [
    "GENESIS_HASH",
    "NoPreviousVersionError",
    "SqliteAuditSink",
    "ToolVersionStore",
    "UnknownToolError",
]
