"""Behavior tests for loading Factory plugins from Python files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from factory.plugin import Plugin
from factory.plugin_loader import (
    InvalidPluginDescriptorError,
    MissingPluginDescriptorError,
    PluginFileNotFoundError,
    PluginImportError,
    PluginLoadError,
    load_plugin,
)

_EXAMPLE_PLUGIN = """\
from factory.plugin import Plugin


class ExampleUnit:
    @property
    def name(self) -> str:
        return "example.unit"

    def run(self, input, context):  # pragma: no cover - never executed
        raise NotImplementedError


PLUGIN = Plugin(name="example", units=(ExampleUnit(),))
"""


def _write_plugin(path: Path, source: str = _EXAMPLE_PLUGIN) -> Path:
    path.write_text(source, encoding="utf-8")
    return path


def test_load_plugin_returns_plugin_descriptor(tmp_path: Path) -> None:
    path = _write_plugin(tmp_path / "example.py")
    plugin = load_plugin(path)
    assert isinstance(plugin, Plugin)
    assert plugin.name == "example"
    assert [unit.name for unit in plugin.units] == ["example.unit"]


def test_load_plugin_accepts_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_plugin(tmp_path / "example.py")
    monkeypatch.chdir(tmp_path)
    plugin = load_plugin(Path("example.py"))
    assert plugin.name == "example"


def test_load_plugin_does_not_mutate_sys_path(tmp_path: Path) -> None:
    path = _write_plugin(tmp_path / "example.py")
    before = list(sys.path)
    load_plugin(path)
    assert sys.path == before


def test_load_plugin_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PluginFileNotFoundError, match="not found"):
        load_plugin(tmp_path / "missing.py")


def test_load_plugin_directory_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(PluginFileNotFoundError, match="not found"):
        load_plugin(tmp_path)


def test_load_plugin_import_failure_raises(tmp_path: Path) -> None:
    path = _write_plugin(tmp_path / "broken.py", 'raise RuntimeError("boom")\n')
    with pytest.raises(PluginImportError, match="failed to import") as excinfo:
        load_plugin(path)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_load_plugin_missing_descriptor_raises(tmp_path: Path) -> None:
    path = _write_plugin(tmp_path / "nodescriptor.py", "VALUE = 42\n")
    with pytest.raises(MissingPluginDescriptorError, match="defines no PLUGIN"):
        load_plugin(path)


def test_load_plugin_none_descriptor_raises_missing(tmp_path: Path) -> None:
    path = _write_plugin(tmp_path / "none.py", "PLUGIN = None\n")
    with pytest.raises(MissingPluginDescriptorError, match="defines no PLUGIN"):
        load_plugin(path)


def test_load_plugin_invalid_descriptor_raises(tmp_path: Path) -> None:
    path = _write_plugin(tmp_path / "invalid.py", 'PLUGIN = "not-a-plugin"\n')
    with pytest.raises(InvalidPluginDescriptorError, match="not a Plugin instance"):
        load_plugin(path)


def test_load_plugin_same_filename_in_two_directories(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    first_plugin = load_plugin(_write_plugin(first / "example.py"))
    second_plugin = load_plugin(_write_plugin(second / "example.py"))
    assert first_plugin.name == "example"
    assert second_plugin.name == "example"
    assert type(first_plugin.units[0]) is not type(second_plugin.units[0])


def test_plugin_load_errors_share_one_base() -> None:
    for error in (
        PluginFileNotFoundError,
        PluginImportError,
        MissingPluginDescriptorError,
        InvalidPluginDescriptorError,
    ):
        assert issubclass(error, PluginLoadError)
        assert issubclass(error, Exception)
