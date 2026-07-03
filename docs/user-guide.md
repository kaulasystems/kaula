<p align="center">
  <img src="assets/kaula-logo.svg" alt="Kaula" width="96">
</p>

# Kaula User Guide

Kaula is self-healing infrastructure for AI agents. When one of your agent's
tools crashes at runtime, Kaula captures the failure, has a repair agent
rewrite the tool, verifies the candidate in an isolated sandbox (tests + a
security scan), and hot-swaps it live — **only if it passes**. Every step is
written to a hash-chained, append-only audit trail, and any swap can be
reverted in a single logged action.

The guiding principle everywhere: **easy to change, impossible to change
invisibly.**

This guide covers installation, the mental model, a component reference, and
a set of ready-to-use recipes you can copy into your project.

---

## Table of contents

1. [Installation](#1-installation)
2. [Five-minute quickstart](#2-five-minute-quickstart)
3. [How healing works](#3-how-healing-works)
4. [Core concepts](#4-core-concepts)
5. [Component reference](#5-component-reference)
6. [Ready-to-use use cases](#6-ready-to-use-use-cases)
   - [UC-1 · Self-heal a broken parsing tool](#uc-1--self-heal-a-broken-parsing-tool)
   - [UC-2 · Make a CrewAI tool self-healing](#uc-2--make-a-crewai-tool-self-healing)
   - [UC-3 · Survive an upstream API format change](#uc-3--survive-an-upstream-api-format-change)
   - [UC-4 · Heal a data-cleaning tool in an ETL step](#uc-4--heal-a-data-cleaning-tool-in-an-etl-step)
   - [UC-5 · Inspect, verify, and roll back (the ops runbook)](#uc-5--inspect-verify-and-roll-back-the-ops-runbook)
   - [UC-6 · Human-in-the-loop: durable pause and resume](#uc-6--human-in-the-loop-durable-pause-and-resume)
   - [UC-7 · Assemble everything from configuration](#uc-7--assemble-everything-from-configuration)
   - [UC-8 · Enforce your own swap policy](#uc-8--enforce-your-own-swap-policy)
   - [UC-9 · Route healing alerts to your team](#uc-9--route-healing-alerts-to-your-team)
7. [Operational requirements & limits](#7-operational-requirements--limits)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Installation

Kaula ships as independent packages sharing the `kaula.*` namespace. Install
what you need:

```bash
# the verified-healing core: loop + sandbox + audit
pip install kaula-self-healing kaula-sandbox-local kaula-audit-local

# the CrewAI adapter (pulls in crewai)
pip install kaula-runtime

# the Claude-backed repair agent (optional extra)
pip install "kaula-self-healing[llm]"
```

> Until the packages are on PyPI, install from a checkout: `make install`
> (uses [uv](https://docs.astral.sh/uv/)) gives you the whole open tier in
> editable mode.

Requirements:

- **Python ≥ 3.11**
- **A local Docker daemon** for the reference sandbox (`DockerSandbox`
  runs candidates in `python:3.11-slim`; run `docker pull python:3.11-slim`
  once so the first heal isn't slowed by an image pull)
- **`ANTHROPIC_API_KEY`** (or another credential source the `anthropic` SDK
  resolves) if you use `LLMRepairAgent`

---

## 2. Five-minute quickstart

The fastest way to see the whole loop is the bundled demo — it breaks a
price parser, heals it live, prints the audit trail, and rolls the fix back:

```bash
make demo-healing           # scripted repair agent, fully offline
ANTHROPIC_API_KEY=... make demo-healing   # live LLM repair
```

The same flow in your own code:

```python
from kaula.audit_local import SqliteAuditSink, ToolVersionStore
from kaula.core import ToolTest
from kaula.runtime import HealingToolWrapper
from kaula.sandbox_local import DockerSandbox
from kaula.self_healing import BasicStaticScanner, LLMRepairAgent, SelfHealingLoop

# 1. A tool that works today — until someone passes "$1,234.56"
def parse_price(text: str) -> float:
    return float(text)

# 2. What "correct" means for this tool. Candidates ship only if every
#    test passes inside the sandbox.
TESTS = (
    ToolTest(name="plain",    args=("1234.56",),   expected=1234.56, check="equals"),
    ToolTest(name="grouped",  args=("1,234.56",),  expected=1234.56, check="equals"),
    ToolTest(name="currency", args=("$1,234.56",), expected=1234.56, check="equals"),
)

# 3. Wire the loop: repair → sandbox-verify → scan → gate → swap → audit
audit = SqliteAuditSink("kaula-audit.db")
store = ToolVersionStore("kaula-versions.db", audit=audit)
loop = SelfHealingLoop(
    repair_agent=LLMRepairAgent(),          # Claude proposes the fix
    sandbox=DockerSandbox(),                # ...verified in isolation
    scanner=BasicStaticScanner(),           # ...and statically scanned
    audit=audit,
)

# 4. Wrap the tool. Healthy calls pass straight through.
price = HealingToolWrapper(
    tool_name="parse_price",
    func=parse_price,
    loop=loop,
    tests=TESTS,
    on_swap=store.register,                 # every new version is version-addressable
)
store.register(price.version)

print(price("$1,234.56"))   # crashes v1 → heals → returns 1234.56
assert audit.verify()       # the chain proves nothing changed invisibly
```

---

## 3. How healing works

Every healing attempt walks an explicit, ordered state machine — no phase
can be skipped, and the only exits are a verified swap or an explicit
failure:

```
 DETECT → DIAGNOSE → SANDBOX → TEST → SCAN → GATE → HOT-SWAP → RECORD → PERSIST → RESUME
              ▲          │        │      │      │
              └──────────┴────────┴──────┘      │   bounded retry (max_attempts)
                                                │
                                             FAILED  → tool unchanged, run paused,
                                                        human notified
```

| Phase | What happens |
|---|---|
| DETECT | The wrapper catches the tool's exception and captures a `ToolFailure` (error, traceback, fingerprint of the arguments — never the raw values). |
| DIAGNOSE | The `RepairAgent` proposes a `RepairCandidate`: a full replacement source for the tool. |
| SANDBOX / TEST | The candidate runs your `ToolTest` suite inside an isolated container — no network, no environment variables, resource-limited. |
| SCAN | Static security scan of the candidate source (deny-list of dangerous imports/calls). |
| GATE | The `PolicyEngine` decides whether the verified candidate may go live. The open default is *all-autonomous-if-green*. |
| HOT-SWAP | The live callable is replaced by the new version — in-process, no restart. |
| RECORD / PERSIST | The swap is written to the audit chain; a curated success record can go to memory. |
| RESUME | The original call is retried once against the healed tool and its result returned to your agent. |

**A failed repair is a safe state.** If no candidate passes within the
attempt budget, the tool is left broken, the run stays paused, and a human
is notified. Kaula never ships an unverified fix and never weakens the gate
to make something pass.

---

## 4. Core concepts

**ToolVersion** — one immutable revision of a tool's source, numbered from
1, each new version pointing at its parent. Healing always produces a child
of the current version, which is what makes one-step rollback possible.

**ToolTest** — one verification case: positional/keyword arguments, an
optional expected value, and a check mode (`"equals"` or `"no_exception"`).
Arguments must be JSON-serialisable — they cross the sandbox boundary.
**Your tests are the definition of correct.** A candidate that passes a weak
suite ships; invest here first.

**Audit chain** — every event's hash covers its content *and* the previous
event's hash. `verify()` recomputes the whole chain, so any edit, deletion,
or reordering of history is detectable. Payloads are PII-safe by design:
argument values are fingerprinted (SHA-256), sources are referenced by hash.

**Rollback index** — `ToolVersionStore` keeps every registered version and
an *active* pointer. `rollback(tool)` moves the pointer to the parent
version as a single, logged action.

**Pause ledger** — when healing fails, the paused run becomes a durable
record (not just a raised exception): who paused, why, and whether a human
has resolved it yet.

**Registry** — the swap mechanism. Implementations resolve in order:
explicit injection → config file → installed packages (entry points) →
default. Upgrading any component — including to a commercial implementation
— is an install plus a config line, never a code change in your agents.

---

## 5. Component reference

### `kaula.self_healing.SelfHealingLoop`

```python
SelfHealingLoop(
    *, repair_agent, sandbox, scanner, audit,
    policy=None,            # default: PermissivePolicyEngine (autonomous if green)
    memory=None,            # optional curated MemoryStore
    max_attempts=3,         # repair attempts before giving up
    sandbox_timeout_s=30.0, # hard timeout per sandbox run
    notify=None,            # callable(str) invoked when healing fails
)
```

`SelfHealingLoop.from_registry(registry, ...)` builds the same object
through the registry instead of explicit arguments (see UC-7).

### `kaula.self_healing.LLMRepairAgent`

Claude-backed reference repair agent (`pip install "kaula-self-healing[llm]"`).

```python
LLMRepairAgent(model="claude-opus-4-8", client=None, max_tokens=16000)
```

- Sends the failing tool's source, error, traceback, and any previously
  failed candidates to the model; expects a diagnosis plus one complete
  replacement source.
- API errors, model refusals, and unusable replies all resolve to *no
  candidate* (the loop's safe state); the cause is on `agent.last_error`.
- The loop never trusts it: every proposal still passes sandbox + scan +
  gate.
- Privacy: the repair prompt necessarily contains the traceback and tool
  source. Point it at an endpoint your data policy allows.

### `kaula.self_healing.BasicStaticScanner`

AST-based deny-list scan. Blocks by default: imports of `subprocess`,
`socket`, `ctypes`, `pty`, `pickle`, `marshal`; calls to `eval`, `exec`,
`compile`, `__import__`, `os.system`, `os.popen`. Findings of severity
`high`/`critical` fail the scan. Both lists are constructor-overridable.

### `kaula.sandbox_local.DockerSandbox`

```python
DockerSandbox(image="python:3.11-slim", memory="256m", cpus="0.5", pids_limit=64)
```

Runs the candidate + tests in a container with `--network none`, a
read-only filesystem, no inherited environment (no ambient credentials),
and resource limits. **Honest boundary:** single-tenant, not
escape-hardened — it contains accidents, not adversaries.

### `kaula.audit_local.SqliteAuditSink` / `ToolVersionStore`

```python
sink = SqliteAuditSink("audit.db")        # or ":memory:"
sink.append(event_type, payload)          # append-only; no update/delete API
sink.events(since_sequence=0)             # iterate the trail
sink.verify()                             # recompute the whole hash chain

store = ToolVersionStore("versions.db", audit=sink)
store.register(version)                   # logged; becomes active
store.current("tool"); store.history("tool"); store.get("tool", 2)
store.rollback("tool")                    # one call, one audit event
```

### `kaula.runtime.HealingToolWrapper`

The interception hook around any Python callable (see quickstart). On an
unhealed failure it raises `ToolHealingPausedError` — deliberately not
swallowed anywhere — optionally recording a durable pause first
(`pause_ledger=`).

### `kaula.runtime.crewai_adapter`

- `heal_crewai_tool(tool, *, loop, tests, ...)` — drop-in replacement for a
  CrewAI `BaseTool` whose failures self-heal (UC-2).
- `kickoff_with_healing(crew, *, inputs, ledger)` /
  `resume_paused_run(crew, record, *, ledger, inputs)` — run a Crew under
  pause-on-failure semantics and resume it after a human intervenes (UC-6).

---

## 6. Ready-to-use use cases

Each recipe is self-contained: copy it, adjust the tool and tests, run it.
They all assume the imports from the quickstart and a running Docker daemon
unless stated otherwise.

### UC-1 · Self-heal a broken parsing tool

**Scenario:** a utility function works in production until real-world input
(localised numbers, stray currency symbols) crashes it. You want it fixed
live, with proof.

```python
from kaula.audit_local import SqliteAuditSink, ToolVersionStore
from kaula.core import ToolTest
from kaula.runtime import HealingToolWrapper
from kaula.sandbox_local import DockerSandbox
from kaula.self_healing import BasicStaticScanner, LLMRepairAgent, SelfHealingLoop


def parse_quantity(text: str) -> int:
    """Naive v1: dies on '1 200 pcs' and '1,200'."""
    return int(text)


TESTS = (
    ToolTest(name="plain",   args=("1200",),      expected=1200, check="equals"),
    ToolTest(name="grouped", args=("1,200",),     expected=1200, check="equals"),
    ToolTest(name="spaced",  args=("1 200 pcs",), expected=1200, check="equals"),
)

audit = SqliteAuditSink("audit.db")
store = ToolVersionStore("versions.db", audit=audit)
loop = SelfHealingLoop(
    repair_agent=LLMRepairAgent(),
    sandbox=DockerSandbox(),
    scanner=BasicStaticScanner(),
    audit=audit,
    max_attempts=3,
)

quantity = HealingToolWrapper(
    tool_name="parse_quantity",
    func=parse_quantity,
    loop=loop,
    tests=TESTS,
    on_swap=store.register,
)
store.register(quantity.version)

print(quantity("1 200 pcs"))                    # heals on first crash → 1200
print(f"now at v{quantity.version.version}, chain ok: {audit.verify()}")
```

**What you get:** the call that would have crashed returns the right
answer; `audit.db` holds the full evidence trail; `versions.db` can revert
the swap in one call.

### UC-2 · Make a CrewAI tool self-healing

**Scenario:** your CrewAI agents depend on a tool that sometimes breaks on
inputs the LLM generates. Instead of the whole crew run dying, the tool
heals in place and the run continues.

```python
from crewai import Agent, Crew, Task
from crewai.tools import tool

from kaula.audit_local import SqliteAuditSink
from kaula.core import ToolTest
from kaula.runtime.crewai_adapter import heal_crewai_tool
from kaula.sandbox_local import DockerSandbox
from kaula.self_healing import BasicStaticScanner, LLMRepairAgent, SelfHealingLoop


@tool("normalize_sku")
def normalize_sku(raw: str) -> str:
    """Normalize a SKU like 'ab 123' to 'AB-123'."""
    prefix, number = raw.split(" ")          # v1 assumption: always one space
    return f"{prefix.upper()}-{number}"


TESTS = (
    ToolTest(name="spaced", args=("ab 123",),  expected="AB-123", check="equals"),
    ToolTest(name="dashed", args=("ab-123",),  expected="AB-123", check="equals"),
    ToolTest(name="tight",  args=("AB123",),   expected="AB-123", check="equals"),
)

loop = SelfHealingLoop(
    repair_agent=LLMRepairAgent(),
    sandbox=DockerSandbox(),
    scanner=BasicStaticScanner(),
    audit=SqliteAuditSink("audit.db"),
)

# Drop-in replacement: same name/description/schema, but failures self-heal.
healing_sku_tool = heal_crewai_tool(normalize_sku, loop=loop, tests=TESTS)

catalog_agent = Agent(
    role="Catalog normalizer",
    goal="Normalize product SKUs from supplier feeds",
    backstory="Meticulous about identifier formats.",
    tools=[healing_sku_tool],
)
task = Task(
    description="Normalize these SKUs: ab 123, cd-456, EF789",
    expected_output="A comma-separated list of normalized SKUs",
    agent=catalog_agent,
)
result = Crew(agents=[catalog_agent], tasks=[task]).kickoff()
print(result)

# Inspect the live tool version at any time:
print(healing_sku_tool._kaula_wrapper.version.version)
```

For class-based tools (subclassing `BaseTool` directly), pass the source
explicitly so the repair agent has something real to rewrite:

```python
healed = heal_crewai_tool(my_tool, loop=loop, tests=TESTS,
                          source=MY_TOOL_SOURCE, entrypoint="run_lookup")
```

### UC-3 · Survive an upstream API format change

**Scenario:** a vendor renames a JSON field overnight (`"temp"` →
`"temperature_c"`). Your extractor starts throwing `KeyError` on every
call. Encode *both* shapes in the tests and the healed tool will handle
whichever arrives.

```python
def extract_temperature(payload: dict) -> float:
    return payload["temp"]                   # v1: only knows the old shape


TESTS = (
    ToolTest(
        name="legacy_shape",
        args=({"temp": 21.5, "city": "Lugano"},),
        expected=21.5, check="equals",
    ),
    ToolTest(
        name="new_shape",
        args=({"temperature_c": 21.5, "city": "Lugano"},),
        expected=21.5, check="equals",
    ),
    ToolTest(
        name="both_prefer_new",
        args=({"temp": 20.0, "temperature_c": 21.5},),
        expected=21.5, check="equals",
    ),
)

weather = HealingToolWrapper(
    tool_name="extract_temperature",
    func=extract_temperature,
    loop=loop,                               # assembled as in UC-1
    tests=TESTS,
)

# First call with the new payload shape crashes v1, heals, and answers:
print(weather({"temperature_c": 21.5, "city": "Lugano"}))   # → 21.5
```

**Pattern to reuse:** when an interface drifts, *add a test for the new
shape while keeping the tests for the old one*. The gate guarantees the fix
is backward-compatible, because a candidate that breaks the legacy test
never ships.

### UC-4 · Heal a data-cleaning tool in an ETL step

**Scenario:** a nightly job normalises supplier rows. A new supplier sends
European decimal commas and empty cells; the cleaner crashes at 2 a.m. With
healing wired in, the job fixes its own cleaner — and if it can't, it stops
safely instead of loading garbage.

```python
def clean_row(row: dict) -> dict:
    """v1: assumes US decimals and no blanks."""
    return {
        "sku": row["sku"].strip().upper(),
        "price": float(row["price"]),
        "qty": int(row["qty"]),
    }


TESTS = (
    ToolTest(
        name="us_format",
        args=({"sku": " ab-1 ", "price": "10.50", "qty": "3"},),
        expected={"sku": "AB-1", "price": 10.5, "qty": 3}, check="equals",
    ),
    ToolTest(
        name="eu_decimal_comma",
        args=({"sku": "ab-2", "price": "10,50", "qty": "3"},),
        expected={"sku": "AB-2", "price": 10.5, "qty": 3}, check="equals",
    ),
    ToolTest(
        name="blank_qty_defaults_zero",
        args=({"sku": "ab-3", "price": "1.00", "qty": ""},),
        expected={"sku": "AB-3", "price": 1.0, "qty": 0}, check="equals",
    ),
)

cleaner = HealingToolWrapper(tool_name="clean_row", func=clean_row,
                             loop=loop, tests=TESTS)

loaded, failed = [], []
for row in supplier_rows:
    try:
        loaded.append(cleaner(row))
    except ToolHealingPausedError as pause:
        # Healing failed within budget: stop the load, keep the evidence.
        failed.append((row["sku"], pause.reason))
        break                                 # don't ship a partial batch silently
```

Because the sandbox refuses to verify blind (`no tests provided`) and the
gate refuses anything not green, the worst case is a *stopped* pipeline with
a paused-run record — never a silently corrupted load.

### UC-5 · Inspect, verify, and roll back (the ops runbook)

**Scenario:** it's the morning after; you want to see what healed overnight,
prove the trail is intact, and revert one swap you don't like.

```python
from kaula.audit_local import SqliteAuditSink, ToolVersionStore

audit = SqliteAuditSink("audit.db")
store = ToolVersionStore("versions.db", audit=audit)

# 1. What happened? (the healing timeline)
for event in audit.events():
    print(f"#{event.sequence:<4} {event.recorded_at}  {event.event_type:<24} "
          f"{event.payload.get('tool_name', '')}")

# 2. Is the record trustworthy? False means the file was tampered with.
assert audit.verify(), "AUDIT CHAIN BROKEN — investigate before trusting anything"

# 3. What versions exist, and what changed?
for version in store.history("parse_quantity"):
    print(f"v{version.version}  parent={version.parent_version}  "
          f"hash={version.source_hash[:12]}")
print(store.current("parse_quantity").source)      # read the live source

# 4. Don't like v3? One call, one logged audit event:
reverted = store.rollback("parse_quantity")
print(f"active again: v{reverted.version}")
```

Useful event types to filter on: `failure_detected`, `repair_proposed`,
`sandbox_result`, `scan_result`, `gate_decision`, `hot_swap`,
`healing_succeeded`, `healing_failed`, `rollback`, `run_paused`,
`pause_resolved`.

> Rolling back updates the *store*; a long-lived in-process wrapper keeps
> its current callable until you swap it too:
> `wrapper._swap(store.current("parse_quantity"))` — or simply restart the
> process and initialise the wrapper from `store.current(...)`.

### UC-6 · Human-in-the-loop: durable pause and resume

**Scenario:** healing sometimes *should* fail — the bug needs a human. You
want those runs to queue up durably, get fixed deliberately, and resume,
instead of living only in a stack trace someone saw at 2 a.m.

```python
from kaula.runtime import SqlitePauseLedger
from kaula.runtime.crewai_adapter import kickoff_with_healing, resume_paused_run

ledger = SqlitePauseLedger("pauses.db", audit=audit)   # pauses are audited too

# Wrap tools with the ledger attached so pauses are recorded at the source:
wrapped = HealingToolWrapper(..., loop=loop, tests=TESTS,
                             pause_ledger=ledger, run_id="nightly-2026-07-02")

# Run the crew under pause-on-failure semantics:
outcome = kickoff_with_healing(crew, inputs={"feed": "supplier_a"}, ledger=ledger)
if outcome.completed:
    print(outcome.output)
else:
    print(f"paused: {outcome.pause.reason} (record {outcome.pause.record_id})")

# ----- later: the morning triage -----
for record in ledger.pending():
    print(record.record_id, record.tool_name, record.reason, record.created_at)

# A human ships a fix out of band — e.g. registers a corrected version and
# swaps it in, or rolls back — then resumes:
resumed = resume_paused_run(crew, outcome.pause, ledger=ledger,
                            inputs={"feed": "supplier_a"})
assert resumed.completed
assert ledger.pending() == []
```

Honest boundary: CrewAI exposes no mid-task state capture, so *resume*
re-kickoffs the crew against the fixed tool. That is safe by construction —
the pause fired before any partial result committed.

### UC-7 · Assemble everything from configuration

**Scenario:** you don't want construction code choosing implementations —
ops does, per environment. Installing a different implementation (including
a commercial one, when you have it) plus one config line is the entire
upgrade; agent code never changes.

`kaula.toml`:

```toml
[implementations]
# interface name (case-insensitive) -> "module:Class"
sandbox     = "kaula.sandbox_local:DockerSandbox"
auditsink   = "kaula.audit_local:SqliteAuditSink"
scanner     = "kaula.self_healing:BasicStaticScanner"
repairagent = "kaula.self_healing:LLMRepairAgent"
```

```python
from kaula.core import Registry
from kaula.self_healing import SelfHealingLoop

registry = Registry(discover_installed=True)   # what installed packages provide
registry.load_config("kaula.toml")             # config outranks discovery

loop = SelfHealingLoop.from_registry(registry, max_attempts=3)
```

Resolution order: explicit `registry.register(...)` → config → installed
packages (entry points in group `kaula.implementations`) → defaults. If two
installed packages claim the same interface and config doesn't pick one,
resolution fails loudly rather than choosing silently.

Tests inject fakes the same way:

```python
registry.register(Sandbox, FakeSandbox())      # explicit injection always wins
```

### UC-8 · Enforce your own swap policy

**Scenario:** the permissive default (*ship anything green*) is right for a
single dev, wrong for your team. You want extra conditions — say, a minimum
test count and a curfew for autonomous swaps — without touching the loop.

Implement the `PolicyEngine` Protocol (duck-typed, nothing to subclass):

```python
from datetime import datetime, timezone

from kaula.core import PolicyDecision, RepairCandidate, SandboxResult, ScanResult


class TeamPolicyEngine:
    """Green is necessary but not sufficient."""

    def __init__(self, min_tests: int = 3, autonomous_hours: range = range(6, 22)):
        self._min_tests = min_tests
        self._hours = autonomous_hours

    def authorize_swap(
        self,
        candidate: RepairCandidate,
        sandbox_result: SandboxResult,
        scan_result: ScanResult,
    ) -> PolicyDecision:
        if not (sandbox_result.passed and scan_result.passed):
            return PolicyDecision(False, "verification not green")
        if sandbox_result.tests_run < self._min_tests:
            return PolicyDecision(
                False, f"only {sandbox_result.tests_run} tests ran; "
                       f"policy requires >= {self._min_tests}"
            )
        if datetime.now(timezone.utc).hour not in self._hours:
            return PolicyDecision(False, "outside autonomous-swap hours; needs approval")
        return PolicyDecision(True, "green + team policy satisfied")


loop = SelfHealingLoop(
    repair_agent=LLMRepairAgent(),
    sandbox=DockerSandbox(),
    scanner=BasicStaticScanner(),
    audit=audit,
    policy=TeamPolicyEngine(min_tests=3),
)
```

A policy denial is **terminal** for that healing attempt — the loop never
retries around the gate. The run pauses, the decision (and your reason
string) is in the audit trail, and a human takes it from there. The same
seam is where organisation-wide RBAC/approval engines plug in.

### UC-9 · Route healing alerts to your team

**Scenario:** when a run pauses, someone should hear about it — in Slack,
PagerDuty, email, wherever. The loop's `notify` hook is a plain callable;
the message already says which tool, why, and that the run is paused.

```python
import json
import urllib.request

SLACK_WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"

def notify_slack(message: str) -> None:
    body = json.dumps({"text": f":rotating_light: {message}"}).encode()
    request = urllib.request.Request(
        SLACK_WEBHOOK, data=body, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(request, timeout=10)

loop = SelfHealingLoop(
    repair_agent=LLMRepairAgent(),
    sandbox=DockerSandbox(),
    scanner=BasicStaticScanner(),
    audit=audit,
    notify=notify_slack,
)
```

Pair it with UC-6's pause ledger: the alert gets a human's attention, the
ledger gives them the queue to work through.

---

## 7. Operational requirements & limits

Know exactly where the open tier stops — these are design boundaries, not
bugs:

- **Tests define correctness.** The gate is only as strong as your
  `ToolTest` suite. Cover the inputs that matter, including the ones that
  triggered the failure.
- **Test arguments must be JSON-serialisable** (they are shipped into the
  sandbox). Unserialisable arguments are rejected before anything leaves
  your process.
- **Healed tools are stdlib-only.** The sandbox has no network and no
  package installation, so candidates that `import requests` can't be
  verified — and the repair agent is instructed not to produce them. Tools
  with heavy third-party dependencies aren't good healing targets yet.
- **The reference sandbox is single-tenant and not escape-hardened.** It
  contains accidental damage from a bad candidate, not a determined
  adversary in a hostile multi-tenant environment.
- **Healing is synchronous.** The failing call blocks while the loop runs
  (LLM call + one container run per attempt — typically tens of seconds).
  Budget for that in interactive paths, or let the pause ledger absorb it.
- **One process at a time.** There is no cross-worker coordination; if the
  same tool fails in many workers simultaneously, each heals independently.
- **The default policy is permissive single-user.** Anything green ships
  autonomously. Put your own `PolicyEngine` in front of it (UC-8) before
  giving this to a team.
- **LLM privacy:** the repair prompt contains the traceback and tool source
  (argument values only ever appear inside the traceback text Python
  produced). The audit chain itself stays by-reference: fingerprints and
  hashes, never raw values.

---

## 8. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `SandboxResult.infra_error: "docker executable ... not found"` | No Docker CLI on PATH. Install Docker or point `DockerSandbox(docker_executable=...)` at it. |
| `infra_error: "harness produced no result marker (exit code 125/127)"` | Docker daemon not running, or the image is missing — `docker pull python:3.11-slim`. |
| `infra_error: "sandbox timed out after 30.0s"` | First run often pays an image pull. Pre-pull the image, or raise `SelfHealingLoop(sandbox_timeout_s=...)`. |
| `ValueError: ToolTest args ... must be JSON-serialisable` | A test argument can't cross the sandbox boundary. Restructure the test to use plain dict/list/str/num values. |
| Healing always fails with `security scan failed` | The candidate needs something on the deny-list. Usually right! If your domain genuinely needs an item, override `BasicStaticScanner(banned_imports=..., banned_calls=...)` — deliberately, in code review. |
| `ImportError: LLMRepairAgent needs the optional LLM dependency` | `pip install "kaula-self-healing[llm]"`. |
| `LLMRepairAgent` returns no candidate; `last_error` says request failed | Check `ANTHROPIC_API_KEY` / network. The loop treats it as a safe failure: run paused, tool unchanged. |
| `ResolutionError: multiple installed implementations for Sandbox` | Two installed packages register the same interface. Pick one in `kaula.toml` `[implementations]`. |
| `ToolHealingPausedError` in my agent run | Working as designed: healing failed within budget. Check `exc.pause_record`, the audit trail's `healing_failed` event, and the pending pause queue (UC-6). |
| `audit.verify()` returns `False` | The audit database was modified outside the append-only API. Treat as an incident: stop trusting the trail, restore from backup, investigate access. |

---

*Maturity: `[MVP]`. Component boundaries and the open/commercial seam are
documented in `docs/kaula-oss-architecture.md`; contributor rules live in
`CLAUDE.md`.*
