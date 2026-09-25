"""Behavior tests for loading Factory plugins from Python files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from factory.jsonrpc import JsonObject
from factory.plugin import InvalidPluginError, Plugin
from factory.plugin_config import PluginConfig
from factory.plugin_loader import (
    AmbiguousPluginError,
    InvalidPluginDescriptorError,
    MissingPluginDescriptorError,
    PluginFileNotFoundError,
    PluginImportError,
    PluginLoadError,
    PluginNotFoundError,
    collect_startup_units,
    load_enabled_plugins,
    load_plugin,
    resolve_plugin,
)
from factory.work import WorkContext, WorkResult, WorkUnit

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


def _write_named_plugin(
    directory: Path, name: str, declared_name: str | None = None
) -> None:
    (directory / f"{name}.py").write_text(
        "from factory.plugin import Plugin\n"
        f'PLUGIN = Plugin(name="{declared_name or name}", units=())\n',
        encoding="utf-8",
    )


def _write_poison(directory: Path, name: str) -> None:
    (directory / f"{name}.py").write_text(
        'raise RuntimeError("this module must never be imported")\n',
        encoding="utf-8",
    )


def _config(*enabled: str, paths: tuple[Path, ...]) -> PluginConfig:
    return PluginConfig(enabled=enabled, paths=paths)


def test_loads_enabled_plugin_from_configured_path(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    _write_named_plugin(user, "foo")
    _write_poison(user, "bar")  # unrelated file must not be imported
    plugins = load_enabled_plugins(_config("foo", paths=(user,)), builtin_dir=builtin)
    assert [plugin.name for plugin in plugins] == ["foo"]


def test_resolves_builtin_plugin(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    _write_named_plugin(builtin, "github")
    plugins = load_enabled_plugins(_config("github", paths=()), builtin_dir=builtin)
    assert [plugin.name for plugin in plugins] == ["github"]


def test_preserves_enabled_order(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    _write_named_plugin(user, "aaa")
    _write_named_plugin(user, "bbb")
    plugins = load_enabled_plugins(
        _config("bbb", "aaa", paths=(user,)), builtin_dir=builtin
    )
    assert [plugin.name for plugin in plugins] == ["bbb", "aaa"]


def test_empty_config_loads_nothing(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    assert load_enabled_plugins(_config(paths=()), builtin_dir=builtin) == ()


def test_missing_plugin_raises_not_found(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    with pytest.raises(PluginNotFoundError, match="nope"):
        load_enabled_plugins(_config("nope", paths=()), builtin_dir=builtin)


def test_builtin_and_user_clash_is_ambiguous(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    _write_named_plugin(builtin, "foo")
    _write_named_plugin(user, "foo")
    with pytest.raises(AmbiguousPluginError, match="ambiguous"):
        load_enabled_plugins(_config("foo", paths=(user,)), builtin_dir=builtin)


def test_declared_name_must_match_file_name(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    _write_named_plugin(builtin, "foo", declared_name="other")
    with pytest.raises(InvalidPluginError, match="other"):
        resolve_plugin("foo", (builtin,))


def test_plugin_file_without_plugin_value_raises(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "foo.py").write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(InvalidPluginError, match="PLUGIN"):
        resolve_plugin("foo", (builtin,))


def test_disabled_plugins_are_never_imported(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    _write_named_plugin(user, "foo")
    _write_poison(user, "evil")
    plugins = load_enabled_plugins(_config("foo", paths=(user,)), builtin_dir=builtin)
    assert [plugin.name for plugin in plugins] == ["foo"]


class _StubUnit:
    def __init__(self, unit_name: str) -> None:
        self._unit_name = unit_name

    @property
    def name(self) -> str:
        return self._unit_name

    def run(self, input: JsonObject, context: WorkContext) -> WorkResult:
        raise NotImplementedError


def _write_plugin_with_unit(directory: Path, name: str, unit_name: str) -> None:
    (directory / f"{name}.py").write_text(
        "from factory.jsonrpc import JsonObject\n"
        "from factory.plugin import Plugin\n"
        "from factory.work import WorkContext, WorkResult\n"
        "\n"
        "class _Unit:\n"
        "    @property\n"
        "    def name(self) -> str:\n"
        f'        return "{unit_name}"\n'
        "    def run(self, input: JsonObject, context: WorkContext) -> WorkResult:\n"
        "        raise NotImplementedError\n"
        "\n"
        f'PLUGIN = Plugin(name="{name}", units=(_Unit(),))\n',
        encoding="utf-8",
    )


def _plugin_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    core = tmp_path / "core"
    core.mkdir()
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    return core, builtin, user


def test_startup_loads_core_but_not_unenabled_builtin(tmp_path: Path) -> None:
    core, builtin, user = _plugin_layout(tmp_path)
    _write_plugin_with_unit(core, "tmux", "tmux.unit")
    _write_plugin_with_unit(builtin, "github", "github.unit")
    units = collect_startup_units(
        _config(paths=(user,)), core_dir=core, builtin_dir=builtin
    )
    assert [unit.name for unit in units] == ["tmux.unit"]


def test_startup_loads_enabled_builtin_and_user_plugins_in_order(
    tmp_path: Path,
) -> None:
    core, builtin, user = _plugin_layout(tmp_path)
    _write_plugin_with_unit(core, "tmux", "tmux.unit")
    _write_plugin_with_unit(builtin, "github", "github.unit")
    _write_plugin_with_unit(user, "mine", "mine.unit")
    extra = _StubUnit("entrypoint.unit")
    assert isinstance(extra, WorkUnit)
    units = collect_startup_units(
        _config("github", "mine", paths=(user,)),
        core_dir=core,
        builtin_dir=builtin,
        extra_units=(extra,),
    )
    assert [unit.name for unit in units] == [
        "tmux.unit",
        "github.unit",
        "mine.unit",
        "entrypoint.unit",
    ]
