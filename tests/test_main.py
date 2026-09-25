"""Tests for the Factory command-line routing."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest


def test_parser_requires_a_command() -> None:
    main_module = importlib.import_module("factory.main")

    with pytest.raises(SystemExit):
        main_module._parser().parse_args([])


def test_state_command_uses_remote_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_module = importlib.import_module("factory.main")
    calls: list[tuple[str, object]] = []

    class Client:
        def __init__(self, config: object) -> None:
            return None

        def call(self, method: str, params: object = None) -> object:
            calls.append((method, params))
            return {"channel_count": 0}

    monkeypatch.setattr(main_module, "FactoryClient", Client)
    args = argparse.Namespace(
        command="state",
        host="localhost",
        port=8443,
        certificate=Path("certificate.pem"),
        server_name=None,
    )

    assert main_module._run(args) == 0
    assert calls == [("work.run", {"unit": "factory.state", "input": {}})]
    assert '"channel_count": 0' in capsys.readouterr().out


def test_send_command_maps_to_mailbox_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_module = importlib.import_module("factory.main")
    calls: list[tuple[str, object]] = []

    class Client:
        def __init__(self, config: object) -> None:
            return None

        def call(self, method: str, params: object = None) -> object:
            calls.append((method, params))
            return {"channel": "@1"}

    monkeypatch.setattr(main_module, "FactoryClient", Client)
    args = argparse.Namespace(
        command="send",
        host="localhost",
        port=8443,
        certificate=Path("certificate.pem"),
        server_name=None,
        channel="@1",
        message="fix the tests",
    )

    assert main_module._run(args) == 0
    assert calls == [
        (
            "work.run",
            {
                "unit": "mailbox.send",
                "input": {"channel": "@1", "message": "fix the tests"},
            },
        )
    ]


def test_main_handles_keyboard_interrupt_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_module = importlib.import_module("factory.main")

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return argparse.Namespace(command="notifications")

    def interrupt(args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "_parser", Parser)
    monkeypatch.setattr(main_module, "_run", interrupt)

    with pytest.raises(SystemExit, match="130"):
        main_module.main()


def _example_plugin_source() -> str:
    return (
        "from factory.plugin import Plugin\n"
        "from factory.jsonrpc import JsonObject\n"
        "from factory.work import WorkContext, WorkResult, WorkSuccess\n"
        "class ExampleUnit:\n"
        "    name = 'example.hello'\n"
        "    def run(self, input: JsonObject, context: WorkContext) -> WorkResult:\n"
        "        return WorkSuccess({})\n"
        "PLUGIN = Plugin(name='example', units=(ExampleUnit(),))\n"
    )


def _patch_start_collaborators(
    monkeypatch: pytest.MonkeyPatch,
    main_module: object,
    captured: dict[str, object],
) -> None:
    tmux_module = importlib.import_module("factory.tmux")

    class FakeRuntime:
        @classmethod
        def create(cls, *args: object, **kwargs: object) -> object:
            return object()

    class FakeNotifications:
        @classmethod
        def open(cls, *args: object, **kwargs: object) -> object:
            return object()

    class FakeRunner:
        def __init__(self, units: tuple[object, ...]) -> None:
            captured["units"] = units

        def start(self) -> object:
            return tmux_module.OperationSuccess(None)

        def close(self) -> None:
            return None

        @property
        def protocol(self) -> object:
            return None

    class FakeWorkRunner:
        @classmethod
        def create(
            cls,
            runtime: object,
            notifications: object,
            *,
            units: tuple[object, ...] = (),
        ) -> FakeRunner:
            return FakeRunner(units)

    class FakeServer:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def close(self) -> None:
            return None

    class FakeTcpServer:
        @classmethod
        def create(cls, *args: object, **kwargs: object) -> FakeServer:
            return FakeServer()

    monkeypatch.setattr(main_module, "TmuxRuntime", FakeRuntime)
    monkeypatch.setattr(main_module, "NotificationLog", FakeNotifications)
    monkeypatch.setattr(main_module, "WorkRunner", FakeWorkRunner)
    monkeypatch.setattr(main_module, "SslTcpServer", FakeTcpServer)
    monkeypatch.setattr(main_module, "create_server_context", lambda *args: object())


def _start_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command="start",
        host="127.0.0.1",
        port=8443,
        session="factory",
        certificate=tmp_path / "cert.pem",
        private_key=tmp_path / "key.pem",
        notifications=tmp_path / "notifications.jsonl",
    )


def test_start_combines_core_and_entry_point_units(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_module = importlib.import_module("factory.main")
    core = tmp_path / "plugins" / "core"
    core.mkdir(parents=True)
    (core / "example.py").write_text(_example_plugin_source(), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class EntryPointUnit:
        name = "entry.point"

    monkeypatch.setattr(main_module, "load_work_units", lambda: (EntryPointUnit(),))
    captured: dict[str, object] = {}
    _patch_start_collaborators(monkeypatch, main_module, captured)

    assert main_module._start(_start_args(tmp_path)) == 0
    names = [unit.name for unit in captured["units"]]  # type: ignore[union-attr]
    assert names == ["example.hello", "entry.point"]


def test_start_fails_clearly_when_core_plugin_broken(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_module = importlib.import_module("factory.main")
    core = tmp_path / "plugins" / "core"
    core.mkdir(parents=True)
    (core / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}
    _patch_start_collaborators(monkeypatch, main_module, captured)

    assert main_module._start(_start_args(tmp_path)) == 1
    assert "factory start: cannot load core plugin" in capsys.readouterr().err
    assert captured == {}
