"""Human-in-the-loop review hooks for the self-healing loop.

Two optional gates the loop consults, both defaulting to fully autonomous when
not provided:

- **Failure review** — after a failure is *detected*, before any auto-repair
  is attempted. Reject → don't attempt; leave the tool broken and pause.
- **Candidate review** — after a candidate passes tests + scan + the policy
  gate, before it is *hot-swapped* live. Reject → don't swap; pause.

The *mechanism* is open and inspectable; sophisticated approval / RBAC /
autonomy-tier engines are the commercial governance layer. These reference
reviewers cover the common cases: interactive (console) approval and
policy-style always/never.

A reviewer is any callable with the matching signature — the loop takes two
independent callables, so you can enable one gate, both, or neither. The
`ConsoleReviewer` class simply bundles a pair of them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kaula.core import (
    RepairCandidate,
    SandboxResult,
    ScanResult,
    ToolFailure,
    ToolVersion,
)

__all__ = [
    "Approval",
    "CandidateReview",
    "ConsoleReviewer",
    "FailureReview",
    "always_approve",
    "always_reject",
]


@dataclass(frozen=True, slots=True)
class Approval:
    """A reviewer's verdict. ``approved=False`` leaves the tool unchanged and
    pauses the run (the loop's safe state)."""

    approved: bool
    reason: str = ""

    @classmethod
    def approve(cls, reason: str = "") -> Approval:
        return cls(True, reason)

    @classmethod
    def reject(cls, reason: str = "") -> Approval:
        return cls(False, reason)


# The two hook signatures the loop accepts.
FailureReview = Callable[[ToolFailure, ToolVersion], Approval]
CandidateReview = Callable[[RepairCandidate, SandboxResult, ScanResult], Approval]


def always_approve(*_args: Any, **_kwargs: Any) -> Approval:
    """Reviewer that approves everything (matches either hook signature)."""
    return Approval.approve("auto-approved")


def always_reject(*_args: Any, **_kwargs: Any) -> Approval:
    """Reviewer that rejects everything — forces a human to drive every step."""
    return Approval.reject("auto-rejected")


class ConsoleReviewer:
    """Reference interactive reviewer: prints context and reads y/N from stdin.

    For CLI / notebook / local-operator use. Pass its bound methods to the
    loop::

        reviewer = ConsoleReviewer()
        loop = SelfHealingLoop(
            ...,
            review_failure=reviewer.review_failure,     # gate after detection
            review_candidate=reviewer.review_candidate, # gate before hot-swap
        )

    In a headless process, don't block on stdin — prefer the async pattern
    (reject → pause → resume out-of-band). ``input_fn`` / ``output_fn`` are
    injectable so this is testable and adaptable to other prompts.
    """

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self._input = input_fn
        self._print = output_fn

    def review_failure(self, failure: ToolFailure, current: ToolVersion) -> Approval:
        self._print(
            f"\n[kaula] tool {failure.tool_name!r} (v{current.version}) failed: "
            f"{failure.error_type}: {failure.error_message}"
        )
        return self._ask("attempt an automatic repair")

    def review_candidate(
        self,
        candidate: RepairCandidate,
        sandbox_result: SandboxResult,
        scan_result: ScanResult,
    ) -> Approval:
        self._print(
            f"\n[kaula] verified fix for {candidate.tool_name!r}: "
            f"{sandbox_result.tests_passed}/{sandbox_result.tests_run} tests pass, "
            f"scan {'clean' if scan_result.passed else 'FAILED'}"
        )
        self._print(f"        diagnosis: {candidate.diagnosis}")
        self._print("--- proposed source ---")
        self._print(candidate.source.rstrip())
        self._print("-----------------------")
        return self._ask("hot-swap this fix live")

    def _ask(self, action: str) -> Approval:
        answer = self._input(f"        approve: {action}? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            return Approval.approve("approved via console")
        return Approval.reject("declined via console")
