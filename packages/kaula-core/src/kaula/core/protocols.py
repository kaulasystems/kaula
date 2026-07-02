"""The seam: Protocols implemented twice — an open reference impl and a
commercial impl — and resolved at runtime, never imported concretely by open
code (docs/kaula-oss-architecture.md §2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from kaula.core.types import (
    AuditEvent,
    HealingRecord,
    PolicyDecision,
    RepairCandidate,
    SandboxResult,
    ScanResult,
    ToolFailure,
    ToolTest,
    ToolVersion,
)

__all__ = [
    "AuditSink",
    "MCPGateway",
    "MemoryStore",
    "PolicyEngine",
    "RepairAgent",
    "Sandbox",
    "Scanner",
]


@runtime_checkable
class RepairAgent(Protocol):
    """Diagnoses a failure and proposes a rewritten tool."""

    def propose_repair(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        history: Sequence[RepairCandidate],
    ) -> RepairCandidate | None:
        """Return the next candidate, or None if no further repair is worth trying."""
        ...


@runtime_checkable
class Sandbox(Protocol):
    """Isolated execution: builds a candidate and runs its tests.

    Implementations must not expose ambient credentials or unrestricted
    egress to the candidate code.
    """

    def run(
        self,
        candidate: RepairCandidate,
        tests: Sequence[ToolTest],
        *,
        timeout_s: float = 30.0,
    ) -> SandboxResult: ...


@runtime_checkable
class Scanner(Protocol):
    """Security scan of candidate source before it can go live."""

    def scan(self, candidate: RepairCandidate) -> ScanResult: ...


@runtime_checkable
class PolicyEngine(Protocol):
    """The gate: decides whether a verified candidate may be hot-swapped."""

    def authorize_swap(
        self,
        candidate: RepairCandidate,
        sandbox_result: SandboxResult,
        scan_result: ScanResult,
    ) -> PolicyDecision: ...


@runtime_checkable
class AuditSink(Protocol):
    """Append-only, hash-chained audit trail.

    Payloads must be JSON-serialisable and PII-free (by-reference only:
    fingerprints and hashes, never raw personal data).
    """

    def append(self, event_type: str, payload: Mapping[str, Any]) -> AuditEvent: ...

    def events(self, *, since_sequence: int = 0) -> Iterable[AuditEvent]: ...

    def verify(self) -> bool:
        """Recompute the chain; False means the trail was tampered with."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """Curated procedural memory: only verified, scored outcomes persist."""

    def record(self, record: HealingRecord) -> None: ...

    def recall(self, tool_name: str, *, limit: int = 5) -> Sequence[HealingRecord]: ...


@runtime_checkable
class MCPGateway(Protocol):
    """Access to MCP servers. The open impl connects and logs; allow-listing,
    screening and credential brokering are the governed (commercial) impl."""

    def list_tools(self, server: str) -> Sequence[str]: ...

    def call_tool(self, server: str, tool: str, arguments: Mapping[str, Any]) -> Any: ...
