"""The loop contract: an explicit, testable state machine.

DETECT → DIAGNOSE → SANDBOX → TEST → SCAN → GATE → HOT-SWAP → RECORD →
PERSIST → RESUME, with bounded retry back to DIAGNOSE from the verification
phases and FAILED reachable from any non-terminal phase. A failed repair is a
safe state: the run stays paused, nothing unverified ships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "HealingPhase",
    "InvalidTransition",
    "LoopStateMachine",
    "PHASE_ORDER",
    "RETRY_PHASES",
    "TERMINAL_PHASES",
]


class HealingPhase(StrEnum):
    DETECT = "detect"
    DIAGNOSE = "diagnose"
    SANDBOX = "sandbox"
    TEST = "test"
    SCAN = "scan"
    GATE = "gate"
    HOT_SWAP = "hot_swap"
    RECORD = "record"
    PERSIST = "persist"
    RESUME = "resume"
    FAILED = "failed"


PHASE_ORDER: tuple[HealingPhase, ...] = (
    HealingPhase.DETECT,
    HealingPhase.DIAGNOSE,
    HealingPhase.SANDBOX,
    HealingPhase.TEST,
    HealingPhase.SCAN,
    HealingPhase.GATE,
    HealingPhase.HOT_SWAP,
    HealingPhase.RECORD,
    HealingPhase.PERSIST,
    HealingPhase.RESUME,
)

# Phases from which the loop may retry with a fresh diagnosis.
RETRY_PHASES: frozenset[HealingPhase] = frozenset(
    {HealingPhase.SANDBOX, HealingPhase.TEST, HealingPhase.SCAN, HealingPhase.GATE}
)

TERMINAL_PHASES: frozenset[HealingPhase] = frozenset({HealingPhase.RESUME, HealingPhase.FAILED})


class InvalidTransition(RuntimeError):
    def __init__(self, current: HealingPhase, attempted: HealingPhase) -> None:
        super().__init__(f"invalid healing transition: {current.value} -> {attempted.value}")
        self.current = current
        self.attempted = attempted


@dataclass
class LoopStateMachine:
    """Enforces the ordered loop contract; implementations drive it, never skip it."""

    phase: HealingPhase = HealingPhase.DETECT
    history: list[HealingPhase] = field(default_factory=lambda: [HealingPhase.DETECT])

    def allowed(self) -> frozenset[HealingPhase]:
        if self.is_terminal():
            return frozenset()
        allowed: set[HealingPhase] = {HealingPhase.FAILED}
        index = PHASE_ORDER.index(self.phase)
        if index + 1 < len(PHASE_ORDER):
            allowed.add(PHASE_ORDER[index + 1])
        if self.phase in RETRY_PHASES:
            allowed.add(HealingPhase.DIAGNOSE)
        return frozenset(allowed)

    def advance(self, next_phase: HealingPhase) -> None:
        if next_phase not in self.allowed():
            raise InvalidTransition(self.phase, next_phase)
        self.phase = next_phase
        self.history.append(next_phase)

    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES
