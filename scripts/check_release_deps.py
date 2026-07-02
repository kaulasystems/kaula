#!/usr/bin/env python3
"""Release-order guard (docs/kaula-oss-architecture.md §8.3).

Release order follows dependency direction: refuse to publish a package
whose kaula-* dependencies are not already available on the target index.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def kaula_dependencies(package: str) -> list[str]:
    pyproject = REPO_ROOT / "packages" / package / "pyproject.toml"
    with open(pyproject, "rb") as handle:
        data = tomllib.load(handle)
    names = []
    for requirement in data["project"].get("dependencies", []):
        match = re.match(r"^(kaula-[A-Za-z0-9-]+)", requirement)
        if match:
            names.append(match.group(1))
    return names


def available_on_index(name: str, index_url: str) -> bool:
    url = f"{index_url.rstrip('/')}/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            json.load(response)
        return True
    except Exception:
        return False


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: check_release_deps.py <package> [<index json api base>]", file=sys.stderr)
        return 2
    package = argv[0]
    index = argv[1] if len(argv) > 1 else "https://pypi.org/pypi"
    missing = [dep for dep in kaula_dependencies(package) if not available_on_index(dep, index)]
    if missing:
        print(
            f"Refusing to release {package}: dependencies not yet on the index: "
            f"{', '.join(missing)} (release kaula-core first, dependents after)",
            file=sys.stderr,
        )
        return 1
    print(f"Release-order check passed for {package}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
