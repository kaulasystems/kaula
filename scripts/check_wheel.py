#!/usr/bin/env python3
"""Wheel namespace check (docs/kaula-oss-architecture.md §8.1).

Fails if a built wheel contains a top-level ``kaula/__init__.py`` — that file
would claim the PEP 420 namespace root and shadow every sibling distribution.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def check_wheel(wheel_path: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
    if "kaula/__init__.py" in names:
        errors.append(f"{wheel_path.name}: contains top-level kaula/__init__.py")
    if not any(name.startswith("kaula/") for name in names):
        errors.append(f"{wheel_path.name}: contributes nothing under the kaula namespace")
    return errors


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_wheel.py <wheel> [<wheel> ...]", file=sys.stderr)
        return 2
    errors: list[str] = []
    for arg in argv:
        path = Path(arg)
        if not path.exists():
            errors.append(f"{arg}: wheel not found")
            continue
        errors.extend(check_wheel(path))
    if errors:
        print("Wheel check FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print(f"Wheel check passed for {len(argv)} wheel(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
