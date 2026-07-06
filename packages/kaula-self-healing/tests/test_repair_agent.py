"""LLMRepairAgent tests — the Anthropic client is injected as a fake, so no
network and no API key are involved."""

from types import SimpleNamespace
from typing import Any

import pytest
from kaula.core import RepairCandidate, ToolFailure, ToolVersion
from kaula.self_healing import (
    LLMRepairAgent,
    build_repair_prompt,
    candidate_from_reply,
)
from kaula.self_healing.repair import extract_python_block

BROKEN_SOURCE = "def parse_price(text):\n    return float(text)\n"
FIXED_SOURCE = "def parse_price(text):\n    return float(text.replace(',', ''))\n"

GOOD_REPLY = (
    "float() cannot parse digit grouping; strip commas before conversion.\n\n"
    "```python\n" + FIXED_SOURCE + "```\n"
)


class FakeClient:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def make_response(text: str, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


@pytest.fixture
def failure() -> ToolFailure:
    try:
        float("1,234.56")
    except ValueError as exc:
        return ToolFailure.from_exception(
            "parse_price", exc, args=("1,234.56",), task_description="extract prices"
        )
    raise AssertionError


@pytest.fixture
def current() -> ToolVersion:
    return ToolVersion.initial("parse_price", "parse_price", BROKEN_SOURCE)


def test_proposes_candidate_from_model_reply(failure: ToolFailure, current: ToolVersion) -> None:
    client = FakeClient(make_response(GOOD_REPLY))
    agent = LLMRepairAgent(client=client)

    candidate = agent.propose_repair(failure, current, ())

    assert candidate is not None
    assert candidate.source == FIXED_SOURCE
    assert candidate.entrypoint == "parse_price"
    assert candidate.attempt == 1
    assert "digit grouping" in candidate.diagnosis
    assert agent.last_error is None


def test_prompt_carries_failure_context_and_constraints(
    failure: ToolFailure, current: ToolVersion
) -> None:
    client = FakeClient(make_response(GOOD_REPLY))
    LLMRepairAgent(client=client).propose_repair(failure, current, ())

    request = client.requests[0]
    prompt = request["messages"][0]["content"]
    assert BROKEN_SOURCE in prompt
    assert "ValueError" in prompt
    assert "extract prices" in prompt
    assert "subprocess" in request["system"]  # deny-list stated up front
    assert request["model"] == "claude-opus-4-8"
    assert request["thinking"] == {"type": "adaptive"}
    # sampling params are rejected by current models — must not be sent
    assert "temperature" not in request


def test_history_of_failed_candidates_is_included(
    failure: ToolFailure, current: ToolVersion
) -> None:
    client = FakeClient(make_response(GOOD_REPLY))
    previous = RepairCandidate(
        failure_id=failure.failure_id,
        tool_name="parse_price",
        entrypoint="parse_price",
        source="def parse_price(text):\n    return 0.0\n",
        diagnosis="wrong guess",
        attempt=1,
    )

    candidate = LLMRepairAgent(client=client).propose_repair(failure, current, (previous,))

    prompt = client.requests[0]["messages"][0]["content"]
    assert "FAILED verification" in prompt
    assert "return 0.0" in prompt
    assert candidate is not None
    assert candidate.attempt == 2


def test_api_error_yields_no_candidate(failure: ToolFailure, current: ToolVersion) -> None:
    agent = LLMRepairAgent(client=FakeClient(RuntimeError("api down")))
    assert agent.propose_repair(failure, current, ()) is None
    assert agent.last_error is not None
    assert "api down" in agent.last_error


def test_refusal_yields_no_candidate(failure: ToolFailure, current: ToolVersion) -> None:
    agent = LLMRepairAgent(client=FakeClient(make_response("", stop_reason="refusal")))
    assert agent.propose_repair(failure, current, ()) is None
    assert agent.last_error is not None
    assert "declined" in agent.last_error


def test_reply_without_code_block_yields_no_candidate(
    failure: ToolFailure, current: ToolVersion
) -> None:
    agent = LLMRepairAgent(client=FakeClient(make_response("I cannot fix this.")))
    assert agent.propose_repair(failure, current, ()) is None


def test_reply_missing_entrypoint_yields_no_candidate(
    failure: ToolFailure, current: ToolVersion
) -> None:
    reply = "Diagnosis.\n\n```python\ndef other():\n    return 1\n```\n"
    agent = LLMRepairAgent(client=FakeClient(make_response(reply)))
    assert agent.propose_repair(failure, current, ()) is None
    assert agent.last_error is not None
    assert "entrypoint" in agent.last_error


def test_extract_python_block_variants() -> None:
    assert extract_python_block("```python\nx = 1\n```") == "x = 1\n"
    assert extract_python_block("```\nx = 1\n```") == "x = 1\n"
    assert extract_python_block("no code here") is None
    two_blocks = "```python\nfirst = 1\n```\ntext\n```python\nsecond = 2\n```"
    assert extract_python_block(two_blocks) == "second = 2\n"


# --- provider-agnostic helpers (used by non-Claude RepairAgents) ---


def test_build_repair_prompt_is_provider_neutral(
    failure: ToolFailure, current: ToolVersion
) -> None:
    system, user = build_repair_prompt(failure, current, ())
    assert "standard library only" in system
    assert "subprocess" in system  # deny-list stated up front
    assert BROKEN_SOURCE in user
    assert "ValueError" in user
    assert "extract prices" in user  # task_description carried through


def test_candidate_from_reply_parses_good_reply(failure: ToolFailure, current: ToolVersion) -> None:
    candidate = candidate_from_reply(GOOD_REPLY, failure, current, ())
    assert candidate.source == FIXED_SOURCE
    assert candidate.entrypoint == "parse_price"
    assert candidate.attempt == 1
    assert "digit grouping" in candidate.diagnosis


def test_candidate_from_reply_rejects_missing_block(
    failure: ToolFailure, current: ToolVersion
) -> None:
    with pytest.raises(ValueError, match="no Python code block"):
        candidate_from_reply("I cannot fix this.", failure, current, ())


def test_candidate_from_reply_rejects_wrong_entrypoint(
    failure: ToolFailure, current: ToolVersion
) -> None:
    reply = "Diagnosis.\n\n```python\ndef other():\n    return 1\n```\n"
    with pytest.raises(ValueError, match="entrypoint"):
        candidate_from_reply(reply, failure, current, ())


def test_candidate_from_reply_counts_attempt_from_history(
    failure: ToolFailure, current: ToolVersion
) -> None:
    previous = RepairCandidate(
        failure_id=failure.failure_id,
        tool_name="parse_price",
        entrypoint="parse_price",
        source="def parse_price(text):\n    return 0.0\n",
        diagnosis="d",
        attempt=1,
    )
    candidate = candidate_from_reply(GOOD_REPLY, failure, current, (previous,))
    assert candidate.attempt == 2
