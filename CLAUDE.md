# CLAUDE.md

Guidance for Claude Code (and any agent or contributor) working in the Kaula monorepo. Read this before writing code. It encodes the rules that keep the open-core seam real; violating them silently breaks the business model, not just the build.

---

## What Kaula is

Kaula is **self-healing agent infrastructure**, built on CrewAI. When an agent's tool fails at runtime, Kaula captures the failure, has a repair agent rewrite the tool, verifies the candidate in a sandbox (tests + security scan), and hot-swaps it live — **only if it passes**. Every change is written to an immutable, hash-chained audit trail and is reversible in one step.

Guiding principle, applied everywhere: **easy to change, impossible to change invisibly.**

The self-healing loop is the differentiator. Natural-language agent creation is the on-ramp (table stakes), not the product.

Authoritative design doc (read it for any non-trivial change):
- `docs/kaula-oss-architecture.md` — package layout and the open/commercial seam

---

## The one rule you must never break

**Dependencies point toward `kaula-core`. No open package ever imports a commercial package.**

- All shared interfaces (Protocols) and domain types live in `kaula-core`.
- `kaula-core` imports nothing from the packages that implement its interfaces — and never imports CrewAI.
- Open packages depend only on `kaula-core` interfaces and other open packages.
- Commercial packages may depend on open packages; the reverse is a **build-failing** error.
- Implementations are resolved at runtime by config, never by an open package importing a concrete commercial impl.

If a task seems to require an open package importing a commercial one, stop — the design is wrong or the code belongs in a different package. Surface it; do not work around it.

**Second rule: only `kaula-runtime*` adapter packages may import an orchestration framework** (CrewAI today; possibly LangGraph later). The loop (`kaula.core`, `kaula.self_healing`) stays framework-agnostic — that portability is a strategic asset, not a style preference. If loop code seems to need a CrewAI type, the framework-specific part belongs in the adapter.

---

## Package map

Two names per package: the **distribution** (hyphenated, what you `pip install` / publish) and the **import path** (underscored, under the shared `kaula.*` namespace).

Open (PyPI, Apache-2.0):
- `kaula-core` → `kaula.core` — Protocols, domain types, the loop state machine. No CrewAI. The sensitive surface; changes here move the seam.
- `kaula-self-healing` → `kaula.self_healing` — reference self-healing loop. Depends only on core interfaces.
- `kaula-runtime` → `kaula.runtime` — CrewAI **adapter** (the only non-adapter-exempt package that may import CrewAI); intercepts tool calls, triggers healing, pauses state on failure. Shaped as the first of a possible family (`kaula-runtime-langgraph` later).
- `kaula-sandbox-local` → `kaula.sandbox_local` — reference `Sandbox` (local Docker). Single-tenant, **not** escape-hardened — say so in docs.
- `kaula-audit-local` → `kaula.audit_local` — reference `AuditSink` + rollback (hash-chained, append-only).
- `kaula-memory-local` → `kaula.memory_local` — reference `MemoryStore` (ephemeral, curated writes only).
- `kaula-mcp` → `kaula.mcp` — basic MCP connect + local logging. No governance here.
- `kaula-planner` → `kaula.planner` — NL → Crew planner (the on-ramp).
- `kaula-cli` → `kaula.cli` — describe → review → run → inspect.
- `kaula-kit` → `kaula` (facade) — **thin** meta-package: bundles the open set and exposes `from kaula import Kaula`. Almost no logic; open-only; never references a commercial package. Don't put behaviour here.

Commercial (private index — **not** in this repo's published set):
`kaula-sandbox-hardened` → `kaula.sandbox_hardened`, `kaula-memory-cloud` → `kaula.memory_cloud`, `kaula-mcp-governed` → `kaula.mcp_governed`, `kaula-governance` → `kaula.governance`, `kaula-audit-cloud` → `kaula.audit_cloud`, `kaula-healing-network` → `kaula.healing_network`.

Each commercial package implements an interface defined in `kaula.core` and registers at runtime. Do not scaffold or reference these from open code.

### Namespace rules (do not violate)
`kaula` is a **PEP 420 implicit namespace package** shared across all distributions. Therefore:
- **Never create a top-level `kaula/__init__.py`** in any package. The namespace root must stay an implicit namespace (a bare directory). An `__init__.py` there shadows every sibling distribution.
- Each package ships exactly one subpackage: `src/kaula/<its_subpackage>/`.
- Distribution names are hyphenated; import paths are underscored. Keep them paired.
- A missing subpackage is an honest `ImportError` (e.g. `from kaula import sandbox_hardened` in an open-only install). Do not paper over it with shims.
- **Sharing the `kaula` import root does NOT relax the dependency rule below.** Namespace ≠ dependency.

---

## Where things go (decision guide)

- New shared type or interface → `kaula-core`. Define the Protocol first, then implement it elsewhere.
- New loop behaviour → `kaula-self-healing`, against core interfaces only.
- Anything touching CrewAI objects → `kaula-runtime` (the only package that wraps CrewAI internals via public extension points — never vendor or patch CrewAI).
- A new pluggable capability (alternate sandbox, scanner, store) → define its Protocol in `kaula-core`, ship a reference impl as a new open package, leave the hardened impl to the commercial side.
- Governance, RBAC, approval, allow-listing → **interface in core, impl is commercial.** The open default `PolicyEngine` is permissive single-user only.

---

## Conventions

- **Python** ≥ 3.11. Type hints required on public APIs; interfaces are `typing.Protocol`.
- **Loop is framework-agnostic.** `kaula-core` and `kaula-self-healing` must be unit-testable with no CrewAI and no live runtime — inject fakes for the Protocols.
- **No ambient credentials in sandboxed execution.** Generated code runs with restricted egress and no inherited secrets.
- **Audit is by-reference for PII.** Never write raw personal data into the audit chain — tokens/references only.
- **Curated memory writes only.** Persist verified, scored outcomes; never let raw failure traces degrade procedural memory.
- **A failed repair is a safe state.** If a candidate can't pass tests+scan within budget, leave the tool broken, keep the run paused, notify a human. Never ship an unverified fix or weaken the gate to make something pass.
- Formatting/lint: `ruff` + `black`. Keep diffs minimal and within the package that owns the concern.

---

## Commands

> Fill these in as the toolchain lands; keep this section authoritative once it does.

```bash
# install workspace (editable, all open packages)
make install            # or: uv sync
# end users instead get the whole open tier via: pip install kaula-kit

# run a single package's tests
make test PKG=kaula-self-healing

# full open-tier test suite
make test

# lint + typecheck
make lint               # ruff + black --check
make typecheck          # mypy / pyright

# the canonical demo: break a tool, watch it heal, inspect the audit trail
make demo-healing

# release (per package; see kaula-oss-architecture.md §8)
make build PKG=kaula-self-healing     # sdist + wheel (hatchling, src layout)
make check-wheel PKG=kaula-self-healing  # fails if wheel contains root kaula/__init__.py
make publish-test PKG=kaula-self-healing # TestPyPI + clean-venv smoke install
make publish PKG=kaula-self-healing      # PyPI via Trusted Publishing (CI only)
```

Publishing rules (non-negotiable):
- Release order follows dependency direction: `kaula-core` first, dependents after.
- Between-package pins are compatible ranges (`kaula-core>=X.Y,<X+1`), never exact, never open.
- Commercial packages publish ONLY to the private index. Never add one to the public publish workflow.
- Never publish manually from a laptop; releases go through the CI workflow (Trusted Publishing, no API tokens).

Before opening a PR: `make lint && make typecheck && make test` must pass, **and** the seam check (no open→commercial import) must be green.

---

## Build order (don't jump ahead)

**v0 = five packages only:** `kaula-core`, `kaula-self-healing`, `kaula-runtime`, `kaula-sandbox-local`, `kaula-audit-local`. Do not scaffold the other packages until told the loop is validated.

1. **[v0]** `kaula-core` interfaces + loop state machine — typed, fully unit-tested, no runtime.
2. **[v0]** `kaula-self-healing` + `kaula-sandbox-local` + `kaula-audit-local` — the contained loop, provable on a fixed toolset.
3. **[v0]** `kaula-runtime` — real CrewAI; broken-tool → heal → resume runs end to end. **v0 ends here.**
4. **[post-validation]** `kaula-memory-local`, `kaula-mcp`, `kaula-planner`, `kaula-cli` — on-ramp and product shell.
5. **[post-validation]** `kaula-kit` — bundle the open set + facade once the packages it wires are stable (it's a front door, not a foundation; build it last among the open tier).
6. Commercial packages (and any second runtime adapter, e.g. `kaula-runtime-langgraph`) register against the same interfaces — no open-tier rewrite.

Step 1 is the whole bet. If the loop contract and its reference impl aren't reliable standalone, nothing above the seam matters. Don't build commercial impls before the open loop is validated with real users.

---

## When unsure

- Don't widen scope to make a feature "complete" — the contained loop and a clean seam beat breadth.
- Don't overclaim in docs or code comments: component maturity is `[MVP] / [Cloud] / [Enterprise] / [Future]`; numbers are design intent, not benchmarks.
- If a change moves the seam (edits a `kaula-core` interface), call it out explicitly in the PR — that's the highest-review-bar surface in the repo.
