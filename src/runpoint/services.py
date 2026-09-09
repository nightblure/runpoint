"""Выполняет runtime-операции запуска точки входа."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

from runpoint import data
from runpoint.domain import Runtime

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from runpoint.domain import Entrypoint

DEFAULT_PYTHON_DEBUG_PORT = 5678
DEFAULT_GO_DEBUG_PORT = 2345

_STALE_DEBUG_PROCESS_TERMINATION_TIMEOUT_SECONDS = 2.0
_DEBUG_PORT_RELEASE_TIMEOUT_SECONDS = 2.0
_DEBUG_PORT_POLL_INTERVAL_SECONDS = 0.05
_EXTERNAL_COMMAND_TIMEOUT_SECONDS = 10.0


def resolve_working_directory(*, config_dir: Path, entrypoint: Entrypoint) -> Path:
    """Возвращает существующую рабочую директорию точки входа."""
    working_dir = (config_dir / entrypoint.cwd).resolve()

    if not working_dir.is_dir():
        message = (
            f"Рабочая директория точки входа {entrypoint.alias!r} не найдена: "
            f"{working_dir}"
        )
        raise SystemExit(message)

    return working_dir


def resolve_python_executable(*, working_dir: Path, entrypoint: Entrypoint) -> Path:
    """Находит интерпретатор в виртуальном окружении точки входа."""
    venv_dir = (working_dir / entrypoint.venv).resolve()
    candidates = (
        venv_dir / "bin" / "python",
        venv_dir / "bin" / "python3",
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    message = (
        f"Python виртуального окружения точки входа {entrypoint.alias!r} не найден. "
        f"Проверена директория: {venv_dir}"
    )
    raise SystemExit(message)


def resolve_go_executable() -> Path:
    """Находит Go toolchain через PATH."""
    executable = shutil.which("go")

    if executable is None:
        message = "Исполняемый файл 'go' не найден в PATH. Установите Go."
        raise SystemExit(message)

    return Path(executable).resolve()


def resolve_delve_executable() -> Path:
    """Находит Delve через PATH."""
    executable = shutil.which("dlv")

    if executable is None:
        message = "Исполняемый файл 'dlv' не найден в PATH. Установите Delve."
        raise SystemExit(message)

    return Path(executable).resolve()


def validate_target(*, working_dir: Path, entrypoint: Entrypoint) -> None:
    """Проверяет существование запускаемого Python-скрипта."""
    target = entrypoint.command_args()

    if target[0] == "-m":
        return

    script_path = Path(target[0])
    if not script_path.is_absolute():
        script_path = working_dir / script_path

    if not script_path.is_file():
        message = (
            f"Python-скрипт точки входа {entrypoint.alias!r} не найден: "
            f"{script_path.resolve()}. Для запуска модуля укажите явно: "
            "python -m <module>, например 'python -m pytest'."
        )
        raise SystemExit(message)


def load_env_variables(
    entrypoint: Entrypoint,
    *,
    no_env: bool,
    config_dir: Path,
) -> dict[str, str]:
    """Формирует окружение запускаемой команды."""
    envs: dict[str, str] = {}

    if not no_env and not entrypoint.is_test() and entrypoint.load_env_file:
        dotenv_path = (config_dir / entrypoint.env_file).resolve()
        envs.update(data.dotenv_values(dotenv_path))

    envs.update(os.environ)

    # можно прокидывать словарь с энвами прямо в entrypoint_factory!
    envs.update(entrypoint.env)
    return envs


def ensure_debug_port_is_free(
    *,
    port: int,
    print_debug: Callable[[str], None],
) -> None:
    """Освобождает отладочный порт от зависших процессов debugpy.

    Адаптер debugpy демонизируется (setsid + двойной форк), поэтому переживает
    аварийное завершение отладочной сессии и продолжает держать порт. Такие
    процессы завершаются перед новым запуском. Посторонние процессы не трогаются.
    """
    lsof_executable = shutil.which("lsof")

    if lsof_executable is None:
        return

    listeners = _find_port_listeners(lsof_executable, port)

    if not listeners:
        return

    stale = {
        pid: command
        for pid, command in listeners.items()
        if _is_debugpy_process(command)
    }
    foreign = {pid: command for pid, command in listeners.items() if pid not in stale}

    if foreign:
        details = "; ".join(f"PID {pid}: {cmd}" for pid, cmd in sorted(foreign.items()))
        message = (
            f"Порт {port} занят другим процессом: {details}. Завершите процесс "
            f"вручную или укажите другой порт: --debug-port <PORT>"
        )
        raise SystemExit(message)

    for pid in sorted(stale):
        print_debug(
            f"Порт {port} занят зависшим debugpy (PID {pid}: {stale[pid]}); "
            "завершаю процесс"
        )
        try:
            _terminate_process(pid)
        except PermissionError as error:
            message = (
                f"Нет прав на завершение зависшего debugpy (PID {pid}); "
                "завершите процесс вручную"
            )
            raise SystemExit(message) from error

    if _find_port_listeners(lsof_executable, port):
        message = (
            f"Не удалось освободить порт {port} от зависшего debugpy; "
            "завершите процессы вручную"
        )
        raise SystemExit(message)


def _find_port_listeners(lsof_executable: str, port: int) -> dict[int, str]:
    """Возвращает PID и командную строку процессов, слушающих TCP-порт."""
    lsof_output = _run_command_output(
        [lsof_executable, "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
    )

    if lsof_output is None:
        return {}

    pids = sorted({int(token) for token in lsof_output.split() if token.isdigit()})

    if not pids:
        return {}

    ps_output = _run_command_output(
        ["ps", "-p", ",".join(str(pid) for pid in pids), "-o", "pid=,command="],
    )

    if ps_output is None:
        return {}

    commands: dict[int, str] = {}

    for line in ps_output.splitlines():
        pid_token, separator, command = line.strip().partition(" ")

        if separator and pid_token.isdigit():
            commands[int(pid_token)] = command.strip()

    return {pid: commands[pid] for pid in pids if pid in commands}


def _run_command_output(args: Sequence[str]) -> str | None:
    """Запускает команду без shell и возвращает stdout либо None при сбое."""
    try:
        result = subprocess.run(  # noqa: S603 -- arguments are constant, no shell
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=_EXTERNAL_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    return result.stdout


def _is_debugpy_process(command: str) -> bool:
    """Определяет, принадлежит ли процесс debugpy (адаптер или сервер)."""
    args = command.split()

    if any("debugpy/adapter" in arg or "debugpy\\adapter" in arg for arg in args):
        return True

    return any(
        previous == "-m" and argument == "debugpy"
        for previous, argument in pairwise(args)
    )


def _terminate_process(pid: int) -> None:
    """Завершает процесс: SIGTERM с ожиданием, затем SIGKILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _is_process_alive(pid):
            return

        with suppress(ProcessLookupError):
            os.kill(pid, sig)

        deadline = time.monotonic() + _STALE_DEBUG_PROCESS_TERMINATION_TIMEOUT_SECONDS

        while _is_process_alive(pid) and time.monotonic() < deadline:
            time.sleep(_DEBUG_PORT_POLL_INTERVAL_SECONDS)


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def build_command(  # noqa: PLR0913
    *,
    working_dir: Path,
    entrypoint: Entrypoint,
    target_args: Sequence[str],
    debug: bool,
    debug_port: int,
    no_debug_wait: bool,
    debug_subprocesses: bool,
) -> list[str]:
    """Implement general command build logic."""
    if entrypoint.runtime is Runtime.GO:
        if debug:
            if entrypoint.command_args()[0] == "build":
                message = "Отладка Go-команды 'build' не поддерживается"
                raise SystemExit(message)

            delve_executable = resolve_delve_executable()

            command = build_go_debug_command(
                delve_executable=delve_executable,
                entrypoint=entrypoint,
                target_args=target_args,
                port=debug_port,
                continue_immediately=no_debug_wait,
            )
        else:
            go_executable = resolve_go_executable()
            command = build_go_command(
                go_executable=go_executable,
                entrypoint=entrypoint,
                target_args=target_args,
            )
    else:
        python_executable = resolve_python_executable(
            working_dir=working_dir,
            entrypoint=entrypoint,
        )

        validate_target(working_dir=working_dir, entrypoint=entrypoint)

        if debug_port is None:
            debug_port = DEFAULT_PYTHON_DEBUG_PORT

        command = build_python_command(
            debug=debug,
            port=debug_port,
            entrypoint=entrypoint,
            python_executable=python_executable,
            target_args=target_args,
            wait_for_client=not no_debug_wait,
            debug_subprocesses=debug_subprocesses,
        )

    return command


def build_python_command(  # noqa: PLR0913 -- arguments map directly to CLI options
    *,
    port: int,
    debug: bool,
    python_executable: Path,
    wait_for_client: bool,
    entrypoint: Entrypoint,
    debug_subprocesses: bool,
    target_args: Sequence[str],
) -> list[str]:
    """Формирует команду обычного или отладочного запуска."""
    target = [*entrypoint.command_args(), *target_args]

    if not debug:
        return [str(python_executable), *target]

    command = [
        str(python_executable),
        "-Xfrozen_modules=off",
        "-m",
        "debugpy",
        "--listen",
        f"127.0.0.1:{port}",
    ]

    if wait_for_client:
        command.append("--wait-for-client")

    if debug_subprocesses:
        command.extend(("--configure-subProcess", "True"))
    else:
        command.extend(("--configure-subProcess", "False"))

    command.extend(target)
    return command


def build_go_command(
    *,
    go_executable: Path,
    entrypoint: Entrypoint,
    target_args: Sequence[str],
) -> list[str]:
    """Формирует команду обычного запуска через Go toolchain."""
    return [str(go_executable), *entrypoint.command_args(), *target_args]


def build_go_debug_command(
    *,
    delve_executable: Path,
    entrypoint: Entrypoint,
    target_args: Sequence[str],
    port: int,
    continue_immediately: bool,
) -> list[str]:
    """Формирует команду отладки Go target через Delve."""
    configured_args = entrypoint.command_args()
    operation = configured_args[0]

    if operation == "run":
        delve_command = "debug"
    elif operation == "test":
        delve_command = "test"
    else:
        message = "Go debug поддерживает только команды 'run' и 'test'"
        raise SystemExit(message)

    if operation == "test" and continue_immediately:
        message = (
            "--no-debug-wait не поддерживается для Go-команды 'test': "
            "dlv test не принимает --continue"
        )
        raise SystemExit(message)

    target_count = len(configured_args) - 1
    invalid_target = target_count == 1 and configured_args[1].startswith("-")

    if target_count > 1 or invalid_target or (operation == "run" and target_count != 1):
        message = (
            f"Некорректный target Go-команды {operation!r}; "
            "аргументы target передавайте после '--'"
        )
        raise SystemExit(message)

    if target_count == 1 and (
        "..." in configured_args[1]
        or configured_args[1] in {"all", "std", "cmd", "tool"}
    ):
        message = (
            f"Go debug не поддерживает package-паттерн {configured_args[1]!r}; "
            f"укажите один Go package"
        )
        raise SystemExit(message)

    command = [
        str(delve_executable),
        delve_command,
        "--headless",
        f"--listen=127.0.0.1:{port}",
        "--api-version=2",
        "--accept-multiclient",
    ]

    if continue_immediately:
        command.append("--continue")

    command.extend(configured_args[1:])

    if target_args:
        command.append("--")
        command.extend(target_args)

    return command


def replace_process(
    *,
    working_dir: Path,
    command: list[str],
    environment: dict[str, str],
) -> None:
    """Заменяет текущий процесс настроенной командой."""
    # execvpe does not flush Python buffers. No subprocess is created: on POSIX
    # the current process image is replaced while PID/process group/stdio stay.
    sys.stdout.flush()
    sys.stderr.flush()
    os.chdir(working_dir)
    os.execvpe(command[0], command, environment)  # noqa: S606 -- no shell is used
