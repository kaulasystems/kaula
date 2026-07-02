"""Building and reading the sandbox work bundle.

The bundle is the only thing that crosses into the isolated environment: the
candidate source (``tool.py``), a static test harness (``harness.py``), and a
JSON payload naming the entrypoint and test cases. The harness prints one
marker line of JSON so results survive whatever the candidate writes to
stdout.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from kaula.core import RepairCandidate, SandboxResult, ToolTest

__all__ = ["RESULT_MARKER", "parse_harness_output", "write_bundle"]

RESULT_MARKER = "KAULA_RESULT "

_HARNESS_SOURCE = """\
import importlib
import json
import sys
import traceback


def main() -> int:
    with open("payload.json", encoding="utf-8") as handle:
        payload = json.load(handle)
    module = importlib.import_module("tool")
    fn = getattr(module, payload["entrypoint"])
    results = []
    for case in payload["tests"]:
        record = {"name": case["name"], "passed": True, "detail": ""}
        try:
            out = fn(*case.get("args", []), **case.get("kwargs", {}))
            if case.get("check") == "equals" and out != case.get("expected"):
                record["passed"] = False
                record["detail"] = "expected %r, got %r" % (case.get("expected"), out)
        except BaseException:
            record["passed"] = False
            record["detail"] = traceback.format_exc(limit=5)
        results.append(record)
    print("KAULA_RESULT " + json.dumps({"results": results}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def write_bundle(directory: Path, candidate: RepairCandidate, tests: Sequence[ToolTest]) -> None:
    payload = {
        "entrypoint": candidate.entrypoint,
        "tests": [
            {
                "name": test.name,
                "args": list(test.args),
                "kwargs": dict(test.kwargs),
                "expected": test.expected,
                "check": test.check,
            }
            for test in tests
        ],
    }
    try:
        payload_json = json.dumps(payload)
    except TypeError as exc:
        raise ValueError(
            "ToolTest args/kwargs/expected must be JSON-serialisable to cross "
            f"the sandbox boundary: {exc}"
        ) from exc
    (directory / "tool.py").write_text(candidate.source, encoding="utf-8")
    (directory / "harness.py").write_text(_HARNESS_SOURCE, encoding="utf-8")
    (directory / "payload.json").write_text(payload_json, encoding="utf-8")


def parse_harness_output(
    stdout: str,
    stderr: str,
    returncode: int,
    duration_s: float,
) -> SandboxResult:
    marker_lines = [line for line in stdout.splitlines() if line.startswith(RESULT_MARKER)]
    if not marker_lines:
        return SandboxResult(
            passed=False,
            tests_run=0,
            tests_passed=0,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration_s,
            infra_error=f"harness produced no result marker (exit code {returncode})",
        )
    data = json.loads(marker_lines[-1][len(RESULT_MARKER) :])
    results = data["results"]
    failures = tuple(
        f"{record['name']}: {record['detail']}" for record in results if not record["passed"]
    )
    tests_run = len(results)
    tests_passed = tests_run - len(failures)
    return SandboxResult(
        passed=returncode == 0 and tests_run > 0 and not failures,
        tests_run=tests_run,
        tests_passed=tests_passed,
        failures=failures,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration_s,
    )
