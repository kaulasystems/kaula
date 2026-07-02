import pytest
from kaula.core import (
    PHASE_ORDER,
    RETRY_PHASES,
    HealingPhase,
    InvalidTransition,
    LoopStateMachine,
)


def test_happy_path_walks_the_full_order() -> None:
    sm = LoopStateMachine()
    for phase in PHASE_ORDER[1:]:
        sm.advance(phase)
    assert sm.phase is HealingPhase.RESUME
    assert sm.is_terminal()
    assert sm.history == list(PHASE_ORDER)


def test_phases_cannot_be_skipped() -> None:
    sm = LoopStateMachine()
    with pytest.raises(InvalidTransition):
        sm.advance(HealingPhase.HOT_SWAP)
    sm.advance(HealingPhase.DIAGNOSE)
    with pytest.raises(InvalidTransition):
        sm.advance(HealingPhase.GATE)


def test_retry_returns_to_diagnose_only_from_verification_phases() -> None:
    sm = LoopStateMachine()
    sm.advance(HealingPhase.DIAGNOSE)
    sm.advance(HealingPhase.SANDBOX)
    sm.advance(HealingPhase.TEST)
    sm.advance(HealingPhase.DIAGNOSE)  # tests failed → new attempt
    assert sm.phase is HealingPhase.DIAGNOSE

    # DIAGNOSE itself is not retryable-into-DIAGNOSE
    with pytest.raises(InvalidTransition):
        sm.advance(HealingPhase.DIAGNOSE)


def test_retry_phase_set_is_exactly_the_verification_phases() -> None:
    assert RETRY_PHASES == {
        HealingPhase.SANDBOX,
        HealingPhase.TEST,
        HealingPhase.SCAN,
        HealingPhase.GATE,
    }


def test_failed_is_reachable_from_any_non_terminal_phase() -> None:
    for target in PHASE_ORDER[:-1]:  # everything before RESUME
        sm = LoopStateMachine()
        for phase in PHASE_ORDER[1:]:
            if sm.phase is target:
                break
            sm.advance(phase)
        sm.advance(HealingPhase.FAILED)
        assert sm.is_terminal()


def test_terminal_states_allow_nothing() -> None:
    sm = LoopStateMachine()
    sm.advance(HealingPhase.FAILED)
    assert sm.allowed() == frozenset()
    with pytest.raises(InvalidTransition):
        sm.advance(HealingPhase.DIAGNOSE)

    done = LoopStateMachine()
    for phase in PHASE_ORDER[1:]:
        done.advance(phase)
    assert done.allowed() == frozenset()
    with pytest.raises(InvalidTransition):
        done.advance(HealingPhase.FAILED)


def test_hot_swap_cannot_retry_silently() -> None:
    """Once past the gate, the only ways forward are RECORD or FAILED —
    a swap can never loop back and try again invisibly."""
    sm = LoopStateMachine()
    for phase in (
        HealingPhase.DIAGNOSE,
        HealingPhase.SANDBOX,
        HealingPhase.TEST,
        HealingPhase.SCAN,
        HealingPhase.GATE,
        HealingPhase.HOT_SWAP,
    ):
        sm.advance(phase)
    assert sm.allowed() == {HealingPhase.RECORD, HealingPhase.FAILED}
