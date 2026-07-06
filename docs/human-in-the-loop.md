# Human-in-the-loop review

By default Kaula is autonomous: a failure is detected, a fix is generated,
verified (tests + security scan), gated by policy, and hot-swapped — all
without a human. You can insert a person at **either or both** of the two
decision points:

```
 DETECT ──▶ [review_failure?] ──▶ DIAGNOSE ▶ SANDBOX ▶ TEST ▶ SCAN ▶ GATE ──▶ [review_candidate?] ──▶ HOT-SWAP
             ▲ gate #1                                                          ▲ gate #2
     "should we try to auto-repair this at all?"          "this fix is verified — may it go live?"
```

- **Gate #1 — after detection** (`review_failure`): before Kaula spends any
  effort or calls the model. Reject → nothing is attempted; the tool is left
  broken and the run pauses. Use it to keep certain tools off autopilot, or to
  let an operator triage first.
- **Gate #2 — after auto-repair** (`review_candidate`): the candidate has
  **already passed** the sandbox tests, the security scan, and the automated
  policy gate — a human just gives the final go/no-go before it swaps live.
  Reject → nothing ships; the run pauses. This is the classic "verify
  automatically, approve manually" autonomy tier.

Both gates are **off by default** (fully autonomous). Turn on one, both, or
neither. Rejection is always the safe state: the tool is unchanged, the run
pauses, and the decision is written to the audit trail.

> Both gates only ever *withhold* a change; they can't force an unverified one.
> Gate #2 runs **after** the automated checks, so a human is never asked to
> approve a candidate that failed its tests or scan.

## Turning the gates on

A reviewer is any callable returning an `Approval`:

| Gate | Signature |
|---|---|
| `review_failure` | `(ToolFailure, ToolVersion) -> Approval` |
| `review_candidate` | `(RepairCandidate, SandboxResult, ScanResult) -> Approval` |

`Approval.approve(reason="")` proceeds; `Approval.reject(reason="")` pauses.
Pass them to the loop:

```python
from kaula.self_healing import Approval, SelfHealingLoop

def gate_failures(failure, current) -> Approval:
    # e.g. never auto-repair anything touching payments
    if failure.tool_name.startswith("payment_"):
        return Approval.reject("payment tools require manual repair")
    return Approval.approve()

def gate_candidates(candidate, sandbox_result, scan_result) -> Approval:
    # require a human only for larger rewrites; auto-ship small ones
    if len(candidate.source.splitlines()) > 40:
        return Approval.reject("large rewrite — needs review")
    return Approval.approve("small verified change")

loop = SelfHealingLoop(
    repair_agent=...,
    sandbox=...,
    scanner=...,
    audit=...,
    review_failure=gate_failures,      # gate #1 (omit to skip)
    review_candidate=gate_candidates,  # gate #2 (omit to skip)
)
```

### Built-in reviewers

```python
from kaula.self_healing import always_approve, always_reject, ConsoleReviewer
```

- `always_approve` / `always_reject` — match either signature; handy for
  tests or a global "manual only" switch (`review_candidate=always_reject`
  makes every verified fix wait for a human).
- `ConsoleReviewer` — interactive: prints the failure / the verified fix and
  its source, and reads `y/N` from stdin. Pass its bound methods:

  ```python
  from kaula.self_healing import ConsoleReviewer

  reviewer = ConsoleReviewer()
  loop = SelfHealingLoop(
      ...,
      review_failure=reviewer.review_failure,
      review_candidate=reviewer.review_candidate,
  )
  ```

  `input_fn` / `output_fn` are injectable, so you can wire it to any prompt
  (a TUI, a test) instead of stdin/stdout.

## Interactive vs. asynchronous approval

**Interactive (blocking).** The reviewer decides right now — a CLI prompt, a
notebook, a synchronous approval call. `ConsoleReviewer` is the reference.
Simple, but it blocks the failing call while it waits.

**Asynchronous (approve later).** In a headless service you don't want to
block on a human. Reject at the gate so the run enters the durable paused
state, and let a person handle it out of band via the pause ledger, then
resume:

```python
from kaula.runtime import SqlitePauseLedger
from kaula.self_healing import Approval

ledger = SqlitePauseLedger("pauses.db", audit=audit)

def gate_candidates(candidate, sandbox_result, scan_result) -> Approval:
    # don't block the request thread — pause and let a human decide later
    return Approval.reject("queued for human approval")

# ... wire review_candidate=gate_candidates and pause_ledger=ledger on the
# HealingToolWrapper (see the user guide, UC-6). The paused run is now a row
# in the ledger with the candidate's diagnosis + source_hash in the audit
# trail; an operator reviews it and drives resume_paused_run(...).
```

This reuses the pause/resume machinery from the user guide (UC‑6): the reject
lands as a durable pause record (mirrored to the audit trail), and
`resume_paused_run` re-runs once the human is ready. See
[docs/user-guide.md](user-guide.md) UC‑6.

## How it composes with the policy gate

`review_candidate` is **not** a replacement for the `PolicyEngine` — they
stack:

1. `PolicyEngine.authorize_swap(...)` — the *automated* green/red gate
   (tests + scan must pass; the open default is all-autonomous-if-green). A
   policy denial is terminal.
2. `review_candidate(...)` — the *human* approval, consulted only **after**
   the policy gate allows.

So a candidate must be green **and** policy-approved **and** human-approved to
ship. Use a custom `PolicyEngine` (user guide, UC‑8) for automatable rules
("≥ 3 tests", "business hours only") and `review_candidate` for the human
sign-off.

## What lands in the audit trail

Every review — approve or reject — is recorded, so the decision and its reason
are part of the hash-chained history:

| Event | When | Payload |
|---|---|---|
| `failure_review` | gate #1 consulted | `approved`, `reason`, `tool_name`, `failure_id` |
| `candidate_review` | gate #2 consulted | `approved`, `reason`, `tool_name`, `candidate_id` |

On a rejection the trail ends with the usual `healing_failed` (tool unchanged,
run paused), so you can always see *who/what* stopped a change and why. A
rejected candidate that was fully verified first shows the complete
`repair_proposed → sandbox_result → scan_result → gate_decision →
candidate_review → healing_failed` sequence — proof the fix was vetted before
the human declined it.

## Privacy note

`review_candidate` hands the reviewer the proposed source and the diagnosis;
`review_failure` hands them the failure (including its traceback). Keep that in
mind if your reviewer forwards the payload somewhere (a chat message, a ticket)
— the same by-reference discipline the audit chain uses (fingerprints, hashes)
is worth applying to whatever your reviewer sends onward.
