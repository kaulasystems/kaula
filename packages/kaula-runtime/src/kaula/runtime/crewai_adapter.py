"""CrewAI glue: wrap a ``BaseTool`` in the healing hook, by composition.

The only module in the open tier that imports CrewAI (seam-checked). It uses
public extension points only: we subclass ``BaseTool`` and delegate — CrewAI
internals are never vendored or patched.
"""

from __future__ import annotations

import inspect
import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from crewai.tools import BaseTool
from kaula.core import ToolTest, ToolVersion
from kaula.runtime.pause import PauseRecord, SqlitePauseLedger
from kaula.runtime.wrapper import HealingToolWrapper, ToolHealingPausedError
from kaula.self_healing import SelfHealingLoop

__all__ = ["CrewRunOutcome", "heal_crewai_tool", "kickoff_with_healing", "resume_paused_run"]


def heal_crewai_tool(
    tool: BaseTool,
    *,
    loop: SelfHealingLoop,
    tests: Sequence[ToolTest],
    source: str | None = None,
    entrypoint: str | None = None,
    run_id: str | None = None,
    on_swap: Callable[[ToolVersion], None] | None = None,
) -> BaseTool:
    """Return a drop-in replacement for ``tool`` whose failures self-heal.

    For tools built from a plain function (CrewAI's ``@tool`` decorator) the
    underlying function and its source are discovered automatically. For
    class-based tools pass ``source`` and ``entrypoint`` explicitly — the
    repair agent needs real source to rewrite.
    """
    func: Callable[..., Any] | None = getattr(tool, "func", None)
    if func is not None:
        underlying = func
        if source is None:
            source = textwrap.dedent(inspect.getsource(func))
        if entrypoint is None:
            entrypoint = func.__name__
    else:
        underlying = tool._run
        if source is None or entrypoint is None:
            raise ValueError(
                "class-based CrewAI tools need explicit source= and entrypoint= "
                "so the repair agent has real source to rewrite"
            )

    wrapper = HealingToolWrapper(
        tool_name=tool.name,
        func=underlying,
        loop=loop,
        tests=tests,
        source=source,
        entrypoint=entrypoint,
        run_id=run_id,
        on_swap=on_swap,
    )

    tool_name: str = tool.name
    tool_description: str = tool.description
    tool_args_schema = getattr(tool, "args_schema", None)

    class SelfHealingTool(BaseTool):
        name: str = tool_name
        description: str = tool_description

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            return wrapper(*args, **kwargs)

    healed = SelfHealingTool()
    if tool_args_schema is not None:
        healed.args_schema = tool_args_schema
    # expose the hook so callers can inspect the live version / rollback
    healed._kaula_wrapper = wrapper  # type: ignore[attr-defined]
    return healed


@dataclass(frozen=True, slots=True)
class CrewRunOutcome:
    """Result of a healing-aware crew run.

    ``completed`` with ``output`` on success; ``paused`` with the pause
    record when an unhealed tool failure stopped the run before any partial
    result could commit.
    """

    completed: bool
    output: Any = None
    pause: PauseRecord | None = None


def kickoff_with_healing(
    crew: Any,
    *,
    inputs: Mapping[str, Any] | None = None,
    ledger: SqlitePauseLedger | None = None,
    run_id: str | None = None,
) -> CrewRunOutcome:
    """Run ``crew.kickoff`` under pause-on-failure semantics.

    An unhealed tool failure raises inside the crew, which stops the run with
    no partial results committed. This helper turns that into a durable
    ``CrewRunOutcome``: the pause record comes from the wrapper's ledger when
    the tools were wrapped with one, otherwise it is written to ``ledger``
    here so the paused run cannot be lost.
    """
    try:
        output = crew.kickoff(inputs=dict(inputs) if inputs else None)
    except ToolHealingPausedError as exc:
        record = exc.pause_record
        if record is None and ledger is not None:
            record = ledger.record_pause(
                tool_name=exc.tool_name,
                failure_id="unrecorded",
                reason=exc.reason,
                run_id=run_id,
            )
        return CrewRunOutcome(completed=False, pause=record)
    return CrewRunOutcome(completed=True, output=output)


def resume_paused_run(
    crew: Any,
    record: PauseRecord,
    *,
    ledger: SqlitePauseLedger,
    inputs: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> CrewRunOutcome:
    """Resume a paused run after a human intervened.

    CrewAI does not expose mid-task state capture, so resume = re-kickoff the
    crew against the now-fixed (or rolled-back) tool; because the pause
    happened before any partial result committed, a fresh run is the honest
    resume. The pause is resolved only if the rerun completes; a second pause
    supersedes the record with a fresh one.
    """
    outcome = kickoff_with_healing(crew, inputs=inputs, ledger=ledger, run_id=run_id)
    if outcome.completed:
        ledger.resolve(record.record_id, resolution="resumed: rerun completed")
    return outcome
