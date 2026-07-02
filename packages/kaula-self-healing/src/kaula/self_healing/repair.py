"""Reference RepairAgent backed by Claude.

The loop stays model-agnostic — this is one implementation of the
``kaula.core.RepairAgent`` Protocol. The ``anthropic`` SDK is an optional
dependency (``pip install "kaula-self-healing[llm]"``) imported lazily, so
the loop package itself stays light.

Privacy note: the failure traceback and current tool source are sent to the
model provider as prompt content. Audit stays by-reference (the loop never
writes these into the audit chain), but the repair prompt necessarily
contains them — point this agent at an endpoint your data policy allows.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from kaula.core import RepairCandidate, ToolFailure, ToolVersion

__all__ = ["LLMRepairAgent", "extract_python_block"]

DEFAULT_MODEL = "claude-opus-4-8"

_SYSTEM_PROMPT = """\
You are Kaula's repair agent. An agent tool failed at runtime; your job is to
rewrite it so the failure cannot recur, changing as little as possible.

Hard constraints — a candidate violating any of these is rejected before it
can ship:
- Python >= 3.11, standard library only. No new third-party imports.
- Keep the entrypoint function name exactly as given, with a call-compatible
  signature (existing call sites must keep working).
- Never import subprocess, socket, ctypes, pty, pickle, or marshal.
- Never call eval, exec, compile, __import__, os.system, or os.popen.
- The candidate is verified in a network-isolated sandbox against a fixed
  test suite; it ships only if every test passes and a security scan is clean.

Respond with:
1. A one-paragraph diagnosis of the root cause.
2. Exactly one ```python code block containing the COMPLETE replacement
   source for the tool (not a diff, not a fragment).
"""


def extract_python_block(text: str) -> str | None:
    """Return the last fenced Python code block in ``text``, if any."""
    matches = re.findall(r"```(?:python|py)?\n(.*?)```", text, flags=re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip() + "\n"


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n… [trimmed]"


class LLMRepairAgent:
    """RepairAgent that asks Claude to rewrite the failed tool.

    Pass ``client`` explicitly to inject a preconfigured (or fake) Anthropic
    client; otherwise one is constructed from the environment. API errors and
    unusable responses yield ``None`` — for the loop, "no candidate" is the
    safe answer — with the cause kept on ``last_error``.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        max_tokens: int = 16000,
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError(
                    "LLMRepairAgent needs the optional LLM dependency: "
                    'pip install "kaula-self-healing[llm]"'
                ) from exc
            client = anthropic.Anthropic()
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self.last_error: str | None = None

    def propose_repair(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        history: Sequence[RepairCandidate],
    ) -> RepairCandidate | None:
        self.last_error = None
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": self._build_prompt(failure, current, history)}
                ],
            )
        except Exception as exc:
            # A failed repair is a safe state: report "no candidate" and let
            # the loop pause the run rather than crash it.
            self.last_error = f"repair request failed: {type(exc).__name__}: {exc}"
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            self.last_error = "model declined the repair request"
            return None

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        source = extract_python_block(text)
        if source is None:
            self.last_error = "response contained no Python code block"
            return None
        if f"def {current.entrypoint}" not in source:
            self.last_error = (
                f"candidate does not define the required entrypoint {current.entrypoint!r}"
            )
            return None

        diagnosis = text.split("```", 1)[0].strip() or "no diagnosis provided"
        return RepairCandidate(
            failure_id=failure.failure_id,
            tool_name=failure.tool_name,
            entrypoint=current.entrypoint,
            source=source,
            diagnosis=_trim(diagnosis, 1000),
            attempt=len(history) + 1,
        )

    def _build_prompt(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        history: Sequence[RepairCandidate],
    ) -> str:
        parts = [
            f"Tool name: {failure.tool_name}",
            f"Entrypoint to preserve: {current.entrypoint}",
            f"Repair attempt: {len(history) + 1}",
            "",
            f"Current source (version {current.version}):\n```python\n{current.source}```",
            "",
            f"Runtime failure: {failure.error_type}: {_trim(failure.error_message, 500)}",
            "",
            "Traceback:\n```\n" + _trim(failure.traceback, 3000) + "\n```",
        ]
        if failure.task_description:
            parts.append(f"\nTask the agent was performing: {failure.task_description}")
        for candidate in history:
            parts.append(
                f"\nPrevious candidate (attempt {candidate.attempt}) FAILED verification. "
                "Do not repeat it:\n```python\n" + _trim(candidate.source, 2000) + "\n```"
            )
        parts.append(
            "\nRewrite the tool so this failure cannot recur while preserving existing "
            "behaviour for inputs that already worked."
        )
        return "\n".join(parts)
