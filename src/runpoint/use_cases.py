"""Координирует запуск настроенной точки входа."""

import shlex
from typing import TYPE_CHECKING, Never

from runpoint import services

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
    debug_port: int,
    no_debug_wait: bool,
    debug_subprocesses: bool,
    no_env: bool,
    print_message: Callable[[str], None],
    print_debug: Callable[[str], None],
) -> Never:
    """Подготавливает запуск и заменяет текущий процесс точкой входа."""
    working_dir = services.resolve_working_directory(
        config_dir=config_dir,
        entrypoint=entrypoint,
    )
    python_executable = services.resolve_python_executable(
        working_dir=working_dir,
        entrypoint=entrypoint,
    )
    services.validate_target(working_dir=working_dir, entrypoint=entrypoint)

    print_message(f"working_dir: {working_dir}")

    command = services.build_command(
        debug=debug,
        port=debug_port,
        entrypoint=entrypoint,
        python_executable=python_executable,
        target_args=target_args,
        wait_for_client=not no_debug_wait,
        debug_subprocesses=debug_subprocesses,
    )

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

    env_variables.setdefault("PYDEVD_DISABLE_FILE_VALIDATION", "1")

    if debug:
        wait_status = (
            "Ожидание подключения IDE" if not no_debug_wait else "без ожидания IDE"
        )
        print_debug(f"debugpy: 127.0.0.1:{debug_port} ({wait_status})")

    services.replace_process(
        working_dir=working_dir,
        command=command,
        environment=env_variables,
    )
