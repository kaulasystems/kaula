"""CrewAI adapter tests. Skipped entirely when CrewAI is not installed —
the wrapper itself is covered framework-free in test_wrapper.py."""

import pytest

crewai = pytest.importorskip("crewai")

from crewai.tools import tool  # noqa: E402
from kaula.core import ToolTest  # noqa: E402
from kaula.runtime import ToolHealingPausedError  # noqa: E402
from kaula.runtime.crewai_adapter import heal_crewai_tool  # noqa: E402
from test_wrapper import FIXED_SOURCE, ScriptedRepairAgent, make_loop  # noqa: E402

TESTS = (
    ToolTest(name="plain", args=("1234.5",), expected=1234.5, check="equals"),
    ToolTest(name="grouped", args=("1,234.5",), expected=1234.5, check="equals"),
)


# the function name must match the entrypoint the scripted repair agent's
# FIXED_SOURCE defines: parse_price
@tool("parse_price")
def parse_price(text: str) -> float:
    """Parse a price string into a float."""
    return float(text)


def test_wrapped_tool_heals_on_failure() -> None:
    healed = heal_crewai_tool(parse_price, loop=make_loop(), tests=TESTS)
    assert healed.name == parse_price.name
    assert healed.run(text="1,234.5") == 1234.5
    assert healed._kaula_wrapper.version.version == 2


def test_wrapped_tool_passes_healthy_calls_through() -> None:
    healed = heal_crewai_tool(parse_price, loop=make_loop(), tests=TESTS)
    assert healed.run(text="42.0") == 42.0
    assert healed._kaula_wrapper.version.version == 1


def test_unhealed_failure_raises_paused_error() -> None:
    healed = heal_crewai_tool(
        parse_price, loop=make_loop(ScriptedRepairAgent(source=None)), tests=TESTS
    )
    with pytest.raises(ToolHealingPausedError):
        healed._run(text="1,234.5")


def test_kickoff_with_healing_completes_when_tools_heal() -> None:
    from kaula.runtime import HealingToolWrapper
    from kaula.runtime.crewai_adapter import kickoff_with_healing

    wrapper = HealingToolWrapper(
        tool_name="parse_price",
        func=lambda text: float(text),
        source="def parse_price(text):\n    return float(text)\n",
        entrypoint="parse_price",
        loop=make_loop(),
        tests=TESTS,
    )

    class FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return wrapper(inputs["text"])

    outcome = kickoff_with_healing(FakeCrew(), inputs={"text": "1,234.5"})
    assert outcome.completed
    assert outcome.output == 1234.5
    assert outcome.pause is None


def test_kickoff_with_healing_pauses_durably_and_resumes() -> None:
    from kaula.runtime import HealingToolWrapper, SqlitePauseLedger
    from kaula.runtime.crewai_adapter import kickoff_with_healing, resume_paused_run

    ledger = SqlitePauseLedger()
    agent = ScriptedRepairAgent(source=None)  # healing will fail
    wrapper = HealingToolWrapper(
        tool_name="parse_price",
        func=lambda text: float(text),
        source="def parse_price(text):\n    return float(text)\n",
        entrypoint="parse_price",
        loop=make_loop(agent),
        tests=TESTS,
        pause_ledger=ledger,
        run_id="run-7",
    )

    class FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return wrapper(inputs["text"])

    crew = FakeCrew()
    outcome = kickoff_with_healing(crew, inputs={"text": "1,234.5"}, ledger=ledger)
    assert not outcome.completed
    assert outcome.pause is not None
    assert ledger.pending()  # the paused run is durable, not just an exception

    # a human ships a fix out of band (here: swap in working source), then resumes
    wrapper._swap(wrapper.version.child(FIXED_SOURCE))
    resumed = resume_paused_run(crew, outcome.pause, ledger=ledger, inputs={"text": "1,234.5"})
    assert resumed.completed
    assert resumed.output == 1234.5
    assert ledger.pending() == []


def test_class_based_tool_requires_explicit_source() -> None:
    from crewai.tools import BaseTool

    class OpaqueTool(BaseTool):
        name: str = "opaque"
        description: str = "no discoverable source"

        def _run(self, text: str) -> float:
            return float(text)

    with pytest.raises(ValueError, match="source"):
        heal_crewai_tool(OpaqueTool(), loop=make_loop(), tests=TESTS)

    healed = heal_crewai_tool(
        OpaqueTool(),
        loop=make_loop(),
        tests=TESTS,
        source=FIXED_SOURCE,
        entrypoint="parse_price",
    )
    assert healed.run(text="1,234.5") == 1234.5
