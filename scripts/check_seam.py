#!/usr/bin/env python3
"""Seam check: fails the build if the open/commercial boundary is violated.

Enforced rules (see CLAUDE.md and docs/kaula-oss-architecture.md §1a/§5):
1. No open package imports a commercial `kaula.*` subpackage.
2. Only `kaula-runtime*` adapter packages import an orchestration framework.
3. `kaula-core` imports no other `kaula.*` subpackage (dependencies point toward core).
4. No package ships a top-level `kaula/__init__.py` (PEP 420 namespace stays implicit).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"

COMMERCIAL_SUBPACKAGES = {
    "sandbox_hardened",
    "memory_cloud",
    "mcp_governed",
    "governance",
    "audit_cloud",
    "healing_network",
}

ORCHESTRATION_FRAMEWORKS = {
    "crewai",
    "langgraph",
    "langchain",
    "langchain_core",
    "langchain_community",
    "autogen",
}


def imported_modules(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return found


def check() -> list[str]:
    errors: list[str] = []
    for pkg_dir in sorted(PACKAGES_DIR.iterdir()):
        if not pkg_dir.is_dir():
            continue
        pkg_name = pkg_dir.name
        is_runtime_adapter = pkg_name.startswith("kaula-runtime")

        root_init = pkg_dir / "src" / "kaula" / "__init__.py"
        if root_init.exists():
            errors.append(f"{root_init}: top-level kaula/__init__.py shadows the namespace root")

        for py_file in sorted((pkg_dir / "src").rglob("*.py")):
            for module, lineno in imported_modules(py_file):
                parts = module.split(".")
                where = f"{py_file.relative_to(REPO_ROOT)}:{lineno}"
                if parts[0] == "kaula" and len(parts) > 1:
                    sub = parts[1]
                    if sub in COMMERCIAL_SUBPACKAGES:
                        errors.append(f"{where}: open package imports commercial kaula.{sub}")
                    if pkg_name == "kaula-core" and sub != "core":
                        errors.append(f"{where}: kaula-core must not import kaula.{sub}")
                if parts[0] in ORCHESTRATION_FRAMEWORKS and not is_runtime_adapter:
                    errors.append(f"{where}: only kaula-runtime* adapters may import {parts[0]}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("Seam check FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print("Seam check passed: dependency direction and namespace rules hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
