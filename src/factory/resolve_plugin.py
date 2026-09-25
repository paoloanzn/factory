"""Resolve an optional plugin by name from filesystem search paths."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from factory.plugin import Plugin


class ResolvePluginError(Exception):
    """Base class for plugin resolution failures."""


class PluginNotFoundError(ResolvePluginError):
    """Raised when no search path contains the requested plugin."""


class AmbiguousPluginError(ResolvePluginError):
    """Raised when more than one search path contains the requested plugin."""


class PluginNameMismatchError(ResolvePluginError):
    """Raised when the loaded descriptor's name differs from the requested name."""


def resolve_plugin(name: str, search_paths: tuple[Path, ...]) -> Plugin:
    """Load the plugin named ``name`` from exactly one of ``search_paths``.

    Looks for ``<search-path>/<name>.py`` in each search path with no
    recursion. Exactly one candidate file must exist, and only that file is
    imported; every other file in the search directories is left untouched.
    The loaded module's ``PLUGIN`` descriptor must be named ``name``.
    """
    matches: list[Path] = []
    seen: set[Path] = set()
    for search_path in search_paths:
        candidate = search_path / f"{name}.py"
        if candidate.is_file() and candidate not in seen:
            seen.add(candidate)
            matches.append(candidate)
    if not matches:
        raise PluginNotFoundError(f'plugin "{name}" not found in any search path')
    if len(matches) > 1:
        locations = ", ".join(str(match) for match in matches)
        raise AmbiguousPluginError(
            f'plugin "{name}" found in more than one search path: {locations}'
        )
    plugin = _load_plugin_file(matches[0])
    if plugin.name != name:
        raise PluginNameMismatchError(
            f'plugin descriptor in {matches[0]} is named "{plugin.name}", '
            f'expected "{name}"'
        )
    return plugin


def _load_plugin_file(path: Path) -> Plugin:
    """Load a Plugin from a single ``.py`` file without mutating ``sys.path``.

    Mirrors the ``load_plugin(path: Path) -> Plugin`` filesystem-loader
    interface so the canonical loader can replace this helper once available.
    """
    spec = importlib.util.spec_from_file_location(f"factory_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ResolvePluginError(f"cannot load plugin from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    descriptor = getattr(module, "PLUGIN", None)
    if not isinstance(descriptor, Plugin):
        raise ResolvePluginError(
            f"{path} does not expose PLUGIN as a factory plugin descriptor"
        )
    return descriptor
