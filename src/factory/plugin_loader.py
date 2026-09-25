"""Load Factory plugin descriptors from standalone Python files."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

from factory.plugin import InvalidPluginError, Plugin, PluginError
from factory.plugin_config import PluginConfig
from factory.work import WorkUnit


class PluginLoadError(Exception):
    """Base class for plugin loading failures."""


class PluginFileNotFoundError(PluginLoadError):
    """Raised when the plugin path does not point to a file."""


class PluginImportError(PluginLoadError):
    """Raised when the plugin file cannot be imported."""


class MissingPluginDescriptorError(PluginLoadError):
    """Raised when the imported module defines no PLUGIN value."""


class InvalidPluginDescriptorError(PluginLoadError):
    """Raised when the module's PLUGIN is not a Plugin instance."""


def load_plugin(path: Path) -> Plugin:
    """Load the PLUGIN descriptor defined by the Python file at ``path``.

    The file is imported in isolation with importlib: ``sys.path`` is not
    modified, and the plugin does not need to be installed as a package.
    Only the module-level ``PLUGIN`` value is read and returned; the
    plugin file is otherwise unused and no lifecycle behavior runs.
    """
    module = _import_module(path)
    descriptor: object = getattr(module, "PLUGIN", None)
    if descriptor is None:
        raise MissingPluginDescriptorError(
            f"plugin module {module.__name__} defines no PLUGIN"
        )
    if not isinstance(descriptor, Plugin):
        raise InvalidPluginDescriptorError(
            f"PLUGIN in {module.__name__} is not a Plugin instance"
        )
    return descriptor


def _import_module(path: Path) -> ModuleType:
    """Import the Python file at ``path`` as a standalone module."""
    if not path.is_file():
        raise PluginFileNotFoundError(f"plugin file not found: {path}")
    name = _module_name(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PluginImportError(f"no import loader for plugin file: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        # Import failures are a domain error here: a plugin file may raise
        # anything while being imported, and the caller must see that as a
        # PluginImportError instead of an unexpected exception.
        sys.modules.pop(name, None)
        raise PluginImportError(f"failed to import plugin file: {path}") from error
    return module


def _module_name(path: Path) -> str:
    """Return a unique synthetic module name for a plugin file."""
    digest = hashlib.sha256(os.fsencode(path.absolute())).hexdigest()[:16]
    stem = re.sub(r"\W", "_", path.stem)
    return f"factory_plugin_{stem}_{digest}"


class PluginNotFoundError(PluginError):
    """Raised when no search path contains the requested plugin."""


class AmbiguousPluginError(PluginError):
    """Raised when several search paths contain the requested plugin."""


def resolve_plugin(name: str, search_paths: tuple[Path, ...]) -> Plugin:
    """Resolve one enabled plugin by name.

    Looks for ``<search-path>/<name>.py`` in each search path (no
    recursion) and loads only the single matching file. Zero matches
    raise PluginNotFoundError; more than one match raises
    AmbiguousPluginError instead of silently picking a winner.
    """
    candidates = (path / f"{name}.py" for path in search_paths)
    matches = [path for path in candidates if path.is_file()]
    if not matches:
        raise PluginNotFoundError(f'no plugin named "{name}" in search paths')
    if len(matches) > 1:
        locations = ", ".join(str(path) for path in matches)
        raise AmbiguousPluginError(
            f'plugin "{name}" is ambiguous, found in: {locations}'
        )
    return _load_plugin_file(matches[0], name)


def _load_plugin_file(path: Path, name: str) -> Plugin:
    try:
        plugin = load_plugin(path)
    except PluginLoadError as error:
        raise InvalidPluginError(f"plugin file {path} is invalid: {error}") from error
    if plugin.name != name:
        raise InvalidPluginError(
            f'plugin file {path} declares name "{plugin.name}", expected "{name}"'
        )
    return plugin


def load_enabled_plugins(
    config: PluginConfig, *, builtin_dir: Path = Path("plugins/builtin")
) -> tuple[Plugin, ...]:
    """Load the optional plugins enabled through PluginConfig.

    Search paths are the builtin plugin directory followed by the
    configured extra paths. Plugins load in ``enabled`` order; core
    plugins are handled separately and never touched here.
    """
    search_paths = (builtin_dir, *config.paths)
    return tuple(resolve_plugin(name, search_paths) for name in config.enabled)


def collect_startup_units(
    config: PluginConfig,
    *,
    core_dir: Path = Path("plugins/core"),
    builtin_dir: Path = Path("plugins/builtin"),
    extra_units: tuple[WorkUnit, ...] = (),
) -> tuple[WorkUnit, ...]:
    """Assemble the work units Factory runs with at startup.

    Core plugin units first, then the configured optional plugin units
    (in ``enabled`` order), then any extra units such as the existing
    Python entry-point work units. A missing ``factory.toml`` yields
    empty optional configuration via ``PluginConfig.load()``.
    """
    # Local import: factory.core_plugins imports this module.
    from factory.core_plugins import load_core_plugins

    plugins = (
        *load_core_plugins(core_dir),
        *load_enabled_plugins(config, builtin_dir=builtin_dir),
    )
    return tuple(unit for plugin in plugins for unit in plugin.units) + tuple(
        extra_units
    )
