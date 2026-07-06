# Releasing (publishing to PyPI)

Kaula publishes through **CI via PyPI Trusted Publishing** — never by
uploading from a laptop (`CLAUDE.md` → Publishing rules). Each open
distribution has its own workflow under `.github/workflows/`:

| Distribution | Workflow | Import | Depends on |
|---|---|---|---|
| `kaula-core` | `publish-kaula-core.yml` | `kaula.core` | — |
| `kaula-audit-local` | `publish-kaula-audit-local.yml` | `kaula.audit_local` | `kaula-core` |
| `kaula-sandbox-local` | `publish-kaula-sandbox-local.yml` | `kaula.sandbox_local` | `kaula-core` |
| `kaula-self-healing` | `publish-kaula-self-healing.yml` | `kaula.self_healing` | `kaula-core` |
| `kaula-runtime` | `publish-kaula-runtime.yml` | `kaula.runtime` | `kaula-core`, `kaula-self-healing` (CrewAI is the optional `[crewai]` extra) |
| `kaula-runtime-langgraph` | `publish-kaula-runtime-langgraph.yml` | `kaula.runtime_langgraph` | `kaula-core`, `kaula-self-healing`, `kaula-runtime`, `langgraph`, `langchain-core` |

Each workflow fires on a tag shaped `<distribution>/vX.Y.Z` and, for that one
package: refuses commercial names, checks the tag matches the pyproject
version, runs the seam check + that package's tests, runs the release-order
guard, builds the sdist + wheel, runs the wheel namespace check, uploads to
TestPyPI, smoke-installs it in a clean venv, and finally publishes to PyPI.

## One-time setup (before the first release)

1. **Reserve the names on PyPI** — register every distribution name so nobody
   squats inside the namespace, *including the bare `kaula`* (a squatter
   owning `kaula` could shadow the whole namespace). Do this for the five
   open names above, the not-yet-built open names (`kaula-memory-local`,
   `kaula-mcp`, `kaula-planner`, `kaula-cli`, `kaula-kit`), and the commercial
   names as placeholders.

2. **Add a Trusted Publisher for each package**, on **both PyPI and
   TestPyPI** (Project → *Publishing* → *Add a pending publisher*):

   | Field | Value |
   |---|---|
   | PyPI project name | the distribution, e.g. `kaula-core` |
   | Owner | `kaulasystems` |
   | Repository | `kaula` |
   | Workflow filename | the matching `publish-<distribution>.yml` |
   | Environment | *(leave blank)* |

   No API tokens are created or stored — Trusted Publishing uses short-lived
   OIDC credentials minted per run.

## Cutting a release

Publish **in dependency order — `kaula-core` first.** Bump the package's
`version` in its `packages/<dist>/pyproject.toml`, merge that, then tag:

```bash
# 1. core first — wait for it to appear on PyPI before the dependents
git tag kaula-core/v0.1.0 && git push origin kaula-core/v0.1.0

# 2. then the dependents (any order among themselves)
git tag kaula-audit-local/v0.1.0    && git push origin kaula-audit-local/v0.1.0
git tag kaula-sandbox-local/v0.1.0  && git push origin kaula-sandbox-local/v0.1.0
git tag kaula-self-healing/v0.1.0   && git push origin kaula-self-healing/v0.1.0
git tag kaula-runtime/v0.1.0        && git push origin kaula-runtime/v0.1.0
```

The release-order guard (`scripts/check_release_deps.py`) will fail a
dependent's workflow if `kaula-core` at the pinned range isn't on PyPI yet —
so if you tag out of order, the dependent simply won't publish until core is
live. Re-run the workflow (re-push the tag) once core is up.

## Version pins

Between-package pins are **compatible ranges, not exact** — e.g.
`kaula-self-healing` requires `kaula-core>=0.1,<0.2`. Bump `kaula-core`'s
patch/minor without re-releasing everything; a breaking Protocol change in
`kaula-core` is a major bump with a deprecation window (it's the SemVer
anchor for the whole set).

## Local dry run (TestPyPI only)

`make publish` / `make publish-test` intentionally error out to enforce the
CI-only rule. For a throwaway experiment against **TestPyPI**:

```bash
make build PKG=kaula-core
make check-wheel PKG=kaula-core
uv publish --publish-url https://test.pypi.org/legacy/ dist/kaula_core-*
```

Do **not** run this against real PyPI — it bypasses the seam check, the
release-order guard, and the smoke test.

## Note: `python-publish.yml`

The stock `python-publish.yml` (GitHub's default template) builds the repo
**root** on GitHub Release and does not understand this five-package
monorepo. The per-package `publish-*.yml` workflows here supersede it; remove
`python-publish.yml` (or leave it disabled) so a published Release doesn't
trigger a broken build.

## Commercial packages

Commercial distributions (`kaula-sandbox-hardened`, `kaula-memory-cloud`,
`kaula-mcp-governed`, `kaula-governance`, `kaula-audit-cloud`,
`kaula-healing-network`) **never** publish to public PyPI. The workflows here
hard-block those names; commercial releases go to a private index through a
separate, private pipeline.
