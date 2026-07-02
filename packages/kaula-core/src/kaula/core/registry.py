"""Lightweight implementation registry (docs/kaula-oss-architecture.md §4).

Maps each seam Protocol to its implementation. Resolution order, simplest
first — this is deliberately not a plugin framework:

1. explicit injection (tests, embedding)
2. config-declared import path (``kaula.toml`` → ``[implementations]``)
3. registered open default

Commercial packages register themselves as alternate impls when installed;
no commercial code path exists here.
"""

from __future__ import annotations

import importlib
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

__all__ = ["Registry", "ResolutionError", "load_symbol"]


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
    def __init__(self) -> None:
        self._explicit: dict[type, Any] = {}
        self._config: dict[str, str] = {}
        self._defaults: dict[type, Callable[[], Any]] = {}

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
        config_path = self._config.get(interface.__name__.lower())
        if config_path is not None:
            symbol = load_symbol(config_path)
            return symbol() if callable(symbol) else symbol
        if interface in self._defaults:
            return self._defaults[interface]()
        raise ResolutionError(
            f"no implementation for {interface.__name__}: inject one explicitly, "
            f"declare it in config, or install a package that provides a default"
        )
