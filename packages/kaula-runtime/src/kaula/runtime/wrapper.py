"""The interception hook: wrap a tool callable, heal it on failure.

This module is framework-free so the hook itself stays unit-testable without
CrewAI; the CrewAI-specific glue lives in ``kaula.runtime.crewai_adapter``.
"""

from __future__ import annotations

import inspect
import textwrap
from collections.abc import Callable, Sequence
from typing import Any

from kaula.core import ToolFailure, ToolTest, ToolVersion
from kaula.runtime.pause import PauseRecord, SqlitePauseLedger
from kaula.self_healing import SelfHealingLoop

__all__ = ["HealingToolWrapper", "ToolHealingPausedError"]


class ToolHealingPausedError(RuntimeError):
    """Healing failed within budget: the tool is unchanged and the run must
    stay paused for a human. Deliberately not swallowed anywhere."""

    def __init__(
        self, tool_name: str, reason: str, *, pause_record: PauseRecord | None = None
    ) -> None:
        super().__init__(
            f"tool {tool_name!r} failed and could not be healed ({reason}); "
            f"run paused for human review"
        )
        self.tool_name = tool_name
        self.reason = reason
        self.pause_record = pause_record


class HealingToolWrapper:
    """Wraps a callable tool; on exception, drives the self-healing loop.

    On a verified fix the wrapper hot-swaps its underlying callable (compiled
    from the gated candidate source) and retries the original call once. On
    an unhealed failure it raises :class:`ToolHealingPausedError` so the
    surrounding run stops instead of committing partial results.
    """

    def __init__(
        self,
        *,
        tool_name: str,
        func: Callable[..., Any],
        loop: SelfHealingLoop,
        tests: Sequence[ToolTest],
        source: str | None = None,
        entrypoint: str | None = None,
        run_id: str | None = None,
        on_swap: Callable[[ToolVersion], None] | None = None,
        pause_ledger: SqlitePauseLedger | None = None,
    ) -> None:
        if source is None:
            source = textwrap.dedent(inspect.getsource(func))
        if entrypoint is None:
            entrypoint = func.__name__
        self._func = func
        self._loop = loop
        self._tests = tuple(tests)
        self._run_id = run_id
        self._on_swap = on_swap
        self._pause_ledger = pause_ledger
        self.version = ToolVersion.initial(tool_name, entrypoint, source)

    @property
    def tool_name(self) -> str:
        return self.version.tool_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._func(*args, **kwargs)
        except Exception as exc:
            failure = ToolFailure.from_exception(
                self.tool_name,
                exc,
                args=args,
                kwargs=kwargs,
                run_id=self._run_id,
            )
            outcome = self._loop.heal(failure, self.version, self._tests, apply_swap=self._swap)
            if not outcome.healed:
                record = None
                if self._pause_ledger is not None:
                    record = self._pause_ledger.record_pause(
                        tool_name=self.tool_name,
                        failure_id=failure.failure_id,
                        reason=outcome.reason,
                        run_id=self._run_id,
                    )
                raise ToolHealingPausedError(
                    self.tool_name, outcome.reason, pause_record=record
                ) from exc
            return self._func(*args, **kwargs)

    def _swap(self, version: ToolVersion) -> None:
        """Hot-swap: compile the gated source and replace the live callable.

        Only ever called by the loop after tests + scan + policy gate passed.
        """
        namespace: dict[str, Any] = {}
        code = compile(
            version.source,
            f"<kaula-heal:{version.tool_name}:v{version.version}>",
            "exec",
        )
        exec(code, namespace)  # noqa: S102 — the verified swap is the product
        try:
            replacement = namespace[version.entrypoint]
        except KeyError:
            raise RuntimeError(
                f"healed source for {version.tool_name!r} does not define "
                f"entrypoint {version.entrypoint!r}"
            ) from None
        self._func = replacement
        self.version = version
        if self._on_swap is not None:
            self._on_swap(version)
