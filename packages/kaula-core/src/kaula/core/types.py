"""Domain types shared across every Kaula package.

These are the vocabulary of the self-healing loop. They carry construction
helpers but no orchestration behaviour, and must never import a framework or
an implementation package.
"""

from __future__ import annotations

import hashlib
import traceback as _traceback
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

__all__ = [
    "AuditEvent",
    "HealingRecord",
    "Plan",
    "PlanStep",
    "PolicyDecision",
    "RepairCandidate",
    "SandboxResult",
    "ScanFinding",
    "ScanResult",
    "ToolFailure",
    "ToolTest",
    "ToolVersion",
    "fingerprint_args",
    "new_id",
    "sha256_hex",
    "utcnow",
]

Severity = Literal["low", "medium", "high", "critical"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def fingerprint_args(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> str:
    """Stable fingerprint of call arguments.

    Audit is by-reference for anything that may contain personal data: raw
    argument values never enter an audit payload, only this digest.
    """
    return sha256_hex(f"{args!r}|{sorted(kwargs.items())!r}")


@dataclass(frozen=True, slots=True)
class ToolFailure:
    """A captured runtime failure of an agent tool — the loop's entry event."""

    tool_name: str
    error_type: str
    error_message: str
    traceback: str
    args_fingerprint: str
    run_id: str | None = None
    task_description: str | None = None
    failure_id: str = field(default_factory=lambda: new_id("fail"))
    occurred_at: datetime = field(default_factory=utcnow)

    @classmethod
    def from_exception(
        cls,
        tool_name: str,
        exc: BaseException,
        *,
        args: tuple[Any, ...] = (),
        kwargs: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        task_description: str | None = None,
    ) -> ToolFailure:
        return cls(
            tool_name=tool_name,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback="".join(_traceback.format_exception(exc)),
            args_fingerprint=fingerprint_args(tuple(args), dict(kwargs or {})),
            run_id=run_id,
            task_description=task_description,
        )


@dataclass(frozen=True, slots=True)
class ToolVersion:
    """One immutable, version-addressable revision of a tool's source."""

    tool_name: str
    entrypoint: str
    source: str
    version: int
    source_hash: str
    parent_version: int | None = None
    created_at: datetime = field(default_factory=utcnow)

    @classmethod
    def initial(cls, tool_name: str, entrypoint: str, source: str) -> ToolVersion:
        return cls(
            tool_name=tool_name,
            entrypoint=entrypoint,
            source=source,
            version=1,
            source_hash=sha256_hex(source),
        )

    def child(self, source: str) -> ToolVersion:
        return ToolVersion(
            tool_name=self.tool_name,
            entrypoint=self.entrypoint,
            source=source,
            version=self.version + 1,
            source_hash=sha256_hex(source),
            parent_version=self.version,
        )


@dataclass(frozen=True, slots=True)
class RepairCandidate:
    """A proposed rewrite of a failed tool, produced by a RepairAgent."""

    failure_id: str
    tool_name: str
    entrypoint: str
    source: str
    diagnosis: str
    attempt: int
    candidate_id: str = field(default_factory=lambda: new_id("cand"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def source_hash(self) -> str:
        return sha256_hex(self.source)


@dataclass(frozen=True, slots=True)
class ToolTest:
    """One verification case a candidate must pass inside the sandbox.

    ``args``/``kwargs``/``expected`` must be JSON-serialisable so they can be
    shipped into an isolated environment.
    """

    name: str
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    expected: Any = None
    check: Literal["equals", "no_exception"] = "no_exception"


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Outcome of building and testing a candidate in a Sandbox."""

    passed: bool
    tests_run: int
    tests_passed: int
    failures: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    infra_error: str | None = None


@dataclass(frozen=True, slots=True)
class ScanFinding:
    rule: str
    severity: Severity
    message: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Outcome of the security scan gate."""

    passed: bool
    findings: tuple[ScanFinding, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """A PolicyEngine's verdict on whether a verified candidate may go live."""

    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable record in the hash-chained audit trail."""

    sequence: int
    event_id: str
    event_type: str
    payload: Mapping[str, Any]
    recorded_at: str
    prev_hash: str
    hash: str


@dataclass(frozen=True, slots=True)
class HealingRecord:
    """A curated memory entry: a verified, scored healing outcome.

    Only verified successes are persisted (curated writes only); the fix is
    referenced by hash, never inlined.
    """

    tool_name: str
    failure_signature: str
    diagnosis: str
    source_hash: str
    score: float
    healed_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class PlanStep:
    description: str
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    """An editable agent plan (the planner's domain type; NL on-ramp)."""

    objective: str
    steps: tuple[PlanStep, ...] = ()
