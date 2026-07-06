# kaula-runtime

The CrewAI adapter, plus the framework-free interception hook every adapter
builds on: intercepts tool calls, captures full failure context, triggers the
self-healing loop, and pauses the run on unrecoverable failure so no partial
results commit.

**The base install is framework-free.** `HealingToolWrapper` and
`SqlitePauseLedger` need no orchestration framework — they heal any plain
callable. **CrewAI is the optional `crewai` extra**, needed only for
`kaula.runtime.crewai_adapter`:

```bash
pip install kaula-runtime            # HealingToolWrapper + pause ledger only
pip install "kaula-runtime[crewai]"  # + the CrewAI adapter
```

**Adapter, not foundation.** `kaula-runtime*` is a family: the CrewAI adapter
lives here and the **LangGraph** adapter is its CrewAI-free sibling
([`kaula-runtime-langgraph`](../kaula-runtime-langgraph/README.md)), against
the same `kaula.core` contracts and the same framework-free loop. Everything
framework-specific lives in an adapter; the loop never sees a framework type.
Frameworks are wrapped through public extension points only — never vendored,
never patched.

- `kaula.runtime.HealingToolWrapper` — the interception hook around any
  callable tool: on exception it captures a `ToolFailure`, runs the loop,
  hot-swaps the verified fix in-process, and retries the call once. If
  healing fails, it raises `ToolHealingPausedError` (the safe state: run
  paused, tool unchanged).
- `kaula.runtime.crewai_adapter.heal_crewai_tool` — wraps a CrewAI
  `BaseTool` in that hook by composition.
- `kaula.runtime.SqlitePauseLedger` — durable record of paused runs: one
  entry per unhealed failure (mirrored to the audit trail), resolved
  explicitly when a human ships a fix, rolls back, or abandons the run.
- `kaula.runtime.crewai_adapter.kickoff_with_healing` /
  `resume_paused_run` — run a Crew under pause-on-failure semantics and
  resume it after human intervention. Honest boundary: CrewAI exposes no
  mid-task state capture, so resume re-kickoffs the crew against the fixed
  tool — safe because the pause fired before any partial result committed.

Maturity: `[MVP]`.
