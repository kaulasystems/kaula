from kaula.core import (
    PermissivePolicyEngine,
    RepairCandidate,
    SandboxResult,
    ScanFinding,
    ScanResult,
    ToolFailure,
    ToolVersion,
    fingerprint_args,
    sha256_hex,
)


def _candidate(source: str = "def f():\n    return 1\n") -> RepairCandidate:
    return RepairCandidate(
        failure_id="fail-1",
        tool_name="f",
        entrypoint="f",
        source=source,
        diagnosis="test",
        attempt=1,
    )


def test_tool_failure_from_exception_captures_context() -> None:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        failure = ToolFailure.from_exception(
            "my_tool", exc, args=(1,), kwargs={"x": 2}, run_id="run-1"
        )
    assert failure.tool_name == "my_tool"
    assert failure.error_type == "ValueError"
    assert failure.error_message == "boom"
    assert "ValueError: boom" in failure.traceback
    assert failure.run_id == "run-1"
    assert failure.failure_id.startswith("fail-")


def test_args_fingerprint_is_stable_and_opaque() -> None:
    fp1 = fingerprint_args(("secret pii",), {"a": 1})
    fp2 = fingerprint_args(("secret pii",), {"a": 1})
    fp3 = fingerprint_args(("secret pii",), {"a": 2})
    assert fp1 == fp2
    assert fp1 != fp3
    assert "secret" not in fp1
    assert len(fp1) == 64


def test_tool_version_lineage() -> None:
    v1 = ToolVersion.initial("t", "run", "def run(): ...")
    assert v1.version == 1
    assert v1.parent_version is None
    assert v1.source_hash == sha256_hex(v1.source)

    v2 = v1.child("def run():\n    return 2\n")
    assert v2.version == 2
    assert v2.parent_version == 1
    assert v2.tool_name == "t"
    assert v2.entrypoint == "run"
    assert v2.source_hash != v1.source_hash


def test_repair_candidate_source_hash() -> None:
    candidate = _candidate()
    assert candidate.source_hash == sha256_hex(candidate.source)
    assert candidate.candidate_id.startswith("cand-")


def test_permissive_policy_all_green() -> None:
    decision = PermissivePolicyEngine().authorize_swap(
        _candidate(),
        SandboxResult(passed=True, tests_run=3, tests_passed=3),
        ScanResult(passed=True),
    )
    assert decision.allowed


def test_permissive_policy_denies_when_not_green() -> None:
    engine = PermissivePolicyEngine()
    candidate = _candidate()
    green_sandbox = SandboxResult(passed=True, tests_run=3, tests_passed=3)
    red_sandbox = SandboxResult(passed=False, tests_run=3, tests_passed=1)
    infra_sandbox = SandboxResult(
        passed=False, tests_run=0, tests_passed=0, infra_error="docker missing"
    )
    red_scan = ScanResult(
        passed=False, findings=(ScanFinding(rule="banned-call", severity="critical", message="x"),)
    )

    assert not engine.authorize_swap(candidate, red_sandbox, ScanResult(passed=True)).allowed
    assert not engine.authorize_swap(candidate, green_sandbox, red_scan).allowed
    denial = engine.authorize_swap(candidate, infra_sandbox, ScanResult(passed=True))
    assert not denial.allowed
    assert "docker missing" in denial.reason
