# kaula-self-healing

The reference implementation of the Kaula self-healing loop, against the
`kaula-core` contracts only: capture failure → propose repair → verify in a
sandbox (tests + security scan) → consult the policy gate → hot-swap →
audit → curate memory.

Deliberately framework-agnostic: no CrewAI, no concrete implementation
imports — every dependency is a `kaula.core` Protocol injected at
construction, so the loop is fully unit-testable with fakes.

Safety invariants (non-negotiable):

- A candidate ships **only** if it passes tests, scan, and the policy gate.
- A failed repair is a safe state: the tool stays broken, the run stays
  paused, a human is notified. The gate is never weakened to make something pass.
- Every step is written to the audit sink; argument values are fingerprinted,
  never stored raw.
- Only verified successes are curated into memory.

Maturity: `[MVP]`.
