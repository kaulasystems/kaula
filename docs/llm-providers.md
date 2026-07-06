# Using a different LLM provider (OpenAI, Azure, Bedrock, local, …)

Kaula's healing loop is **model-agnostic**. The only place the open tier calls
an LLM is the *repair agent* — the component that rewrites a failed tool — and
that's a `kaula.core.RepairAgent` **Protocol**, not a hardwired vendor. The
reference `LLMRepairAgent` uses Claude; swapping in OpenAI, Azure OpenAI,
Amazon Bedrock, Google Vertex, or a local model is just implementing that one
Protocol.

> **Two different LLM touchpoints — don't confuse them.**
> 1. **Kaula's repair agent** — heals broken tools. Provider chosen here. This
>    page is about this one.
> 2. **Your CrewAI agents** — the workload doing your actual task. Their model
>    is configured through **CrewAI itself** (its `LLM` class / env vars like
>    `OPENAI_API_KEY`, `AZURE_API_KEY`), not through Kaula. See CrewAI's docs.
>
> They're independent: you can run GPT‑4o agents that heal with Claude, or
> Claude agents that heal with a local model — whatever you configure for each.

## The contract

A repair agent implements one method:

```python
def propose_repair(
    failure: ToolFailure,
    current: ToolVersion,
    history: Sequence[RepairCandidate],
) -> RepairCandidate | None: ...
```

Return a `RepairCandidate` (a **complete** replacement source that keeps the
entrypoint name), or `None` when you can't produce one — for the loop, "no
candidate" is the safe state (the run pauses, nothing unverified ships).

**You do not have to write the prompt or parse the reply yourself.** Kaula
exposes the vetted, provider-neutral pieces:

- `build_repair_prompt(failure, current, history) -> (system, user)` — the
  system + user messages, including the security deny-list and the
  "complete source, keep the entrypoint" instructions.
- `candidate_from_reply(reply, failure, current, history) -> RepairCandidate`
  — parses the model's `diagnosis + one python code block`; raises
  `ValueError` if there's no code block or the wrong entrypoint (treat as
  "no candidate").

## Safety is provider-independent

Whatever model you use, **the loop still verifies every proposal** in the
sandbox (your tests) + the security scan + the policy gate before hot-swapping.
A weaker model doesn't make Kaula unsafe — it just fails verification more
often, so you see more paused runs and fewer live swaps. Kaula never ships an
unverified fix regardless of which LLM produced it.

Prefer a **code-capable** model (GPT‑4o / o‑series, Claude, a strong local
coding model). Cheaper models work but heal less reliably.

## A reusable base for any chat-completions provider

Every example below subclasses this ~20-line base — the only per-provider code
is one `complete()` method. Put it in your app (e.g. `myapp/repair_agents.py`):

```python
from collections.abc import Sequence

from kaula.core import RepairCandidate, ToolFailure, ToolVersion
from kaula.self_healing import build_repair_prompt, candidate_from_reply


class ChatRepairAgent:
    """Base RepairAgent for any chat-completions API.

    Subclass and implement ``complete(system, user) -> str``. Request errors
    and unusable replies become ``None`` (the loop's safe state); the cause is
    kept on ``last_error``.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.last_error: str | None = None

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError

    def propose_repair(
        self,
        failure: ToolFailure,
        current: ToolVersion,
        history: Sequence[RepairCandidate],
    ) -> RepairCandidate | None:
        self.last_error = None
        system, user = build_repair_prompt(failure, current, history)
        try:
            reply = self.complete(system, user)
        except Exception as exc:
            self.last_error = f"repair request failed: {type(exc).__name__}: {exc}"
            return None
        try:
            return candidate_from_reply(reply, failure, current, history)
        except ValueError as exc:
            self.last_error = str(exc)
            return None
```

The provider SDKs below are **your** dependencies, not Kaula's — install
whichever you use (`pip install openai`, `pip install litellm`, …).

### OpenAI

```python
from openai import OpenAI  # pip install openai ; export OPENAI_API_KEY=...


class OpenAIRepairAgent(ChatRepairAgent):
    def __init__(self, model: str = "gpt-4o", **client_kwargs) -> None:
        super().__init__(model)
        self._client = OpenAI(**client_kwargs)

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
```

### Azure OpenAI

Identical wire format; you point at your Azure resource and pass the
**deployment name** as the model.

```python
import os

from openai import AzureOpenAI  # pip install openai


class AzureOpenAIRepairAgent(ChatRepairAgent):
    def __init__(self, deployment: str, api_version: str = "2024-10-21") -> None:
        super().__init__(deployment)  # for Azure, "model" is the deployment name
        self._client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=api_version,
        )

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
```

### Anything via LiteLLM (Bedrock, Vertex, Gemini, Mistral, Cohere, …)

[LiteLLM](https://docs.litellm.ai) normalizes ~100 providers behind one call,
so a single agent covers most clouds — you just change the `model` string.

```python
import litellm  # pip install litellm


class LiteLLMRepairAgent(ChatRepairAgent):
    def complete(self, system: str, user: str) -> str:
        resp = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp["choices"][0]["message"]["content"] or ""


# model strings (set the provider's env/credentials as LiteLLM documents):
#   Amazon Bedrock : "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
#   Google Vertex  : "vertex_ai/gemini-1.5-pro"
#   Azure OpenAI   : "azure/<your-deployment>"
#   Mistral        : "mistral/mistral-large-latest"
agent = LiteLLMRepairAgent(model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0")
```

### Local / self-hosted (Ollama, vLLM, LM Studio — OpenAI-compatible)

Most local servers expose an OpenAI-compatible endpoint, so reuse the OpenAI
SDK with a `base_url`. This keeps the failing tool's source and traceback
entirely on your own hardware.

```python
from openai import OpenAI  # pip install openai


class LocalRepairAgent(ChatRepairAgent):
    def __init__(
        self,
        model: str = "qwen2.5-coder:7b",
        base_url: str = "http://localhost:11434/v1",  # Ollama default
    ) -> None:
        super().__init__(model)
        self._client = OpenAI(base_url=base_url, api_key="not-needed")

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
```

## Wiring it into the loop

Anywhere you'd use `LLMRepairAgent`, use your agent instead — the loop doesn't
care which:

```python
from kaula.audit_local import SqliteAuditSink
from kaula.sandbox_local import DockerSandbox
from kaula.self_healing import BasicStaticScanner, SelfHealingLoop

from myapp.repair_agents import OpenAIRepairAgent

loop = SelfHealingLoop(
    repair_agent=OpenAIRepairAgent(model="gpt-4o"),
    sandbox=DockerSandbox(),
    scanner=BasicStaticScanner(),
    audit=SqliteAuditSink("audit.db"),
)
```

### Selecting it by config (no code change in your agents)

Because `RepairAgent` is resolved through the registry (see the user guide,
UC‑7), you can name your provider agent in `kaula.toml` instead of
constructing it. The registry imports `module:Attr` and calls it with **no
arguments**, so the class must be constructible from the environment
(defaults + env vars) — the `OpenAIRepairAgent`/`LocalRepairAgent` above
already are:

```toml
[implementations]
repairagent = "myapp.repair_agents:OpenAIRepairAgent"
sandbox     = "kaula.sandbox_local:DockerSandbox"
auditsink   = "kaula.audit_local:SqliteAuditSink"
scanner     = "kaula.self_healing:BasicStaticScanner"
```

```python
from kaula.core import Registry
from kaula.self_healing import SelfHealingLoop

registry = Registry(discover_installed=True)
registry.load_config("kaula.toml")
loop = SelfHealingLoop.from_registry(registry)
```

If your agent needs constructor arguments (a specific model or deployment),
either give them defaults, wrap it in a zero-arg factory function and point
config at that, or just inject the instance directly with
`registry.register(RepairAgent, MyAgent(model="…"))`.

## Privacy reminder

The repair prompt necessarily contains the failing tool's source and its
traceback (argument *values* appear only inside the traceback text Python
produced). Point your agent at an endpoint your data policy allows — a local
model keeps all of it on your own hardware. Kaula's audit chain itself stays
by-reference (fingerprints and hashes, never raw values), independent of which
provider you choose.
