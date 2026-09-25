"""Factory setup, server, and remote-control command line."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import sys
from pathlib import Path
from types import FrameType

from factory.client import ClientConfig, FactoryClient, FactoryClientError
from factory.command import SubprocessRunner
from factory.core_plugins import load_core_plugins, plugin_units
from factory.jsonrpc import JsonObject, JsonValue
from factory.notifications import NotificationLog
from factory.plugin import PluginError
from factory.plugin_loader import PluginLoadError
from factory.setup import (
    FactorySetup,
    SetupConfig,
    SetupEnvironment,
    SetupFailure,
)
from factory.tcp_server import SslTcpServer, TcpServerError, create_server_context
from factory.tmux import OperationFailure, TmuxRuntime
from factory.work import WorkError, WorkRunner, WorkUnit, load_work_units


def main() -> None:
    """Run the requested Factory command."""
    args = _parser().parse_args()
    try:
        exit_code = _run(args)
    except KeyboardInterrupt:
        exit_code = 130
    except (FactoryClientError, TcpServerError, WorkError, OSError) as error:
        print(f"factory: {error}", file=sys.stderr)
        exit_code = 1
    if exit_code:
        raise SystemExit(exit_code)


def _run(args: argparse.Namespace) -> int:
    if args.command == "setup":
        return _setup(args)
    if args.command == "start":
        return _start(args)

    client = FactoryClient(_client_config(args))
    if args.command == "state":
        _print_json(_run_work(client, "factory.state", {}))
    elif args.command == "create":
        _print_json(_run_work(client, "channel.create", {"name": args.name}))
    elif args.command == "send":
        _print_json(
            _run_work(
                client,
                "mailbox.send",
                {"channel": args.channel, "message": args.message},
            )
        )
    elif args.command == "read":
        result = _run_work(
            client,
            "mailbox.read",
            {"channel": args.channel, "lines": args.lines},
        )
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            print(result["content"], end="")
        else:
            _print_json(result)
    elif args.command == "notifications":
        if args.follow:
            for event in client.notifications(args.after):
                _print_json(event, compact=True)
        else:
            _print_json(_run_work(client, "notification.list", {"after": args.after}))
    return 0


def _setup(args: argparse.Namespace) -> int:
    environment = SetupEnvironment.detect()
    if isinstance(environment, SetupFailure):
        print(f"factory setup: {environment.message}", file=sys.stderr)
        return 1
    result = FactorySetup(SubprocessRunner(), environment).run(
        SetupConfig(
            host=args.host,
            port=args.port,
            session=args.session,
            server_name=args.server_name,
        )
    )
    if isinstance(result, SetupFailure):
        print(f"factory setup: {result.message}", file=sys.stderr)
        return 1
    print(f"Factory is running as {result.service}.")
    print(f"Copy this certificate to clients: {result.certificate}")
    return 0


def _start(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    runtime = TmuxRuntime.create(SubprocessRunner(timeout=10), args.session)
    notifications = NotificationLog.open(args.notifications)
    try:
        units = _startup_work_units()
    except (PluginError, PluginLoadError) as error:
        print(f"factory start: cannot load core plugin: {error}", file=sys.stderr)
        return 1
    runner = WorkRunner.create(runtime, notifications, units=units)
    server = SslTcpServer.create(
        context=create_server_context(args.certificate, args.private_key),
        protocol=runner.protocol,
        host=args.host,
        port=args.port,
    )
    started = runner.start()
    if isinstance(started, OperationFailure):
        print(f"factory start: {started.message}", file=sys.stderr)
        return 1

    def stop(signum: int, frame: FrameType | None) -> None:
        server.close()

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.close()
    finally:
        signal.signal(signal.SIGTERM, previous)
        runner.close()
        server.close()
    return 0


def _startup_work_units() -> tuple[WorkUnit, ...]:
    """Collect core plugin units plus factory.plugins entry-point units."""
    return plugin_units(load_core_plugins(Path("plugins/core"))) + tuple(
        load_work_units()
    )


def _client_config(args: argparse.Namespace) -> ClientConfig:
    return ClientConfig.create(
        host=args.host,
        port=args.port,
        certificate=args.certificate,
        server_name=args.server_name,
    )


def _run_work(client: FactoryClient, unit: str, input: JsonObject) -> JsonValue:
    return client.call("work.run", {"unit": unit, "input": input})


def _print_json(value: JsonValue, *, compact: bool = False) -> None:
    separators = (",", ":") if compact else None
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=separators,
        ),
        flush=compact,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Programmable, tmux-backed agent runtime",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="install tools and enable autostart")
    setup.add_argument("--host", default="127.0.0.1")
    setup.add_argument("--port", type=int, default=8443)
    setup.add_argument("--session", default="factory")
    setup.add_argument("--server-name", default=socket.gethostname())

    start = commands.add_parser("start", help="run the Factory server")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8443)
    start.add_argument("--session", default="factory")
    start.add_argument("--certificate", type=Path, default=Path(".keys/cert.pem"))
    start.add_argument(
        "--private-key", type=Path, default=Path(".keys/private_key.pem")
    )
    start.add_argument(
        "--notifications",
        type=Path,
        default=Path(".factory/notifications.jsonl"),
    )

    state = commands.add_parser("state", help="inspect live channels and processes")
    _add_client_options(state)

    create = commands.add_parser("create", help="create a tmux-window channel")
    create.add_argument("name")
    _add_client_options(create)

    send = commands.add_parser("send", help="send a message to a channel")
    send.add_argument("channel")
    send.add_argument("message")
    _add_client_options(send)

    read = commands.add_parser("read", help="read recent channel output")
    read.add_argument("channel")
    read.add_argument("--lines", type=int, default=200)
    _add_client_options(read)

    notifications = commands.add_parser(
        "notifications",
        help="read durable events or follow live notifications",
    )
    notifications.add_argument("--after", type=int, default=0)
    notifications.add_argument("--follow", action="store_true")
    _add_client_options(notifications)
    return parser


def _add_client_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--certificate", type=Path, default=Path(".keys/cert.pem"))
    parser.add_argument("--server-name")
