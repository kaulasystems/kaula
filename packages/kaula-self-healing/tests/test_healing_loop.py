"""Loop behaviour, verified with fakes only — no runtime, no CrewAI."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest
from kaula.core import (
    AuditEvent,
    HealingRecord,
    PolicyDecision,
    RepairCandidate,
    SandboxResult,
    ScanFinding,
    ScanResult,
    ToolFailure,
    ToolTest,
    ToolVersion,
)
from kaula.self_healing import SelfHealingLoop

GOOD_SOURCE = "def parse(x):\n    return float(x.replace(',', ''))\n"
BAD_SOURCE = "def parse(x):\n    return float(x)\n"


class FakeRepairAgent:
    def __init__(self, sources: Sequence[str | None]):
        self._sources = list(sources)
        self.calls = 0

    def propose_repair(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        history: Sequence[RepairCandidate],
    ) -> RepairCandidate | None:
        self.calls += 1
        source = self._sources.pop(0)
        if source is None:
            return None
        return RepairCandidate(
            failure_id=failure.failure_id,
            tool_name=failure.tool_name,
            entrypoint=current.entrypoint,
            source=source,
            diagnosis="float() cannot parse grouped digits",
            attempt=len(history) + 1,
        )


class FakeSandbox:
    """Passes candidates whose source is GOOD_SOURCE, fails the rest."""

    def run(
        self, candidate: RepairCandidate, tests: Sequence[ToolTest], *, timeout_s: float = 30.0
    ) -> SandboxResult:
        if candidate.source == GOOD_SOURCE:
            return SandboxResult(passed=True, tests_run=len(tests), tests_passed=len(tests))
        return SandboxResult(
            passed=False,
            tests_run=len(tests),
            tests_passed=0,
            failures=tuple(f"{t.name}: ValueError" for t in tests),
        )


class FakeScanner:
    def __init__(self, passed: bool = True):
        self._passed = passed

    def scan(self, candidate: RepairCandidate) -> ScanResult:
        if self._passed:
            return ScanResult(passed=True)
        return ScanResult(
            passed=False,
            findings=(ScanFinding(rule="banned-call", severity="critical", message="eval"),),
        )


class DenyingPolicy:
    def authorize_swap(
        self,
        candidate: RepairCandidate,
        sandbox_result: SandboxResult,
        scan_result: ScanResult,
    ) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason="approval required")


class RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []

    def append(self, event_type: str, payload: Mapping[str, Any]) -> AuditEvent:
        self.entries.append((event_type, dict(payload)))
        return AuditEvent(
            sequence=len(self.entries),
            event_id=f"evt-{len(self.entries)}",
            event_type=event_type,
            payload=dict(payload),
            recorded_at="",
            prev_hash="",
            hash="",
        )

    def events(self, *, since_sequence: int = 0) -> Iterable[AuditEvent]:
        return []

    def verify(self) -> bool:
        return True

    def types(self) -> list[str]:
        return [event_type for event_type, _ in self.entries]


class RecordingMemory:
    def __init__(self) -> None:
        self.records: list[HealingRecord] = []

    def record(self, record: HealingRecord) -> None:
        self.records.append(record)

    def recall(self, tool_name: str, *, limit: int = 5) -> Sequence[HealingRecord]:
        return self.records[:limit]


@pytest.fixture
def failure() -> ToolFailure:
    try:
        float("1,234.56")
    except ValueError as exc:
        return ToolFailure.from_exception("parse", exc, args=("1,234.56",))
    raise AssertionError("expected ValueError")


@pytest.fixture
def current() -> ToolVersion:
    return ToolVersion.initial("parse", "parse", BAD_SOURCE)


TESTS = (
    ToolTest(name="plain", args=("1234.56",), expected=1234.56, check="equals"),
    ToolTest(name="grouped", args=("1,234.56",), expected=1234.56, check="equals"),
)


def make_loop(agent: FakeRepairAgent, audit: RecordingAudit, **kwargs: Any) -> SelfHealingLoop:
    defaults: dict[str, Any] = {
        "repair_agent": agent,
        "sandbox": FakeSandbox(),
        "scanner": FakeScanner(),
        "audit": audit,
        "max_attempts": 3,
    }
    defaults.update(kwargs)
    return SelfHealingLoop(**defaults)


def test_heals_first_try_and_swaps(failure: ToolFailure, current: ToolVersion) -> None:
    audit = RecordingAudit()
    memory = RecordingMemory()
    swapped: list[ToolVersion] = []
    loop = make_loop(FakeRepairAgent([GOOD_SOURCE]), audit, memory=memory)

    outcome = loop.heal(failure, current, TESTS, apply_swap=swapped.append)

    assert outcome.healed and not outcome.paused
    assert outcome.attempts == 1
    assert outcome.new_version is not None
    assert outcome.new_version.version == 2
    assert outcome.new_version.parent_version == 1
    assert swapped == [outcome.new_version]
    assert audit.types() == [
        "failure_detected",
        "repair_proposed",
        "sandbox_result",
        "scan_result",
        "gate_decision",
        "hot_swap",
        "healing_succeeded",
    ]
    # curated memory write: verified success, perfect score, fix by hash
    assert len(memory.records) == 1
    assert memory.records[0].score == 1.0
    assert memory.records[0].source_hash == outcome.new_version.source_hash
    assert GOOD_SOURCE not in memory.records[0].diagnosis


def test_retries_after_failed_verification_then_heals(
    failure: ToolFailure, current: ToolVersion
) -> None:
    audit = RecordingAudit()
    agent = FakeRepairAgent([BAD_SOURCE, GOOD_SOURCE])
    loop = make_loop(agent, audit)

    outcome = loop.heal(failure, current, TESTS, apply_swap=lambda v: None)

    assert outcome.healed
    assert outcome.attempts == 2
    assert agent.calls == 2


def test_budget_exhausted_is_a_safe_state(failure: ToolFailure, current: ToolVersion) -> None:
    audit = RecordingAudit()
    notifications: list[str] = []
    swapped: list[ToolVersion] = []
    loop = make_loop(
        FakeRepairAgent([BAD_SOURCE, BAD_SOURCE]),
        audit,
        max_attempts=2,
        notify=notifications.append,
    )

    outcome = loop.heal(failure, current, TESTS, apply_swap=swapped.append)

    assert not outcome.healed
    assert outcome.paused
    assert outcome.attempts == 2
    assert swapped == []  # tool left broken, nothing unverified shipped
    assert audit.types()[-1] == "healing_failed"
    assert len(notifications) == 1
    assert "paused" in notifications[0]


def test_scan_failure_blocks_swap(failure: ToolFailure, current: ToolVersion) -> None:
    audit = RecordingAudit()
    swapped: list[ToolVersion] = []
    loop = make_loop(
        FakeRepairAgent([GOOD_SOURCE]), audit, scanner=FakeScanner(passed=False), max_attempts=1
    )

    outcome = loop.heal(failure, current, TESTS, apply_swap=swapped.append)

    assert not outcome.healed and outcome.paused
    assert swapped == []
    assert "scan" in outcome.reason
    assert "gate_decision" not in audit.types()


def test_policy_denial_is_terminal_not_retried(failure: ToolFailure, current: ToolVersion) -> None:
    audit = RecordingAudit()
    agent = FakeRepairAgent([GOOD_SOURCE, GOOD_SOURCE, GOOD_SOURCE])
    loop = make_loop(agent, audit, policy=DenyingPolicy())

    outcome = loop.heal(failure, current, TESTS, apply_swap=lambda v: None)

    assert not outcome.healed and outcome.paused
    assert agent.calls == 1  # the gate is not negotiated with
    assert "approval required" in outcome.reason


def test_no_candidate_ends_the_loop(failure: ToolFailure, current: ToolVersion) -> None:
    audit = RecordingAudit()
    loop = make_loop(FakeRepairAgent([None]), audit)

    outcome = loop.heal(failure, current, TESTS, apply_swap=lambda v: None)

    assert not outcome.healed and outcome.paused
    assert outcome.attempts == 0
    assert "no candidate" in outcome.reason


def test_swap_exception_is_audited_and_safe(failure: ToolFailure, current: ToolVersion) -> None:
    audit = RecordingAudit()

    def exploding_swap(version: ToolVersion) -> None:
        raise RuntimeError("registry unavailable")

    loop = make_loop(FakeRepairAgent([GOOD_SOURCE]), audit)
    outcome = loop.heal(failure, current, TESTS, apply_swap=exploding_swap)

    assert not outcome.healed and outcome.paused
    assert "hot_swap_failed" in audit.types()
    assert audit.types()[-1] == "healing_failed"


def test_audit_payloads_never_contain_raw_arguments(
    failure: ToolFailure, current: ToolVersion
) -> None:
    audit = RecordingAudit()
    loop = make_loop(FakeRepairAgent([GOOD_SOURCE]), audit)
    loop.heal(failure, current, TESTS, apply_swap=lambda v: None)

    detected = dict(audit.entries)["failure_detected"]
    assert detected["args_fingerprint"] == failure.args_fingerprint
    assert "1,234.56" not in str(detected.get("args", ""))
    assert "args" not in detected
