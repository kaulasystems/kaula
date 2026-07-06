"""Human-in-the-loop gates: after detection, and after auto-repair."""

from kaula.self_healing import (
    Approval,
    ConsoleReviewer,
    always_approve,
    always_reject,
)
from test_healing_loop import (
    GOOD_SOURCE,
    TESTS,
    FakeRepairAgent,
    RecordingAudit,
    current,  # noqa: F401 — pytest fixture
    failure,  # noqa: F401 — pytest fixture
    make_loop,
)

# --- gate #1: review after detection, before any repair ---


def test_failure_rejected_skips_repair_entirely(failure, current) -> None:  # noqa: F811
    audit = RecordingAudit()
    agent = FakeRepairAgent([GOOD_SOURCE])
    swapped = []
    loop = make_loop(agent, audit, review_failure=always_reject)

    outcome = loop.heal(failure, current, TESTS, apply_swap=swapped.append)

    assert not outcome.healed and outcome.paused
    assert outcome.attempts == 0
    assert agent.calls == 0  # repair agent was never consulted
    assert swapped == []
    assert audit.types() == ["failure_detected", "failure_review", "healing_failed"]
    assert "declined to auto-repair" in outcome.reason


def test_failure_approved_proceeds_to_heal(failure, current) -> None:  # noqa: F811
    audit = RecordingAudit()
    loop = make_loop(FakeRepairAgent([GOOD_SOURCE]), audit, review_failure=always_approve)

    outcome = loop.heal(failure, current, TESTS, apply_swap=lambda v: None)

    assert outcome.healed
    assert "failure_review" in audit.types()
    assert audit.types().index("failure_review") < audit.types().index("repair_proposed")


def test_failure_reviewer_sees_the_failure(failure, current) -> None:  # noqa: F811
    seen = []

    def reviewer(f, c):
        seen.append((f.tool_name, c.version))
        return Approval.approve()

    make_loop(FakeRepairAgent([GOOD_SOURCE]), RecordingAudit(), review_failure=reviewer).heal(
        failure, current, TESTS, apply_swap=lambda v: None
    )
    assert seen == [("parse", 1)]


# --- gate #2: review after auto-repair, before hot-swap ---


def test_candidate_rejected_blocks_the_swap(failure, current) -> None:  # noqa: F811
    audit = RecordingAudit()
    swapped = []
    loop = make_loop(FakeRepairAgent([GOOD_SOURCE]), audit, review_candidate=always_reject)

    outcome = loop.heal(failure, current, TESTS, apply_swap=swapped.append)

    assert not outcome.healed and outcome.paused
    assert swapped == []  # verified, but a human said no → nothing shipped
    assert "human rejected verified candidate" in outcome.reason
    # the candidate was fully verified before the human was asked
    types = audit.types()
    assert types == [
        "failure_detected",
        "repair_proposed",
        "sandbox_result",
        "scan_result",
        "gate_decision",
        "candidate_review",
        "healing_failed",
    ]


def test_candidate_approved_swaps(failure, current) -> None:  # noqa: F811
    audit = RecordingAudit()
    swapped = []
    loop = make_loop(FakeRepairAgent([GOOD_SOURCE]), audit, review_candidate=always_approve)

    outcome = loop.heal(failure, current, TESTS, apply_swap=swapped.append)

    assert outcome.healed
    assert len(swapped) == 1
    types = audit.types()
    assert types.index("candidate_review") < types.index("hot_swap")


def test_candidate_reviewer_sees_verified_results(failure, current) -> None:  # noqa: F811
    seen = []

    def reviewer(candidate, sandbox_result, scan_result):
        seen.append((candidate.tool_name, sandbox_result.passed, scan_result.passed))
        return Approval.reject("not now")

    make_loop(FakeRepairAgent([GOOD_SOURCE]), RecordingAudit(), review_candidate=reviewer).heal(
        failure, current, TESTS, apply_swap=lambda v: None
    )
    assert seen == [("parse", True, True)]


def test_both_gates_compose(failure, current) -> None:  # noqa: F811
    audit = RecordingAudit()
    swapped = []
    loop = make_loop(
        FakeRepairAgent([GOOD_SOURCE]),
        audit,
        review_failure=always_approve,
        review_candidate=always_approve,
    )
    outcome = loop.heal(failure, current, TESTS, apply_swap=swapped.append)
    assert outcome.healed and len(swapped) == 1
    types = audit.types()
    assert "failure_review" in types and "candidate_review" in types


# --- the reference ConsoleReviewer ---


def test_console_reviewer_yes_approves() -> None:
    captured = []
    reviewer = ConsoleReviewer(input_fn=lambda _prompt: "y", output_fn=captured.append)
    from kaula.core import ToolFailure, ToolVersion

    v = ToolVersion.initial("t", "run", "def run(): ...")
    try:
        int("x")
    except ValueError as exc:
        f = ToolFailure.from_exception("t", exc)
    assert reviewer.review_failure(f, v).approved
    assert any("failed" in line for line in captured)


def test_console_reviewer_default_rejects() -> None:
    reviewer = ConsoleReviewer(input_fn=lambda _prompt: "", output_fn=lambda _msg: None)
    from kaula.core import ToolFailure, ToolVersion

    v = ToolVersion.initial("t", "run", "def run(): ...")
    try:
        int("x")
    except ValueError as exc:
        f = ToolFailure.from_exception("t", exc)
    decision = reviewer.review_failure(f, v)
    assert not decision.approved
    assert "declined" in decision.reason
