from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from runpoint import services
from runpoint.domain import Runtime

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from runpoint.domain import Entrypoint


def launch_entrypoint(  # noqa: PLR0913
    *,
    debug: bool,
    no_env: bool,
    config_dir: Path,
    debug_port: int | None,
    entrypoint: Entrypoint,
    target_args: Sequence[str],
    print_message: Callable[[str], None],
    print_debug: Callable[[str], None],
) -> int | None:
    """Подготавливает и запускает точку входа.

    Обычный запуск и Go-отладка заменяют текущий процесс (execvpe) и не
    возвращают управление. Python-отладка запускается дочерним процессом и
    возвращает exit code (130 по SIGINT).
    """
    working_dir = services.resolve_working_directory(
        config_dir=config_dir,
        entrypoint=entrypoint,
    )

    debug_port = _resolve_debug_port(entrypoint.runtime, debug_port)

    command = services.build_command(
        entrypoint=entrypoint,
        debug=debug,
        debug_port=debug_port,
        working_dir=working_dir,
        target_args=target_args,
    )

    print_message(f"working_dir: {working_dir}")
    print_message(f"cmd: {shlex.join(command)}")

    env_variables = services.load_env_variables(
        entrypoint,
        no_env=no_env,
        config_dir=config_dir,
    )

    env_notice = services.env_loading_notice(
        entrypoint,
        no_env=no_env,
        config_dir=config_dir,
    )
    if env_notice is not None:
        print_message(env_notice)

    if entrypoint.runtime is Runtime.PYTHON:
        env_variables.setdefault("PYDEVD_DISABLE_FILE_VALIDATION", "1")

    if not debug:
        services.replace_process(
            working_dir=working_dir,
            command=command,
            environment=env_variables,
        )
        return None  # unreachable: execvpe replaces the process

    matchers = services.debugger_matchers(entrypoint.runtime)

    if entrypoint.runtime is Runtime.GO:
        return services.run_go_debug(
            command=command,
            working_dir=working_dir,
            environment=env_variables,
            port=debug_port,
            matchers=matchers,
            print_debug=print_debug,
        )

    print_debug(f"debugpy: 127.0.0.1:{debug_port}")
    return services.run_python_debug(
        command=command,
        working_dir=working_dir,
        environment=env_variables,
        port=debug_port,
        matchers=matchers,
        print_debug=print_debug,
    )


def _resolve_debug_port(runtime: Runtime, debug_port: int | None) -> int:
    if debug_port is not None:
        return debug_port

    if runtime is Runtime.GO:
        return services.DEFAULT_GO_DEBUG_PORT

    if runtime is Runtime.PYTHON:
        return services.DEFAULT_PYTHON_DEBUG_PORT

    msg = "Порт отладки не определён"
    raise NotImplementedError(msg)
