"""The reference self-healing loop.

Drives the ``kaula.core`` state machine through injected Protocol
implementations. Never imports a concrete impl or a framework.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from kaula.core import (
    AuditSink,
    HealingPhase,
    HealingRecord,
    LoopStateMachine,
    MemoryStore,
    PermissivePolicyEngine,
    PolicyEngine,
    RepairAgent,
    RepairCandidate,
    Sandbox,
    Scanner,
    ToolFailure,
    ToolTest,
    ToolVersion,
)

__all__ = ["HealingOutcome", "SelfHealingLoop"]

_MESSAGE_LIMIT = 500


@dataclass(frozen=True, slots=True)
class HealingOutcome:
    """What the loop resolved to. ``paused=True`` means a human must act."""

    healed: bool
    tool_name: str
    attempts: int
    reason: str
    new_version: ToolVersion | None = None
    paused: bool = False


def _trim(text: str) -> str:
    return text if len(text) <= _MESSAGE_LIMIT else text[:_MESSAGE_LIMIT] + "…"


class SelfHealingLoop:
    """Capture → repair → verify → gate → hot-swap → audit → curate.

    A failed repair is a safe state: if no candidate passes tests + scan +
    gate within ``max_attempts``, the tool is left broken, the run stays
    paused, and ``notify`` is called. The gate is never weakened.
    """

    def __init__(
        self,
        *,
        repair_agent: RepairAgent,
        sandbox: Sandbox,
        scanner: Scanner,
        audit: AuditSink,
        policy: PolicyEngine | None = None,
        memory: MemoryStore | None = None,
        max_attempts: int = 3,
        sandbox_timeout_s: float = 30.0,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._repair_agent = repair_agent
        self._sandbox = sandbox
        self._scanner = scanner
        self._audit = audit
        self._policy = policy if policy is not None else PermissivePolicyEngine()
        self._memory = memory
        self._max_attempts = max_attempts
        self._sandbox_timeout_s = sandbox_timeout_s
        self._notify = notify

    def heal(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        tests: Sequence[ToolTest],
        *,
        apply_swap: Callable[[ToolVersion], None],
    ) -> HealingOutcome:
        sm = LoopStateMachine()
        self._audit.append(
            "failure_detected",
            {
                "failure_id": failure.failure_id,
                "tool_name": failure.tool_name,
                "tool_version": current.version,
                "error_type": failure.error_type,
                "error_message": _trim(failure.error_message),
                "args_fingerprint": failure.args_fingerprint,
                "run_id": failure.run_id,
            },
        )

        history: list[RepairCandidate] = []
        reason = "unknown"
        for attempt in range(1, self._max_attempts + 1):
            sm.advance(HealingPhase.DIAGNOSE)
            candidate = self._repair_agent.propose_repair(failure, current, tuple(history))
            if candidate is None:
                reason = f"repair agent produced no candidate on attempt {attempt}"
                break
            history.append(candidate)
            self._audit.append(
                "repair_proposed",
                {
                    "failure_id": failure.failure_id,
                    "candidate_id": candidate.candidate_id,
                    "tool_name": candidate.tool_name,
                    "attempt": attempt,
                    "source_hash": candidate.source_hash,
                    "diagnosis": _trim(candidate.diagnosis),
                },
            )

            sm.advance(HealingPhase.SANDBOX)
            result = self._sandbox.run(candidate, tests, timeout_s=self._sandbox_timeout_s)
            sm.advance(HealingPhase.TEST)
            self._audit.append(
                "sandbox_result",
                {
                    "candidate_id": candidate.candidate_id,
                    "passed": result.passed,
                    "tests_run": result.tests_run,
                    "tests_passed": result.tests_passed,
                    "failures": [_trim(f) for f in result.failures],
                    "infra_error": result.infra_error,
                    "duration_s": result.duration_s,
                },
            )
            if not result.passed:
                reason = f"sandbox verification failed on attempt {attempt}"
                if attempt < self._max_attempts:
                    continue  # top of loop re-enters DIAGNOSE
                break

            sm.advance(HealingPhase.SCAN)
            scan = self._scanner.scan(candidate)
            self._audit.append(
                "scan_result",
                {
                    "candidate_id": candidate.candidate_id,
                    "passed": scan.passed,
                    "findings": [
                        {"rule": f.rule, "severity": f.severity, "message": _trim(f.message)}
                        for f in scan.findings
                    ],
                },
            )
            if not scan.passed:
                reason = f"security scan failed on attempt {attempt}"
                if attempt < self._max_attempts:
                    continue
                break

            sm.advance(HealingPhase.GATE)
            decision = self._policy.authorize_swap(candidate, result, scan)
            self._audit.append(
                "gate_decision",
                {
                    "candidate_id": candidate.candidate_id,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                },
            )
            if not decision.allowed:
                # A policy denial is terminal, never retried around: the gate
                # is not something the loop negotiates with.
                reason = f"policy gate denied swap: {decision.reason}"
                break

            sm.advance(HealingPhase.HOT_SWAP)
            new_version = current.child(candidate.source)
            try:
                apply_swap(new_version)
            except Exception as exc:
                self._audit.append(
                    "hot_swap_failed",
                    {
                        "candidate_id": candidate.candidate_id,
                        "tool_name": candidate.tool_name,
                        "error": _trim(f"{type(exc).__name__}: {exc}"),
                    },
                )
                reason = f"hot swap raised {type(exc).__name__}"
                break

            sm.advance(HealingPhase.RECORD)
            self._audit.append(
                "hot_swap",
                {
                    "tool_name": candidate.tool_name,
                    "from_version": current.version,
                    "to_version": new_version.version,
                    "source_hash": new_version.source_hash,
                    "candidate_id": candidate.candidate_id,
                },
            )

            sm.advance(HealingPhase.PERSIST)
            if self._memory is not None:
                score = result.tests_passed / result.tests_run if result.tests_run else 0.0
                self._memory.record(
                    HealingRecord(
                        tool_name=candidate.tool_name,
                        failure_signature=f"{failure.error_type}: {_trim(failure.error_message)}",
                        diagnosis=_trim(candidate.diagnosis),
                        source_hash=new_version.source_hash,
                        score=score,
                    )
                )
            self._audit.append(
                "healing_succeeded",
                {
                    "tool_name": candidate.tool_name,
                    "failure_id": failure.failure_id,
                    "attempts": attempt,
                    "new_version": new_version.version,
                },
            )

            sm.advance(HealingPhase.RESUME)
            return HealingOutcome(
                healed=True,
                tool_name=failure.tool_name,
                attempts=attempt,
                reason=decision.reason,
                new_version=new_version,
            )

        sm.advance(HealingPhase.FAILED)
        self._audit.append(
            "healing_failed",
            {
                "tool_name": failure.tool_name,
                "failure_id": failure.failure_id,
                "attempts": len(history),
                "reason": reason,
            },
        )
        if self._notify is not None:
            self._notify(
                f"[kaula] healing failed for tool {failure.tool_name!r}: {reason}. "
                f"The tool is left unchanged and the run is paused."
            )
        return HealingOutcome(
            healed=False,
            tool_name=failure.tool_name,
            attempts=len(history),
            reason=reason,
            paused=True,
        )
