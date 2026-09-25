"""Load Factory plugin descriptors from standalone Python files."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

from factory.plugin import Plugin


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
