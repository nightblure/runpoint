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
