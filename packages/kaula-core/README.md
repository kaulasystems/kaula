# kaula-core

The contracts package of [Kaula](../../README.md): domain types, the seam
`Protocol`s (`Sandbox`, `AuditSink`, `MemoryStore`, `MCPGateway`, `PolicyEngine`,
`Scanner`, `RepairAgent`), the self-healing loop state machine, and the
lightweight implementation registry.

- No heavy dependencies, no CrewAI — the loop contract is framework-agnostic
  and unit-testable without any runtime.
- Every pluggable capability is defined here as an interface and implemented
  elsewhere (reference impls in open packages, hardened impls commercially).
- This is the sensitive surface: changing a Protocol here moves the
  open/commercial seam and carries the strictest review bar in the repo.

```python
from kaula.core import Sandbox, ToolFailure, LoopStateMachine, HealingPhase
```

Maturity: `[MVP]`.
