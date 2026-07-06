# kaula-runtime-langgraph

The **LangGraph** runtime adapter for [Kaula](../../README.md) — the
CrewAI-free sibling of `kaula-runtime`. It wraps a LangChain/LangGraph tool so
that when the tool fails at runtime, Kaula captures the failure, has a repair
agent rewrite it, verifies the candidate in a sandbox (tests + security scan),
and hot-swaps it live — **only if it passes** — all recorded in the
hash-chained audit trail and reversible in one step.

**Adapter, not foundation.** This is a second `kaula-runtime*` adapter against
the same `kaula.core` contracts and the same framework-free loop; only
adapter packages may import an orchestration framework (CI enforces it).
LangChain is wrapped by composition through public extension points — never
vendored or patched.

## Install

```bash
pip install kaula-runtime-langgraph kaula-sandbox-local kaula-audit-local
# repair agent: pip install "kaula-self-healing[llm]"  (or bring your own — see docs/llm-providers.md)
```

## Use

```python
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from kaula.audit_local import SqliteAuditSink
from kaula.core import ToolTest
from kaula.runtime_langgraph import heal_langgraph_tool
from kaula.sandbox_local import DockerSandbox
from kaula.self_healing import BasicStaticScanner, LLMRepairAgent, SelfHealingLoop


@tool
def parse_price(text: str) -> float:
    """Parse a price string into a float."""
    return float(text)              # v1 dies on "$1,234.56"


TESTS = (
    ToolTest(name="plain",    args=("1234.56",),   expected=1234.56, check="equals"),
    ToolTest(name="grouped",  args=("1,234.56",),  expected=1234.56, check="equals"),
    ToolTest(name="currency", args=("$1,234.56",), expected=1234.56, check="equals"),
)

loop = SelfHealingLoop(
    repair_agent=LLMRepairAgent(),
    sandbox=DockerSandbox(),
    scanner=BasicStaticScanner(),
    audit=SqliteAuditSink("audit.db"),
)

healing_price = heal_langgraph_tool(parse_price, loop=loop, tests=TESTS)

# Drop it into any LangGraph agent / ToolNode — same tool interface:
agent = create_react_agent(model, tools=[healing_price])
```

Healthy calls pass straight through. A failing call heals in place and the
original call is retried once against the fixed tool. If healing can't produce
a verified fix within budget, the tool is left unchanged and the run pauses
(safe state).

## Native pause & resume (LangGraph interrupts)

Because LangGraph has first-class checkpointing and interrupts, Kaula's pause
becomes a **real** durable checkpoint rather than the re-kickoff the CrewAI
adapter must use. Pass `pause_via_interrupt=True` and run under a checkpointer:

```python
healing_price = heal_langgraph_tool(
    parse_price, loop=loop, tests=TESTS, pause_via_interrupt=True,
)
# ... build the graph with a checkpointer (e.g. MemorySaver / SqliteSaver).
# On an unhealed failure the graph interrupts with a payload like
#   {"kaula": "healing_paused", "tool_name": ..., "reason": ...,
#    "pause_record_id": ...}
# A human fixes the tool out of band, then resumes:
#   graph.invoke(Command(resume="fixed"), config)   # the tool node re-runs
```

Pair it with `pause_ledger=SqlitePauseLedger(...)` for a durable queue of
paused runs (see the user guide, UC‑6).

For class-based tools (a `BaseTool` subclass rather than an `@tool` function),
pass `source=` and `entrypoint=` so the repair agent has real source to
rewrite.

Maturity: `[MVP]`.
