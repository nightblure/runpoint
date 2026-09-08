"""Координирует запуск настроенной точки входа."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, NoReturn

from runpoint import services
from runpoint.domain import Runtime

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from runpoint.domain import Entrypoint

DEFAULT_PYTHON_DEBUG_PORT = 5678
DEFAULT_GO_DEBUG_PORT = 2345


def launch_entrypoint(  # noqa: PLR0913 -- parameters are explicit use-case inputs
    *,
    config_dir: Path,
    entrypoint: Entrypoint,
    target_args: Sequence[str],
    debug: bool,
    debug_port: int | None,
    no_debug_wait: bool,
    debug_subprocesses: bool,
    no_env: bool,
    print_message: Callable[[str], None],
    print_debug: Callable[[str], None],
) -> NoReturn:
    """Подготавливает запуск и заменяет текущий процесс точкой входа."""
    working_dir = services.resolve_working_directory(
        config_dir=config_dir,
        entrypoint=entrypoint,
    )
    if entrypoint.runtime is Runtime.GO:
        if debug:
            if entrypoint.command_args()[0] == "build":
                message = "Отладка Go-команды 'build' не поддерживается"
                raise SystemExit(message)

            delve_executable = services.resolve_delve_executable()

            if debug_port is None:
                debug_port = DEFAULT_GO_DEBUG_PORT

            command = services.build_go_debug_command(
                delve_executable=delve_executable,
                entrypoint=entrypoint,
                target_args=target_args,
                port=debug_port,
                continue_immediately=no_debug_wait,
            )
        else:
            go_executable = services.resolve_go_executable()
            command = services.build_go_command(
                go_executable=go_executable,
                entrypoint=entrypoint,
                target_args=target_args,
            )
    else:
        python_executable = services.resolve_python_executable(
            working_dir=working_dir,
            entrypoint=entrypoint,
        )

        services.validate_target(working_dir=working_dir, entrypoint=entrypoint)

        if debug_port is None:
            debug_port = DEFAULT_PYTHON_DEBUG_PORT

        command = services.build_command(
            debug=debug,
            port=debug_port,
            entrypoint=entrypoint,
            python_executable=python_executable,
            target_args=target_args,
            wait_for_client=not no_debug_wait,
            debug_subprocesses=debug_subprocesses,
        )

    print_message(f"working_dir: {working_dir}")

    print_message(f"cmd: {shlex.join(command)}")

    env_variables = services.load_env_variables(
        entrypoint,
        no_env=no_env,
        config_dir=config_dir,
    )

    notice = services.env_loading_notice(
        entrypoint,
        no_env=no_env,
        config_dir=config_dir,
    )
    if notice is not None:
        print_message(notice)

    if entrypoint.runtime is Runtime.PYTHON:
        env_variables.setdefault("PYDEVD_DISABLE_FILE_VALIDATION", "1")

    if debug and entrypoint.runtime is Runtime.GO:
        wait_status = (
            "Ожидание подключения IDE" if not no_debug_wait else "без ожидания IDE"
        )
        print_debug(f"dlv: 127.0.0.1:{debug_port} ({wait_status})")
    elif debug:
        wait_status = (
            "Ожидание подключения IDE" if not no_debug_wait else "без ожидания IDE"
        )
        print_debug(f"debugpy: 127.0.0.1:{debug_port} ({wait_status})")

    services.replace_process(
        working_dir=working_dir,
        command=command,
        environment=env_variables,
    )
