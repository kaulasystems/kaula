"""HealingToolWrapper tests — the hook itself, no CrewAI required."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest
from kaula.core import (
    AuditEvent,
    RepairCandidate,
    SandboxResult,
    ScanResult,
    ToolFailure,
    ToolTest,
    ToolVersion,
)
from kaula.runtime import HealingToolWrapper, ToolHealingPausedError
from kaula.self_healing import SelfHealingLoop

FIXED_SOURCE = "def parse_price(text):\n    return float(text.replace(',', ''))\n"


def parse_price(text: str) -> float:
    return float(text)


TESTS = (
    ToolTest(name="plain", args=("1234.5",), expected=1234.5, check="equals"),
    ToolTest(name="grouped", args=("1,234.5",), expected=1234.5, check="equals"),
)


class ScriptedRepairAgent:
    def __init__(self, source: str | None = FIXED_SOURCE):
        self._source = source

    def propose_repair(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        history: Sequence[RepairCandidate],
    ) -> RepairCandidate | None:
        if self._source is None:
            return None
        return RepairCandidate(
            failure_id=failure.failure_id,
            tool_name=failure.tool_name,
            entrypoint=current.entrypoint,
            source=self._source,
            diagnosis="strip digit grouping",
            attempt=len(history) + 1,
        )


class InProcessSandbox:
    """Test double: executes the candidate in-process against its tests."""

    def run(
        self, candidate: RepairCandidate, tests: Sequence[ToolTest], *, timeout_s: float = 30.0
    ) -> SandboxResult:
        namespace: dict[str, Any] = {}
        exec(compile(candidate.source, "<test-sandbox>", "exec"), namespace)
        fn = namespace[candidate.entrypoint]
        failures = []
        for test in tests:
            try:
                out = fn(*test.args, **dict(test.kwargs))
                if test.check == "equals" and out != test.expected:
                    failures.append(f"{test.name}: expected {test.expected!r}, got {out!r}")
            except Exception as exc:
                failures.append(f"{test.name}: {type(exc).__name__}: {exc}")
        return SandboxResult(
            passed=not failures,
            tests_run=len(tests),
            tests_passed=len(tests) - len(failures),
            failures=tuple(failures),
        )


class PassingScanner:
    def scan(self, candidate: RepairCandidate) -> ScanResult:
        return ScanResult(passed=True)


class NullAudit:
    def __init__(self) -> None:
        self.types: list[str] = []

    def append(self, event_type: str, payload: Mapping[str, Any]) -> AuditEvent:
        self.types.append(event_type)
        return AuditEvent(
            sequence=len(self.types),
            event_id=str(len(self.types)),
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


def make_loop(agent: ScriptedRepairAgent | None = None) -> SelfHealingLoop:
    return SelfHealingLoop(
        repair_agent=agent if agent is not None else ScriptedRepairAgent(),
        sandbox=InProcessSandbox(),
        scanner=PassingScanner(),
        audit=NullAudit(),
        max_attempts=2,
    )


def make_wrapper(**kwargs: Any) -> HealingToolWrapper:
    defaults: dict[str, Any] = {
        "tool_name": "parse_price",
        "func": parse_price,
        "loop": make_loop(),
        "tests": TESTS,
    }
    defaults.update(kwargs)
    return HealingToolWrapper(**defaults)


def test_healthy_calls_pass_through() -> None:
    wrapper = make_wrapper()
    assert wrapper("1234.5") == 1234.5
    assert wrapper.version.version == 1


def test_failure_heals_swaps_and_retries_the_call() -> None:
    swapped: list[ToolVersion] = []
    wrapper = make_wrapper(on_swap=swapped.append)

    # this input crashes v1; the wrapper must heal and answer anyway
    assert wrapper("1,234.5") == 1234.5

    assert wrapper.version.version == 2
    assert wrapper.version.source_hash == swapped[0].source_hash
    # and the healed tool keeps working on both old and new inputs
    assert wrapper("999.0") == 999.0
    assert wrapper("9,999.0") == 9999.0


def test_unhealed_failure_pauses_the_run() -> None:
    wrapper = make_wrapper(loop=make_loop(ScriptedRepairAgent(source=None)))
    with pytest.raises(ToolHealingPausedError) as excinfo:
        wrapper("1,234.5")
    assert "paused" in str(excinfo.value)
    assert wrapper.version.version == 1  # tool left unchanged: safe state


def test_swap_rejects_source_missing_the_entrypoint() -> None:
    wrapper = make_wrapper()
    bad = wrapper.version.child("def other_name():\n    return None\n")
    with pytest.raises(RuntimeError, match="entrypoint"):
        wrapper._swap(bad)


def test_source_captured_from_function_when_not_given() -> None:
    wrapper = make_wrapper()
    assert "def parse_price" in wrapper.version.source
    assert wrapper.version.entrypoint == "parse_price"
