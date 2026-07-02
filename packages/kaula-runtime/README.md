# kaula-runtime

The CrewAI adapter: intercepts tool calls, captures full failure context,
triggers the self-healing loop, and pauses the run on unrecoverable failure
so no partial results commit.

**Adapter, not foundation.** This is the only open package allowed to import
an orchestration framework, and it is shaped as the first of a possible
family (`kaula-runtime-langgraph` would be a sibling against the same
`kaula.core` contracts). Everything framework-specific lives here; the loop
itself never sees a CrewAI type. CrewAI is wrapped through public extension
points only — never vendored, never patched.

- `kaula.runtime.HealingToolWrapper` — the interception hook around any
  callable tool: on exception it captures a `ToolFailure`, runs the loop,
  hot-swaps the verified fix in-process, and retries the call once. If
  healing fails, it raises `ToolHealingPausedError` (the safe state: run
  paused, tool unchanged).
- `kaula.runtime.crewai_adapter.heal_crewai_tool` — wraps a CrewAI
  `BaseTool` in that hook by composition.

Maturity: `[MVP]`.
