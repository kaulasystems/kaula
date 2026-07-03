"""The canonical Kaula demo: break a tool, watch it heal, inspect the audit trail.

    make demo-healing

A price-parsing tool crashes on grouped digits ("1,234.56"). Kaula captures
the failure, a repair agent proposes a rewrite, the candidate is verified in
a sandbox (tests + static security scan), the policy gate approves it, and
the tool is hot-swapped live — the original call then succeeds. Every step
lands in a hash-chained audit trail, and the swap is reverted in one logged
action at the end.

With ANTHROPIC_API_KEY set (and the optional LLM extra installed:
pip install "kaula-self-healing[llm]"), the repair is proposed live by
LLMRepairAgent; otherwise a deterministic scripted agent stands in so the
demo always shows the *loop*: capture → verify → gate → swap → audit →
rollback.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from kaula.audit_local import SqliteAuditSink, ToolVersionStore
from kaula.core import (
    RepairCandidate,
    SandboxResult,
    ToolFailure,
    ToolTest,
    ToolVersion,
)
from kaula.runtime import HealingToolWrapper
from kaula.sandbox_local import DockerSandbox, parse_harness_output, write_bundle
from kaula.self_healing import BasicStaticScanner, SelfHealingLoop

BROKEN_SOURCE = '''\
def parse_price(text):
    """Parse a price string like '1234.56' into a float."""
    return float(text)
'''

FIXED_SOURCE = '''\
def parse_price(text):
    """Parse a price string like '$1,234.56' into a float."""
    cleaned = text.strip().lstrip("$\\u20ac\\u00a3").replace(",", "")
    return float(cleaned)
'''

TESTS = (
    ToolTest(name="plain", args=("1234.56",), expected=1234.56, check="equals"),
    ToolTest(name="grouped", args=("1,234.56",), expected=1234.56, check="equals"),
    ToolTest(name="currency", args=("$1,234.56",), expected=1234.56, check="equals"),
)


class ScriptedRepairAgent:
    """Demo stand-in for the LLM-backed RepairAgent: always proposes the fix."""

    def propose_repair(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        history: Sequence[RepairCandidate],
    ) -> RepairCandidate | None:
        return RepairCandidate(
            failure_id=failure.failure_id,
            tool_name=failure.tool_name,
            entrypoint=current.entrypoint,
            source=FIXED_SOURCE,
            diagnosis="float() cannot parse digit grouping or currency symbols; strip them first",
            attempt=len(history) + 1,
        )


class DemoProcessSandbox:
    """DEMO-ONLY fallback when no Docker daemon is available: runs the
    candidate in a plain subprocess with NO isolation. Never use outside
    this demo — the reference sandbox is kaula.sandbox_local.DockerSandbox."""

    def run(
        self,
        candidate: RepairCandidate,
        tests: Sequence[ToolTest],
        *,
        timeout_s: float = 30.0,
    ) -> SandboxResult:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="kaula-demo-sbx-") as tmp:
            bundle = Path(tmp)
            write_bundle(bundle, candidate, tests)
            completed = subprocess.run(
                [sys.executable, "harness.py"],
                cwd=bundle,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        return parse_harness_output(
            completed.stdout, completed.stderr, completed.returncode, time.monotonic() - started
        )


def pick_repair_agent() -> object:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from kaula.self_healing import LLMRepairAgent

            agent = LLMRepairAgent()
        except ImportError:
            print(
                "repair agent: ANTHROPIC_API_KEY is set but the anthropic SDK is missing "
                '(pip install "kaula-self-healing[llm]") — using the scripted agent'
            )
            return ScriptedRepairAgent()
        print("repair agent: LLMRepairAgent (live Claude repair)")
        return agent
    print("repair agent: scripted (set ANTHROPIC_API_KEY for a live LLM repair)")
    return ScriptedRepairAgent()


def pick_sandbox() -> DockerSandbox | DemoProcessSandbox:
    if shutil.which("docker") is not None:
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        if probe.returncode == 0:
            print("sandbox: DockerSandbox (local Docker daemon)")
            return DockerSandbox()
    print("sandbox: no Docker daemon found — using the DEMO-ONLY process sandbox")
    return DemoProcessSandbox()


def main() -> int:
    audit = SqliteAuditSink()
    store = ToolVersionStore(audit=audit)

    loop = SelfHealingLoop(
        repair_agent=pick_repair_agent(),  # type: ignore[arg-type]
        sandbox=pick_sandbox(),
        scanner=BasicStaticScanner(),
        audit=audit,
        notify=lambda message: print(f"NOTIFY: {message}"),
    )

    namespace: dict[str, object] = {}
    exec(compile(BROKEN_SOURCE, "<demo:v1>", "exec"), namespace)
    wrapper = HealingToolWrapper(
        tool_name="parse_price",
        func=namespace["parse_price"],  # type: ignore[arg-type]
        loop=loop,
        tests=TESTS,
        source=BROKEN_SOURCE,
        run_id="demo-run",
        on_swap=store.register,
    )
    store.register(wrapper.version)

    print("\n--- 1. break the tool ---")
    print('calling parse_price("$1,234.56") against v1 (naive float()) ...')
    result = wrapper("$1,234.56")
    print(f"call returned {result!r} — healed live, v{wrapper.version.version} is now active")

    print("\n--- 2. inspect the audit trail ---")
    for event in audit.events():
        interesting = {
            key: value
            for key, value in event.payload.items()
            if key
            in ("tool_name", "passed", "allowed", "reason", "to_version", "from_version", "attempt")
        }
        print(f"  #{event.sequence:<2} {event.event_type:<24} {interesting}")
    print(f"chain verifies: {audit.verify()}")

    print("\n--- 3. one-step rollback ---")
    reverted = store.rollback("parse_price")
    print(f"active version is v{reverted.version} again (logged as a single audit event)")
    last = list(audit.events())[-1]
    print(f"  #{last.sequence:<2} {last.event_type:<24} {dict(last.payload)}")
    print(f"chain verifies: {audit.verify()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
