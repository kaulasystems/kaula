"""LangGraph adapter tests. Skipped entirely when LangChain/LangGraph aren't
installed — the healing loop and wrapper are covered framework-free in
kaula-runtime and kaula-self-healing."""

from collections.abc import Sequence
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")

from kaula.core import (  # noqa: E402
    RepairCandidate,
    SandboxResult,
    ScanResult,
    ToolFailure,
    ToolTest,
    ToolVersion,
)
from kaula.runtime import ToolHealingPausedError  # noqa: E402
from kaula.runtime_langgraph import heal_langgraph_tool  # noqa: E402
from kaula.self_healing import SelfHealingLoop  # noqa: E402
from langchain_core.tools import BaseTool, tool  # noqa: E402

FIXED_SOURCE = "def parse_price(text):\n    return float(text.replace(',', ''))\n"

TESTS = (
    ToolTest(name="plain", args=("1234.5",), expected=1234.5, check="equals"),
    ToolTest(name="grouped", args=("1,234.5",), expected=1234.5, check="equals"),
)


class ScriptedRepairAgent:
    def __init__(self, source: str | None = FIXED_SOURCE) -> None:
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
    def run(
        self, candidate: RepairCandidate, tests: Sequence[ToolTest], *, timeout_s: float = 30.0
    ) -> SandboxResult:
        namespace: dict[str, Any] = {}
        exec(compile(candidate.source, "<test-sandbox>", "exec"), namespace)
        fn = namespace[candidate.entrypoint]
        failures = []
        for t in tests:
            try:
                out = fn(*t.args, **dict(t.kwargs))
                if t.check == "equals" and out != t.expected:
                    failures.append(f"{t.name}: got {out!r}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{t.name}: {type(exc).__name__}")
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
    def append(self, event_type: str, payload: dict) -> Any:  # type: ignore[type-arg]
        from kaula.core import AuditEvent

        return AuditEvent(0, "", event_type, dict(payload), "", "", "")

    def events(self, *, since_sequence: int = 0):  # type: ignore[no-untyped-def]
        return []

    def verify(self) -> bool:
        return True


def make_loop(agent: ScriptedRepairAgent | None = None) -> SelfHealingLoop:
    return SelfHealingLoop(
        repair_agent=agent or ScriptedRepairAgent(),
        sandbox=InProcessSandbox(),
        scanner=PassingScanner(),
        audit=NullAudit(),
        max_attempts=2,
    )


# name must match the entrypoint FIXED_SOURCE defines
@tool("parse_price")
def parse_price(text: str) -> float:
    """Parse a price string into a float."""
    return float(text)


def test_wrapped_tool_passes_healthy_calls_through() -> None:
    healed = heal_langgraph_tool(parse_price, loop=make_loop(), tests=TESTS)
    assert healed.name == parse_price.name
    assert healed.invoke({"text": "42.0"}) == 42.0
    assert healed._kaula_wrapper.version.version == 1


def test_wrapped_tool_heals_on_failure() -> None:
    healed = heal_langgraph_tool(parse_price, loop=make_loop(), tests=TESTS)
    assert healed.invoke({"text": "1,234.5"}) == 1234.5
    assert healed._kaula_wrapper.version.version == 2


def test_unhealed_failure_raises_paused_error() -> None:
    healed = heal_langgraph_tool(
        parse_price, loop=make_loop(ScriptedRepairAgent(source=None)), tests=TESTS
    )
    with pytest.raises(ToolHealingPausedError):
        healed.invoke({"text": "1,234.5"})


def test_class_based_tool_requires_explicit_source() -> None:
    class OpaqueTool(BaseTool):
        name: str = "opaque"
        description: str = "no discoverable source"

        def _run(self, text: str) -> float:
            return float(text)

    with pytest.raises(ValueError, match="source"):
        heal_langgraph_tool(OpaqueTool(), loop=make_loop(), tests=TESTS)

    healed = heal_langgraph_tool(
        OpaqueTool(),
        loop=make_loop(),
        tests=TESTS,
        source=FIXED_SOURCE,
        entrypoint="parse_price",
    )
    assert healed.invoke({"text": "1,234.5"}) == 1234.5


def test_pause_via_interrupt_surfaces_a_graph_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []  # type: ignore[type-arg]
    import langgraph.types as lgt

    monkeypatch.setattr(lgt, "interrupt", lambda payload: captured.append(payload) or "paused")

    healed = heal_langgraph_tool(
        parse_price,
        loop=make_loop(ScriptedRepairAgent(source=None)),
        tests=TESTS,
        pause_via_interrupt=True,
    )
    result = healed.invoke({"text": "1,234.5"})
    assert result == "paused"
    assert captured and captured[0]["kaula"] == "healing_paused"
    assert captured[0]["tool_name"] == "parse_price"


def test_real_langgraph_interrupt_and_resume() -> None:
    """End-to-end: an unhealed tool failure interrupts a real graph run under a
    checkpointer, and Command(resume=...) continues it."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    healed = heal_langgraph_tool(
        parse_price,
        loop=make_loop(ScriptedRepairAgent(source=None)),  # healing will fail
        tests=TESTS,
        pause_via_interrupt=True,
    )

    def node(state: dict) -> dict:  # type: ignore[type-arg]
        # first pass raises → interrupt; on resume, returns the resume value
        outcome = healed.invoke({"text": "1,234.5"})
        return {"result": outcome}

    graph = (
        StateGraph(dict)
        .add_node("run", node)
        .add_edge(START, "run")
        .add_edge("run", END)
        .compile(checkpointer=MemorySaver())
    )
    config = {"configurable": {"thread_id": "t1"}}

    first = graph.invoke({}, config)
    assert "__interrupt__" in first  # the run paused instead of crashing

    final = graph.invoke(Command(resume="human-fixed"), config)
    assert final["result"] == "human-fixed"
