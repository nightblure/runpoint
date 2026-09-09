"""Координирует запуск настроенной точки входа."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from runpoint import services
from runpoint.domain import Runtime

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from runpoint.domain import Entrypoint


def launch_entrypoint(  # noqa: PLR0913, C901
    *,
    debug: bool,
    no_env: bool,
    config_dir: Path,
    no_debug_wait: bool,
    debug_port: int | None,
    entrypoint: Entrypoint,
    debug_subprocesses: bool,
    target_args: Sequence[str],
    print_message: Callable[[str], None],
    print_debug: Callable[[str], None],
) -> None:
    """Подготавливает запуск и заменяет текущий процесс точкой входа."""
    working_dir = services.resolve_working_directory(
        config_dir=config_dir,
        entrypoint=entrypoint,
    )

    if debug_port is None:
        if entrypoint.runtime is Runtime.GO:
            debug_port = services.DEFAULT_GO_DEBUG_PORT
        elif entrypoint.runtime is Runtime.PYTHON:
            debug_port = services.DEFAULT_PYTHON_DEBUG_PORT

    if debug_port is None:
        msg = "debug port is None"
        raise SystemExit(msg)

    command = services.build_command(
        entrypoint=entrypoint,
        debug=debug,
        debug_port=debug_port,
        working_dir=working_dir,
        debug_subprocesses=debug_subprocesses,
        no_debug_wait=no_debug_wait,
        target_args=target_args,
    )

    print_message(f"working_dir: {working_dir}")

    print_message(f"cmd: {shlex.join(command)}")

    env_variables = services.load_env_variables(
        entrypoint,
        no_env=no_env,
        config_dir=config_dir,
    )

    if no_env:
        print_message("Загрузка энвов пропущена из-за флага --no-env")

    if entrypoint.is_test():
        print_message("Загрузка энвов пропущена: обнаружен запуск тестов")

    if entrypoint.load_env_file:
        dotenv_path = (config_dir / entrypoint.env_file).resolve()
        print_message(f"Переменные окружения загружены из {dotenv_path}")

    if entrypoint.runtime is Runtime.PYTHON:
        env_variables.setdefault("PYDEVD_DISABLE_FILE_VALIDATION", "1")

    if debug:
        services.ensure_debug_port_is_free(
            port=debug_port,
            print_debug=print_debug,
        )

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
