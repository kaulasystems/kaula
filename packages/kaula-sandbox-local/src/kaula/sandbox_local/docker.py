"""Reference Sandbox: local Docker execution.

Single-tenant and not escape-hardened (see README). Isolation applied to
every run: no network, no inherited environment (no ambient credentials),
read-only bundle mount, read-only root filesystem with a tmpfs /tmp,
memory/CPU/pid limits, and a hard timeout.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from kaula.core import RepairCandidate, SandboxResult, ToolTest
from kaula.sandbox_local.bundle import parse_harness_output, write_bundle

__all__ = ["DockerSandbox"]


class DockerSandbox:
    def __init__(
        self,
        image: str = "python:3.11-slim",
        *,
        docker_executable: str = "docker",
        memory: str = "256m",
        cpus: str = "0.5",
        pids_limit: int = 64,
    ) -> None:
        self._image = image
        self._docker = docker_executable
        self._memory = memory
        self._cpus = cpus
        self._pids_limit = pids_limit

    def build_command(self, bundle_dir: Path) -> list[str]:
        return [
            self._docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            self._memory,
            "--cpus",
            self._cpus,
            "--pids-limit",
            str(self._pids_limit),
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--volume",
            f"{bundle_dir}:/work:ro",
            "--workdir",
            "/work",
            self._image,
            "python",
            "harness.py",
        ]

    def run(
        self,
        candidate: RepairCandidate,
        tests: Sequence[ToolTest],
        *,
        timeout_s: float = 30.0,
    ) -> SandboxResult:
        if not tests:
            return SandboxResult(
                passed=False,
                tests_run=0,
                tests_passed=0,
                infra_error="no tests provided — refusing to verify a candidate blind",
            )
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="kaula-sandbox-") as tmp:
            bundle_dir = Path(tmp)
            write_bundle(bundle_dir, candidate, tests)
            command = self.build_command(bundle_dir)
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                return SandboxResult(
                    passed=False,
                    tests_run=0,
                    tests_passed=0,
                    stdout=_as_text(exc.stdout),
                    stderr=_as_text(exc.stderr),
                    duration_s=time.monotonic() - started,
                    infra_error=f"sandbox timed out after {timeout_s}s",
                )
            except FileNotFoundError:
                return SandboxResult(
                    passed=False,
                    tests_run=0,
                    tests_passed=0,
                    duration_s=time.monotonic() - started,
                    infra_error=(
                        f"docker executable {self._docker!r} not found — "
                        "DockerSandbox needs a local Docker daemon"
                    ),
                )
        return parse_harness_output(
            completed.stdout,
            completed.stderr,
            completed.returncode,
            time.monotonic() - started,
        )


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data
