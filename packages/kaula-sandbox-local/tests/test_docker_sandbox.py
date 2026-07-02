"""DockerSandbox unit tests: isolation flags and result handling, with the
docker invocation faked. A real end-to-end run lives in
test_harness_locally / the skipped-if-no-docker integration test."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kaula.core import RepairCandidate, ToolTest
from kaula.sandbox_local import RESULT_MARKER, DockerSandbox, write_bundle

CANDIDATE = RepairCandidate(
    failure_id="fail-1",
    tool_name="parse",
    entrypoint="parse",
    source="def parse(x):\n    return float(x.replace(',', ''))\n",
    diagnosis="handle grouped digits",
    attempt=1,
)

TESTS = (
    ToolTest(name="plain", args=("1234.5",), expected=1234.5, check="equals"),
    ToolTest(name="grouped", args=("1,234.5",), expected=1234.5, check="equals"),
)


def test_command_enforces_isolation() -> None:
    command = DockerSandbox().build_command(Path("/bundle"))
    text = " ".join(command)
    assert "--network none" in text
    assert "--read-only" in text
    assert "--pids-limit 64" in text
    assert "/bundle:/work:ro" in text
    # no ambient credentials: the only env var passed is the bytecode toggle
    assert command.count("--env") == 1
    assert "PYTHONDONTWRITEBYTECODE=1" in command


def test_successful_run_parses_marker_output(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = RESULT_MARKER + json.dumps(
        {"results": [{"name": "plain", "passed": True, "detail": ""}]}
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(command, 0, stdout=f"tool noise\n{marker}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = DockerSandbox().run(CANDIDATE, TESTS[:1])
    assert result.passed
    assert result.tests_run == 1
    assert result.tests_passed == 1


def test_failing_tests_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = RESULT_MARKER + json.dumps(
        {
            "results": [
                {"name": "plain", "passed": True, "detail": ""},
                {"name": "grouped", "passed": False, "detail": "ValueError"},
            ]
        }
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(command, 0, stdout=marker, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = DockerSandbox().run(CANDIDATE, TESTS)
    assert not result.passed
    assert result.tests_passed == 1
    assert result.failures == ("grouped: ValueError",)


def test_timeout_is_an_infra_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = DockerSandbox().run(CANDIDATE, TESTS, timeout_s=5.0)
    assert not result.passed
    assert result.infra_error is not None
    assert "timed out" in result.infra_error


def test_missing_docker_is_an_infra_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = DockerSandbox().run(CANDIDATE, TESTS)
    assert not result.passed
    assert result.infra_error is not None
    assert "docker" in result.infra_error


def test_empty_test_suite_is_refused() -> None:
    result = DockerSandbox().run(CANDIDATE, ())
    assert not result.passed
    assert result.infra_error is not None
    assert "no tests" in result.infra_error


def test_garbled_container_output_is_an_infra_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(command, 137, stdout="OOM", stderr="killed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = DockerSandbox().run(CANDIDATE, TESTS)
    assert not result.passed
    assert result.infra_error is not None
    assert "137" in result.infra_error


def test_unserialisable_tests_rejected_before_leaving_the_process() -> None:
    bad_tests = (ToolTest(name="bad", args=(object(),)),)
    with pytest.raises(ValueError, match="JSON-serialisable"):
        DockerSandbox().run(CANDIDATE, bad_tests)


def test_harness_locally(tmp_path: Path) -> None:
    """The bundle + harness works end to end (run with this interpreter,
    outside Docker — candidate source here is our own fixture)."""
    write_bundle(tmp_path, CANDIDATE, TESTS)
    completed = subprocess.run(
        [sys.executable, "harness.py"], cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    from kaula.sandbox_local import parse_harness_output

    result = parse_harness_output(completed.stdout, completed.stderr, completed.returncode, 0.0)
    assert result.passed
    assert result.tests_run == 2
    assert result.tests_passed == 2


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_docker_integration() -> None:
    """Real container run; skipped unless a Docker daemon is reachable."""
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
    if probe.returncode != 0:
        pytest.skip("docker daemon not reachable")
    result = DockerSandbox().run(CANDIDATE, TESTS, timeout_s=120.0)
    assert result.infra_error is None, result.infra_error
    assert result.passed
