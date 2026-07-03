"""Lightweight implementation registry (docs/kaula-oss-architecture.md §4).

Maps each seam Protocol to its implementation. Resolution order, simplest
first — this is deliberately not a plugin framework:

1. explicit injection (tests, embedding)
2. config-declared import path (``kaula.toml`` → ``[implementations]``)
3. installed-package entry points (group ``kaula.implementations``)
4. registered open default

Entry points are how packages register themselves at install time: each
distribution declares ``<interface name> = "module:Impl"`` under the
``kaula.implementations`` group, so a commercial impl slots in by being
installed + named in config — no open code path ever imports it. If several
installed packages provide the same interface and config doesn't pick one,
resolution fails loudly rather than choosing silently.
"""

from __future__ import annotations

import importlib
import tomllib
from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

__all__ = ["ENTRY_POINT_GROUP", "Registry", "ResolutionError", "load_symbol"]

ENTRY_POINT_GROUP = "kaula.implementations"


class ResolutionError(LookupError):
    pass


def load_symbol(path: str) -> Any:
    """Import ``"package.module:Attribute"`` and return the attribute."""
    module_name, _, attr = path.partition(":")
    if not module_name or not attr:
        raise ValueError(f"implementation path must look like 'module:Attr', got {path!r}")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ResolutionError(f"{module_name} has no attribute {attr!r}") from exc


class Registry:
    def __init__(self, *, discover_installed: bool = False) -> None:
        self._explicit: dict[type, Any] = {}
        self._config: dict[str, str] = {}
        self._entry_points: dict[str, list[str]] = {}
        self._defaults: dict[type, Callable[[], Any]] = {}
        if discover_installed:
            self.load_entry_points()

    def load_entry_points(self, group: str = ENTRY_POINT_GROUP) -> None:
        """Discover implementations registered by installed distributions."""
        for entry_point in entry_points(group=group):
            values = self._entry_points.setdefault(entry_point.name.lower(), [])
            if entry_point.value not in values:
                values.append(entry_point.value)

    def register(self, interface: type, instance: Any) -> None:
        """Explicit injection — always wins."""
        self._explicit[interface] = instance

    def register_default(self, interface: type, factory: Callable[[], Any]) -> None:
        """The open reference impl, used when nothing else is configured."""
        self._defaults[interface] = factory

    def configure(self, mapping: Mapping[str, str]) -> None:
        """Interface name (case-insensitive) → ``"module:Attr"`` import path."""
        for key, value in mapping.items():
            self._config[key.lower()] = value

    def load_config(self, path: str | Path) -> None:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        self.configure(data.get("implementations", {}))

    def resolve(self, interface: type) -> Any:
        if interface in self._explicit:
            return self._explicit[interface]
        key = interface.__name__.lower()
        config_path = self._config.get(key)
        if config_path is not None:
            symbol = load_symbol(config_path)
            return symbol() if callable(symbol) else symbol
        discovered = self._entry_points.get(key, [])
        if len(discovered) > 1:
            raise ResolutionError(
                f"multiple installed implementations for {interface.__name__} "
                f"({', '.join(sorted(discovered))}); declare the one to use in config"
            )
        if discovered:
            symbol = load_symbol(discovered[0])
            return symbol() if callable(symbol) else symbol
        if interface in self._defaults:
            return self._defaults[interface]()
        raise ResolutionError(
            f"no implementation for {interface.__name__}: inject one explicitly, "
            f"declare it in config, or install a package that provides one"
        )
