"""CrewAI glue: wrap a ``BaseTool`` in the healing hook, by composition.

The only module in the open tier that imports CrewAI (seam-checked). It uses
public extension points only: we subclass ``BaseTool`` and delegate — CrewAI
internals are never vendored or patched.
"""

from __future__ import annotations

import inspect
import textwrap
from collections.abc import Callable, Sequence
from typing import Any

from crewai.tools import BaseTool
from kaula.core import ToolTest, ToolVersion
from kaula.runtime.wrapper import HealingToolWrapper
from kaula.self_healing import SelfHealingLoop

__all__ = ["heal_crewai_tool"]


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
