"""Behavior tests for resolving an optional plugin by name."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.resolve_plugin import (
    AmbiguousPluginError,
    PluginNameMismatchError,
    PluginNotFoundError,
    ResolvePluginError,
    resolve_plugin,
)


def _write_plugin(directory: Path, filename: str, descriptor_name: str) -> None:
    directory.joinpath(filename).write_text(
        "from factory.plugin import Plugin\n"
        f'PLUGIN = Plugin(name="{descriptor_name}", units=())\n',
        encoding="utf-8",
    )


def test_resolve_plugin_happy_path(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "github.py", "github")
    plugin = resolve_plugin("github", (tmp_path,))
    assert plugin.name == "github"
    assert plugin.units == ()


def test_resolve_plugin_zero_matches_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(PluginNotFoundError, match='plugin "github" not found'):
        resolve_plugin("github", (tmp_path,))


def test_resolve_plugin_ambiguous_matches_fail_clearly(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_plugin(first, "github.py", "github")
    _write_plugin(second, "github.py", "github")
    with pytest.raises(AmbiguousPluginError, match="more than one search path"):
        resolve_plugin("github", (first, second))


def test_resolve_plugin_rejects_descriptor_name_mismatch(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "github.py", "gitlab")
    with pytest.raises(PluginNameMismatchError, match='named "gitlab"'):
        resolve_plugin("github", (tmp_path,))


def test_resolve_plugin_does_not_import_unrelated_files(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "github.py", "github")
    tmp_path.joinpath("poison.py").write_text(
        'raise RuntimeError("this file must never be imported")\n', encoding="utf-8"
    )
    plugin = resolve_plugin("github", (tmp_path,))
    assert plugin.name == "github"


def test_resolve_plugin_does_not_search_recursively(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    _write_plugin(nested, "github.py", "github")
    with pytest.raises(PluginNotFoundError, match='plugin "github" not found'):
        resolve_plugin("github", (tmp_path,))


def test_errors_share_a_common_base() -> None:
    for error in (PluginNotFoundError, AmbiguousPluginError, PluginNameMismatchError):
        assert issubclass(error, ResolvePluginError)
