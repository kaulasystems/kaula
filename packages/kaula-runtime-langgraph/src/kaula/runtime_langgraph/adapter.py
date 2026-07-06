"""LangGraph adapter: wrap a LangChain/LangGraph tool so its failures heal.

The second runtime adapter in the `kaula-runtime*` family (CrewAI was the
first). Like `kaula-runtime`, it is allowed to import an orchestration
framework — here LangGraph / LangChain — while the loop stays
framework-agnostic. This module is thin glue by composition over the
framework-free `kaula.runtime.HealingToolWrapper`; LangChain internals are
never vendored or patched.

Two pause behaviours on an unhealed failure:

- default — raise `ToolHealingPausedError` (the safe state), same as the
  CrewAI adapter.
- ``pause_via_interrupt=True`` — surface it as a LangGraph ``interrupt`` so a
  run under a checkpointer pauses at a durable checkpoint and can be resumed
  with ``Command(resume=...)`` after a human acts. On resume the tool node
  re-executes — native, real pause/resume rather than a re-kickoff.
"""

from __future__ import annotations

import inspect
import textwrap
from collections.abc import Callable, Sequence
from typing import Any

from kaula.core import ToolTest, ToolVersion
from kaula.runtime import HealingToolWrapper, SqlitePauseLedger, ToolHealingPausedError
from kaula.self_healing import SelfHealingLoop
from langchain_core.tools import BaseTool

__all__ = ["heal_langgraph_tool"]


def heal_langgraph_tool(
    tool: BaseTool,
    *,
    loop: SelfHealingLoop,
    tests: Sequence[ToolTest],
    source: str | None = None,
    entrypoint: str | None = None,
    run_id: str | None = None,
    on_swap: Callable[[ToolVersion], None] | None = None,
    pause_ledger: SqlitePauseLedger | None = None,
    pause_via_interrupt: bool = False,
) -> BaseTool:
    """Return a drop-in replacement for a LangChain ``BaseTool`` that self-heals.

    For tools built from a plain function (LangChain's ``@tool`` decorator)
    the underlying function and its source are discovered automatically. For
    class-based tools (a ``BaseTool`` subclass) pass ``source`` and
    ``entrypoint`` explicitly — the repair agent needs real source to rewrite.
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
                "class-based LangChain tools need explicit source= and entrypoint= "
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
        pause_ledger=pause_ledger,
    )

    tool_name: str = tool.name
    tool_description: str = tool.description
    tool_args_schema = getattr(tool, "args_schema", None)

    class SelfHealingTool(BaseTool):
        name: str = tool_name
        description: str = tool_description

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            # LangChain may inject framework kwargs; the healed tool only wants
            # the model-provided arguments.
            kwargs.pop("run_manager", None)
            kwargs.pop("config", None)
            try:
                return wrapper(*args, **kwargs)
            except ToolHealingPausedError as exc:
                if pause_via_interrupt:
                    return _pause_as_interrupt(exc)
                raise

    healed = SelfHealingTool()
    if tool_args_schema is not None:
        healed.args_schema = tool_args_schema
    # expose the hook so callers can inspect the live version / roll back
    healed._kaula_wrapper = wrapper
    return healed


def _pause_as_interrupt(exc: ToolHealingPausedError) -> Any:
    """Turn an unhealed pause into a LangGraph interrupt (checkpoint + resume).

    Requires running under a checkpointer; if ``interrupt`` isn't importable
    (older LangGraph), fall back to raising so the run still stops safely.
    """
    try:
        from langgraph.types import interrupt
    except Exception:  # pragma: no cover - LangGraph without interrupt()
        raise exc from None
    record = getattr(exc, "pause_record", None)
    return interrupt(
        {
            "kaula": "healing_paused",
            "tool_name": exc.tool_name,
            "reason": exc.reason,
            "pause_record_id": getattr(record, "record_id", None),
        }
    )
