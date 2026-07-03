# Kaula — Open-Source Library Architecture

*This document specifies the **package and module structure** of the open-source Kaula libraries: how the monorepo is laid out, where the open/commercial seam falls in code, and the extension points the commercial layer plugs into. Targets: Python (matching CrewAI), monorepo of separately-publishable packages under a shared `kaula.*` import namespace, **production-grade** open tier (stable interfaces, real coverage, an honest reference sandbox).*

---

## 0. Design goals (what "open-source" must satisfy here)

1. **Production-usable standalone.** The open packages must run a real self-healing loop in production — just not at fleet scale, and without the hardened sandbox or governed control plane. If the open tier only works as a demo, adoption churns the week someone tries to ship it.
2. **A clean seam, drawn in code.** Open packages depend only on *interfaces* (Protocols / ABCs) for the parts that become commercial. The commercial packages provide alternate implementations of those same interfaces. No fork, no rewrite — a dependency swap.
3. **CrewAI is a dependency, not a fork — and not a marriage.** Kaula wraps CrewAI primitives through public extension points; we never vendor or patch CrewAI internals. Strategically, the framework touchpoint is confined to one package (`kaula-runtime`) designed as the *first of potentially several runtime adapters*. The loop itself (`kaula.core`, `kaula.self_healing`) is framework-agnostic by rule, which keeps Kaula portable if the orchestration-framework market shifts (as of early 2026, enterprise adoption is consolidating toward LangGraph while CrewAI leads prototyping — exactly Kaula's buyer moving substrate). A `kaula-runtime-langgraph` adapter is a plausible early follow-on, not a rewrite.
4. **Each package publishes independently.** Separate versioning and release cadence so the loop can stabilise without waiting on the planner, and vice versa.
5. **The hard, value-accumulating parts are isolated behind interfaces from day one** — sandbox, memory persistence, MCP governance, audit sink — so commercial implementations slot in without touching open code.
6. **One import root, many distributions.** Everything imports as `kaula.<lib>` for a unified developer experience, but each `kaula.<lib>` is shipped by its own independently-published, independently-licensed distribution. The umbrella name unifies *ergonomics*; it never collapses the open/commercial seam.

---

## 1. Repository layout (monorepo)

A single repo, multiple installable packages under `packages/`. Each package is a **distribution** (what you `pip install` and publish) that contributes a **subpackage** into the shared `kaula` import namespace (what you write in code). Open packages publish to PyPI; commercial packages live in a private path / private index and are **not** in the open repo's published set.

```
kaula/                                  ← monorepo root
├── packages/
│   │
│   │  ── OPEN (Core) — published to PyPI, Apache-2.0 ──
│   │      distribution name          imports as
│   ├── kaula-core/            #   kaula.core         interfaces, types, loop contract
│   ├── kaula-self-healing/    #   kaula.self_healing the self-healing loop (reference impl)
│   ├── kaula-runtime/         #   kaula.runtime      CrewAI wrap + tool interception
│   ├── kaula-sandbox-local/   #   kaula.sandbox_local reference sandbox (local Docker)
│   ├── kaula-audit-local/     #   kaula.audit_local  append-only audit + rollback
│   ├── kaula-memory-local/    #   kaula.memory_local ephemeral semantic + procedural memory
│   ├── kaula-mcp/             #   kaula.mcp          basic MCP connector + local logging
│   ├── kaula-planner/         #   kaula.planner      NL→Crew planner (the on-ramp)
│   ├── kaula-cli/             #   kaula.cli          describe → review → run → inspect
│   └── kaula-kit/             #   kaula (facade)     meta-package: installs the open set, exposes entry point
│
│   │  ── COMMERCIAL — private index, NOT in open repo ──
│   ├── kaula-sandbox-hardened/  # kaula.sandbox_hardened  [Cloud/Ent] escape-hardened exec
│   ├── kaula-memory-cloud/      # kaula.memory_cloud      [Cloud] persistent cross-run memory
│   ├── kaula-mcp-governed/      # kaula.mcp_governed      [Cloud] allow-list · screen · audit
│   ├── kaula-governance/        # kaula.governance        [Cloud] RBAC, approval, autonomy tiers
│   ├── kaula-audit-cloud/       # kaula.audit_cloud       [Cloud] tamper-evident store at scale
│   └── kaula-healing-network/   # kaula.healing_network   [Future] shared signed-patch exchange
│
├── examples/                # runnable vertical demos (extraction, AML)
├── docs/
└── pyproject.toml           # workspace / tooling root
```

Each package's source tree is laid out so it contributes only its own subpackage into the namespace — e.g. `kaula-self-healing/` contains `src/kaula/self_healing/` and **no** `kaula/__init__.py` of its own (see §1a). Convention: distribution names are hyphenated (`kaula-self-healing`), import paths are underscored (`kaula.self_healing`).

**Why a monorepo of separate packages, not one big package or many repos:** one repo keeps the interface contracts and their reference implementations versioned together and refactored atomically (an interface change and the impls that satisfy it land in one PR). Separate *distributions* keep the install surface honest — a user installing `kaula-self-healing` does not drag in the planner or CLI — and let the open/commercial seam be a publish-time boundary, not a code-comment boundary. The shared `kaula.*` import root gives one clean developer experience across all of them.

---

## 1a. The namespace mechanism (how one `kaula` spans many distributions)

`kaula` is a **PEP 420 implicit namespace package**. No single distribution owns it; each contributes a subpackage into it. This is the same pattern used by large multi-distribution ecosystems (e.g. `google.*`).

Rules that make it work — and that an agent or contributor must not violate:

- **No distribution ships a top-level `kaula/__init__.py`.** That file would claim ownership of the namespace root and shadow every sibling. The root `kaula` must stay an implicit namespace package (just a directory, no `__init__.py`).
- **Each distribution owns exactly one subpackage** and ships only `src/kaula/<its_subpackage>/`.
- **`from kaula import self_healing` resolves by whichever distribution providing `kaula.self_healing` is installed.** If it isn't installed, that's an `ImportError` — which is the correct, honest behaviour: a commercial subpackage simply isn't importable in an open-only install.
- **The seam is unchanged.** Sharing an import root does not let an open subpackage import a commercial one; the dependency rule in §5 still holds, enforced by the same CI check. Namespace ≠ dependency.

```python
# open-only install
from kaula import self_healing, sandbox_local, audit_local   # ✓ all present
from kaula import sandbox_hardened                            # ✗ ImportError — not installed

# after: pip install kaula-sandbox-hardened (from private index)
from kaula import sandbox_hardened                            # ✓ now resolves
```

---

## 2. The seam: which package owns each interface

Everything that becomes commercial is defined as an interface in **`kaula-core`** and implemented twice: a reference impl in an open package, a production impl in a commercial package. The open library never imports a commercial package; it resolves implementations at runtime via configuration (see §4).

| Interface (in `kaula-core`) | Open reference impl | Commercial impl | Why the seam is here |
|---|---|---|---|
| `Sandbox` | `kaula-sandbox-local` | `kaula-sandbox-hardened` | Operating a truly isolated, escape-hardened sandbox is the top paid wedge; the local one is honest but not multi-tenant-safe. |
| `AuditSink` | `kaula-audit-local` | `kaula-audit-cloud` | Local append-only log is real and verifiable; fleet-scale tamper-evident storage is operational work buyers won't do themselves. |
| `MemoryStore` | `kaula-memory-local` | `kaula-memory-cloud` | Procedural memory is the compounding asset; persistent cross-run memory is where it compounds. |
| `MCPGateway` | `kaula-mcp` (connect + log) | `kaula-mcp-governed` | Connecting is open; org-wide allow-listing, response screening, credential brokering is governance buyers require. |
| `PolicyEngine` | `kaula-core` (permissive default) | `kaula-governance` | Open default = single-user, all-autonomous-if-green; RBAC/approval/autonomy-tiers are enterprise. |

This is the literal expression of "easy to add, impossible to add invisibly" and "open core / closed value": the *mechanism* is open and inspectable; the *operationally hard, value-accumulating* implementations are separable.

---

## 3. Package-by-package (open tier)

### `kaula-core` — the contracts
No heavy dependencies. Defines:
- **Domain types:** `ToolFailure`, `RepairCandidate`, `SandboxResult`, `ScanResult`, `AuditEvent`, `ToolVersion`, `Plan`.
- **Protocols** (the seam): `Sandbox`, `AuditSink`, `MemoryStore`, `MCPGateway`, `PolicyEngine`, `Scanner`, `RepairAgent`.
- **The loop contract:** the ordered state machine `DETECT → DIAGNOSE → SANDBOX → TEST → SCAN → GATE → HOT-SWAP → RECORD → PERSIST → RESUME` as an explicit, testable type, independent of any implementation.
- **No CrewAI import.** Core stays framework-agnostic so the loop is reusable and unit-testable without a runtime.

### `kaula-self-healing` — the differentiator, in the open
The reference implementation of the self-healing loop against the `kaula-core` contracts. Orchestrates: capture failure → invoke `RepairAgent` → build candidate in a `Sandbox` → run tests → run `Scanner` → consult `PolicyEngine` gate → hot-swap → write to `AuditSink` → curate to `MemoryStore`. Depends only on `kaula-core` interfaces, never concrete impls. **This is deliberately open** — the mechanism earns trust; the hardened sandbox behind it is what's held back.

### `kaula-runtime` — the CrewAI adapter
The single most important hook: intercepts every CrewAI tool call (`try/except` wrap), captures full context on failure, and triggers `kaula-self-healing`. Owns the Flow/Crew/Task integration and state-pause-on-failure so no partial results commit. Depends on CrewAI + `kaula-core` + `kaula-self-healing`.

**Adapter, not foundation.** This is the *only* open package allowed to import CrewAI, and it is deliberately shaped as the first of a family: everything framework-specific (interception hook, state pause, resume semantics) lives here behind the framework-agnostic loop. If/when a second runtime is warranted (e.g. `kaula-runtime-langgraph` → `kaula.runtime_langgraph`), it is a sibling adapter against the same `kaula.core` contracts — no change to the loop, the seam, or user-facing healing behaviour. CI enforces the corollary: **no package other than a `kaula-runtime*` adapter may import an orchestration framework.**

### `kaula-sandbox-local` — reference `Sandbox`
Local Docker execution with resource limits, timeout, no ambient credentials, restricted egress. **Honest about its boundary:** documented as single-tenant and not escape-hardened for hostile multi-tenant use — that's the commercial sandbox's job. Good enough for a team running their own agents on their own machine in production.

### `kaula-audit-local` — reference `AuditSink` + rollback
Append-only, content-hashed, chained records in SQLite/files; the rollback index (version-addressable revert as a single logged action). Verifiable, not just readable. PII-by-reference design baked in (tokens, not raw personal data, in the chained content).

### `kaula-memory-local` — reference `MemoryStore`
Semantic + procedural partitions, local/ephemeral. Curated writes only (verified, scored outcomes persist) so memory doesn't degrade the agent. Procedural memory warm-starts healing within a session.

### `kaula-mcp` — basic MCP
Connect to MCP servers + local per-call logging. The governance (allow-list, screening, credential brokering) is explicitly *not* here — it's the `MCPGateway` commercial impl.

### `kaula-planner` — the on-ramp
NL objective → editable `Plan` → compiled CrewAI objects; NL diffs ("make it hierarchical", "add a reviewer") applied to the plan, each logged. **Framed as table stakes, not the differentiator** — its value is that everything it produces lands inside the healing + audit spine from the first run.

### `kaula-cli` — makes the open tier a product
`describe → review → run → inspect`. Turns the library into something usable without writing glue, including a terminal view of the healing timeline (a read model over the audit event stream).

### `kaula-kit` — the front door (meta-package)
A deliberately **thin** convenience distribution. Its entire job:
- **Dependency bundle:** `pip install kaula-kit` pulls the whole open set (`kaula-core`, `kaula-self-healing`, `kaula-runtime`, the three local impls, `kaula-mcp`, `kaula-planner`, `kaula-cli`) at compatible versions. One install to get a working open tier.
- **Entry point:** exposes the assembled `Kaula` facade so users write `from kaula import Kaula` without knowing which subpackage assembles it.

Hard constraints (a meta-package is where the seam most easily blurs):
- **Almost no logic.** It wires the open reference impls into a default `Kaula` and re-exports it. Anything more belongs in the package that owns the concern.
- **Open-only.** `kaula-kit` never depends on, references, or knows about any commercial package. Upgrading to Cloud is still `pip install kaula-<commercial>` + config — `kaula-kit` is not involved and is not a prerequisite.
- **The facade respects the namespace.** Because no distribution may own `kaula/__init__.py` (§1a), the facade is *defined* in an importable subpackage (e.g. `kaula.kit`) and the top-level `from kaula import Kaula` is satisfied by a re-export the namespace tooling allows — never by a hand-written root `__init__.py`. If honoring `from kaula import Kaula` literally would require claiming the namespace root, prefer `from kaula.kit import Kaula` and document that as the entry point instead. **Clean namespace beats a prettier import.**

---

## 4. How implementations are resolved (the swap mechanism)

The open library never imports a commercial package. Implementations are selected at runtime by configuration, so installing the commercial package + changing config is the entire upgrade path — no code change in user agents.

```python
# open default — everything reference, single-user
from kaula import Kaula        # facade assembled by kaula-kit (or: from kaula.kit import Kaula)
kaula = Kaula()   # local sandbox, local audit, local memory, permissive policy

# commercial — same agent code, hardened impls resolved from config/install
# pip install kaula-sandbox-hardened kaula-governance kaula-mcp-governed
kaula = Kaula(config="kaula.cloud.toml")
```

Resolution order, simplest first (we are not building a plugin framework for its own sake):
1. **Explicit injection** — pass an impl object directly (used in tests and embedding).
2. **Config-declared** — `kaula.toml` names the impl for each interface; resolved by import path.
3. **Default** — if nothing is configured, fall back to the open reference impl.

A lightweight registry in `kaula-core` maps each `Protocol` to its configured/​default impl. No commercial code path exists in the open repo; the commercial packages simply register themselves as alternate impls when installed. This keeps the seam a **publish-time + install-time** boundary, exactly matching the monorepo package split.

---

## 5. Dependency direction (the rule that keeps the seam clean)

```
kaula-cli ─┐
kaula-planner ─┤
kaula-runtime ─┼──► kaula-self-healing ──► kaula-core ◄── (all interfaces live here)
kaula-mcp ─┘                                ▲
                                            │ implement (never imported by core)
   kaula-sandbox-local · kaula-audit-local · kaula-memory-local
                                            ▲
                                            │ implement (resolved at runtime only)
   kaula-sandbox-hardened · kaula-memory-cloud · kaula-mcp-governed · kaula-governance
```

**The one inviolable rule:** dependencies point *toward* `kaula-core`. Core imports nothing from the layers that implement its interfaces. Commercial packages depend on `kaula-core` (and may depend on open impls), but **no open package ever depends on a commercial one**. A CI check enforces this — an open package importing a commercial package fails the build. This is what makes the open repo genuinely standalone and the seam real rather than aspirational.

---

## 6. Licensing & governance of the open tier

- **License:** Apache-2.0 for the open packages (permissive drives adoption; patent grant matters for an agent-infra tool enterprises will scrutinise). The decision and any CLA are flagged to legal, not assumed here.
- **What's open vs. held:** open = `kaula-core`, `-self-healing`, `-runtime`, `-sandbox-local`, `-audit-local`, `-memory-local`, `-mcp`, `-planner`, `-cli`. Held = hardened sandbox, cloud memory, governed MCP, governance/RBAC, fleet-scale audit, healing network.
- **Contribution boundary:** PRs welcome on open packages; the interface definitions in `kaula-core` are the sensitive surface (changing them moves the seam), so they carry a stricter review bar.
- **Honest README boundary:** the open sandbox's single-tenant limitation and the absence of org-wide governance are documented up front, not discovered. Production users should know exactly where the open tier stops.

---

## 7. Build order for the OSS repo

**v0 ships five packages, not nine.** Pre-validation, every published package is maintenance surface, versioning overhead, and implied API commitment. The loop-critical set is `kaula-core`, `kaula-self-healing`, `kaula-runtime`, `kaula-sandbox-local`, `kaula-audit-local` — enough to run, demo, and put into first production use the entire differentiator. The rest of the open tier follows once the loop is validated with real users.

1. **[v0]** `kaula-core` interfaces + the loop state machine as a typed, fully unit-tested contract (no runtime needed).
2. **[v0]** `kaula-self-healing` + `kaula-sandbox-local` + `kaula-audit-local` against core — the contained loop, provable on a fixed toolset.
3. **[v0]** `kaula-runtime` — wire it into real CrewAI; the broken-tool → heal → resume demo runs end to end. **v0 ends here.**
4. **[post-validation]** `kaula-memory-local`, `kaula-mcp`, `kaula-planner`, `kaula-cli` — the on-ramp and the product shell.
5. **[post-validation]** `kaula-kit` — bundle the open set and expose the facade, once the packages it wires are stable.
6. Commercial packages register against the same interfaces — no open-tier rewrite. A second runtime adapter (`kaula-runtime-langgraph`) slots in at this layer too, if the framework market motion warrants it.

> Step 1 is still the whole bet, now expressed as code structure: if the loop contract in `kaula-core` and its reference impl in `kaula-self-healing` aren't reliable and trustworthy standalone, nothing above the seam matters. Validate production demand for that loop with real users before building the commercial impls — or steps 4–6 at all.

---

## 8. pip deployment (packaging & publishing)

How the distributions are built and released. The rules here are load-bearing: a publishing mistake can break the namespace for every user or leak the seam.

### 8.1 Package build layout
Each package under `packages/` is a standard `src`-layout project with its own `pyproject.toml`:

```
packages/kaula-self-healing/
├── pyproject.toml            # name = "kaula-self-healing"
├── src/
│   └── kaula/                # NO __init__.py here — ever (§1a)
│       └── self_healing/
│           └── __init__.py   # the subpackage's own init is fine
└── tests/
```

- **Build backend:** `hatchling` (modern, handles implicit namespace packages cleanly with src layout). One backend across all packages — no per-package divergence.
- **The namespace rule at build time:** the sdist/wheel must contain `kaula/self_healing/**` and **no** top-level `kaula/__init__.py`. A CI check inspects every built wheel for a root `kaula/__init__.py` and fails the release if present — this is the packaging-level enforcement of §1a.

### 8.2 Versioning & dependency pins
- **Independent versions per distribution** (design goal 4). No lockstep: `kaula-cli` at 0.3 can depend on `kaula-core` 1.1.
- **Compatible-release pins between kaula packages:** downstream packages pin `kaula-core>=X.Y,<X+1` (compatible range, not exact). Exact pins would force lockstep releases; open ranges would let core break siblings.
- **`kaula-core` is the version anchor.** Its public interfaces follow strict SemVer: any breaking Protocol change is a major bump, announced, with a deprecation window. Everything else keys off it.
- **`kaula-kit` pins a tested set:** the meta-package pins the specific compatible versions verified together in CI, so `pip install kaula-kit` always yields a coherent open tier.

### 8.3 Publishing pipeline
- **PyPI via Trusted Publishing (OIDC) from GitHub Actions** — no long-lived API tokens to leak. Each package gets a PyPI publisher configuration bound to the repo + release workflow.
- **Release flow per package:** tag `<pkg>/vX.Y.Z` → CI builds sdist+wheel → wheel namespace check (§8.1) → seam check (no open→commercial import, no non-adapter framework import) → full test matrix → publish to **TestPyPI** → smoke-install from TestPyPI in a clean venv (`pip install`, `from kaula import <lib>`) → publish to PyPI.
- **Release order respects dependency direction:** `kaula-core` first, then its dependents. CI refuses to publish a package whose kaula dependencies at the pinned range aren't already on PyPI.
- **Commercial packages never touch PyPI.** They publish to a private index (e.g. a private PyPI-compatible registry); customers install with an `--extra-index-url`/keyring credential. A CI guard blocks any `kaula-sandbox-hardened`-class package from the public publish workflow entirely — the seam enforced at the registry level.

### 8.4 Name reservation (do this before anything else)
Before the first release, register on PyPI: the nine open distribution names, the commercial names (registered as placeholders so no one squats inside the brand namespace), **and the bare `kaula` name** — reserved even if unused, since a squatter owning `kaula` could ship a malicious root package that shadows the entire namespace for every user.

