"""Core discovery and composition tests using isolated plugin directories."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from factory.command import CommandResult
from factory.core_plugins import (
    CorePluginDirectoryError,
    DuplicatePluginError,
    load_core_plugins,
    plugin_units,
)
from factory.jsonrpc import JsonObject
from factory.notifications import NotificationLog
from factory.plugin import Plugin
from factory.plugin_loader import PluginLoadError
from factory.tmux import TmuxRuntime
from factory.work import (
    DuplicateWorkUnitError,
    WorkContext,
    WorkResult,
    WorkRunner,
    WorkSuccess,
)


def _write_plugin(path: Path, name: str, unit: str = "example.unit") -> None:
    path.write_text(
        "from factory.plugin import Plugin\n"
        "from factory.work import WorkSuccess\n"
        "class Unit:\n"
        f"    name = {unit!r}\n"
        "    def run(self, input, context):\n"
        "        return WorkSuccess(input)\n"
        f"PLUGIN = Plugin(name={name!r}, units=(Unit(),))\n",
        encoding="utf-8",
    )


def test_core_plugins_load_in_filename_order(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "z.py", "first-by-name", "z.unit")
    _write_plugin(tmp_path / "a.py", "last-by-name", "a.unit")

    plugins = load_core_plugins(tmp_path)

    assert tuple(plugin.name for plugin in plugins) == ("last-by-name", "first-by-name")
    assert tuple(unit.name for unit in plugin_units(plugins)) == ("a.unit", "z.unit")


def test_core_discovery_ignores_private_non_python_and_nested_files(
    tmp_path: Path,
) -> None:
    for filename in ("__init__.py", "_private.py", "notes.txt"):
        (tmp_path / filename).write_text("raise RuntimeError('must not execute')\n")
    nested = tmp_path / "nested.py"
    nested.mkdir()
    (nested / "child.py").write_text("raise RuntimeError('must not execute')\n")
    _write_plugin(tmp_path / "public.py", "public")

    assert tuple(plugin.name for plugin in load_core_plugins(tmp_path)) == ("public",)


def test_empty_core_directory_returns_no_plugins(tmp_path: Path) -> None:
    assert load_core_plugins(tmp_path) == ()
    assert plugin_units(()) == ()


def test_missing_core_directory_loads_as_empty(tmp_path: Path) -> None:
    # An installed Factory runs without a repository checkout, so a
    # missing core directory means no core plugins, not a startup failure.
    assert load_core_plugins(tmp_path / "missing") == ()


def test_core_path_must_be_a_directory(tmp_path: Path) -> None:
    path = tmp_path / "file.py"
    path.write_text("")
    with pytest.raises(CorePluginDirectoryError):
        load_core_plugins(path)


def test_duplicate_plugin_names_are_rejected(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "a.py", "same", "a.unit")
    _write_plugin(tmp_path / "b.py", "same", "b.unit")
    with pytest.raises(DuplicatePluginError, match="same"):
        load_core_plugins(tmp_path)


def test_loader_errors_are_not_silenced(tmp_path: Path) -> None:
    (tmp_path / "invalid.py").write_text("PLUGIN = object()\n")
    with pytest.raises(PluginLoadError):
        load_core_plugins(tmp_path)


class ExampleUnit:
    name = "example.unit"

    def run(self, input: JsonObject, context: WorkContext) -> WorkResult:
        return WorkSuccess(input)


def test_flattening_preserves_units_without_deduplicating() -> None:
    unit = ExampleUnit()
    plugins = (Plugin("a", (unit,)), Plugin("b", (unit,)))
    assert plugin_units(iter(plugins)) == (unit, unit)


def _runner(plugins: tuple[Plugin, ...]) -> WorkRunner:
    # Discovery and work.list must never invoke the operating-system adapter.
    class UnusedCommandRunner:
        def run(
            self, arguments: Sequence[str], *, input_text: str | None = None
        ) -> CommandResult:
            raise AssertionError("unexpected command")

    runtime = TmuxRuntime.create(UnusedCommandRunner())
    notifications = NotificationLog(Path("unused-notifications.jsonl"), [])
    return WorkRunner.create(runtime, notifications, units=plugin_units(plugins))


def test_loaded_core_units_are_visible_to_work_runner(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "example.py", "example")
    runner = _runner(load_core_plugins(tmp_path))
    response = runner.protocol.handle(b'{"jsonrpc":"2.0","method":"work.list","id":1}')
    assert response is not None
    assert "example.unit" in json.loads(response)["result"]


def test_duplicate_work_units_are_still_rejected_by_work_registry(
    tmp_path: Path,
) -> None:
    _write_plugin(tmp_path / "a.py", "a")
    _write_plugin(tmp_path / "b.py", "b")
    plugins = load_core_plugins(tmp_path)
    assert len(plugins) == 2
    with pytest.raises(DuplicateWorkUnitError):
        _runner(plugins)
