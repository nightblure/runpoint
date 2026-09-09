"""Выполняет runtime-операции запуска точки входа."""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import psutil

from runpoint import data
from runpoint.domain import Runtime

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from runpoint.domain import Entrypoint

DEFAULT_PYTHON_DEBUG_PORT = 5678
DEFAULT_GO_DEBUG_PORT = 2345

_STALE_DEBUG_PROCESS_TERMINATION_TIMEOUT_SECONDS = 2.0
_CHILD_GRACE_SECONDS = 5.0


@runtime_checkable
class StaleDebuggerMatcher(Protocol):
    """Определяет, принадлежит ли cmdline зависшему отладчику на порту."""

    def matches(self, *, cmdline: list[str], port: int) -> bool:
        """Определяет принадлежность процесса зависшему отладчику."""


@dataclasses.dataclass(frozen=True, slots=True)
class DebugpyAdapterMatcher:
    """Совпадает с процессом debugpy-адаптера на точном --port."""

    def matches(self, *, cmdline: list[str], port: int) -> bool:
        """Совпадает, когда cmdline — debugpy-адаптер с точным --port."""
        if not any(_is_debugpy_adapter_path(argument) for argument in cmdline):
            return False

        if "--for-server" not in cmdline:
            return False

        return _has_exact_option(args=cmdline, option="--port", value=str(port))


@dataclasses.dataclass(frozen=True, slots=True)
class DelveMatcher:
    """Совпадает с headless-сервером Delve на точном --listen-порту."""

    def matches(self, *, cmdline: list[str], port: int) -> bool:
        """Совпадает, когда cmdline — dlv/delve с точным --listen-портом."""
        if not cmdline:
            return False

        if Path(cmdline[0]).name not in {"dlv", "delve"}:
            return False

        return _has_listen_port(args=cmdline, port=port)


_STALE_DEBUGGER_MATCHERS: dict[Runtime, tuple[StaleDebuggerMatcher, ...]] = {
    Runtime.PYTHON: (DebugpyAdapterMatcher(),),
    Runtime.GO: (DelveMatcher(),),
}


def debugger_matchers(runtime: Runtime) -> tuple[StaleDebuggerMatcher, ...]:
    """Возвращает матчеры зависших отладчиков для runtime.

    Точка расширения (open-closed): новый отладчик = новый matcher-класс
    и запись в реестре, ядро очистки не меняется.
    """
    return _STALE_DEBUGGER_MATCHERS[runtime]


def _has_listen_port(*, args: list[str], port: int) -> bool:
    target = str(port)

    for index, argument in enumerate(args):
        value: str | None = None
        if argument == "--listen" and index + 1 < len(args):
            value = args[index + 1]
        elif argument.startswith("--listen="):
            value = argument[len("--listen=") :]

        if value is not None and _port_value(value) == target:
            return True

    return False


def _port_value(address: str) -> str:
    """Возвращает порт из host:port, :port или port."""
    return address.rsplit(":", maxsplit=1)[-1]


def _is_debugpy_adapter_path(argument: str) -> bool:
    parts = PurePosixPath(argument.replace("\\", "/")).parts
    return parts[-2:] == ("debugpy", "adapter") or parts[-3:] == (
        "debugpy",
        "adapter",
        "__main__.py",
    )


def _has_exact_option(*, args: list[str], option: str, value: str) -> bool:
    return any(
        (argument == option and index + 1 < len(args) and args[index + 1] == value)
        or argument == f"{option}={value}"
        for index, argument in enumerate(args)
    )


def cleanup_stale_debuggers(
    *,
    port: int,
    matchers: Sequence[StaleDebuggerMatcher],
    print_debug: Callable[[str], None],
) -> None:
    """Завершает зависшие процессы отладчика на порту через psutil.

    Универсальна и не знает о конкретных отладчиках: решение о принадлежности
    процесса отдаётся матчерам. Точка расширения — новые matcher-классы.
    """
    for process in psutil.process_iter():
        _terminate_if_stale_debugger(
            process=process,
            port=port,
            matchers=matchers,
            print_debug=print_debug,
        )


def _terminate_if_stale_debugger(
    *,
    process: psutil.Process,
    port: int,
    matchers: Sequence[StaleDebuggerMatcher],
    print_debug: Callable[[str], None],
) -> None:
    try:
        cmdline = process.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return

    if not any(matcher.matches(cmdline=cmdline, port=port) for matcher in matchers):
        return

    _terminate_debugger_process(process=process, port=port, print_debug=print_debug)


def _terminate_debugger_process(
    *,
    process: psutil.Process,
    port: int,
    print_debug: Callable[[str], None],
) -> None:
    try:
        pid = process.pid
    except psutil.NoSuchProcess:
        return

    print_debug(f"Порт {port} занят зависшим процессом отладчика (PID {pid}); завершаю")

    try:
        process.terminate()
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied:
        print_debug(
            f"Порт {port}: нет прав на завершение PID {pid}; освободите порт вручную"
        )
        return

    if _wait_process(process=process):
        return

    try:
        process.kill()
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied:
        print_debug(
            f"Порт {port}: нет прав на завершение PID {pid}; освободите порт вручную"
        )
        return

    if not _wait_process(process=process):
        print_debug(
            f"Порт {port}: процесс PID {pid} не завершился после SIGKILL; "
            "освободите порт вручную"
        )


def _wait_process(*, process: psutil.Process) -> bool:
    """Возвращает True, если процесс завершился, и False — если жив после ожидания."""
    try:
        process.wait(timeout=_STALE_DEBUG_PROCESS_TERMINATION_TIMEOUT_SECONDS)
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True
    else:
        return True


def _dlv_output_path(port: int) -> Path:
    return Path(tempfile.gettempdir()) / f"runpoint-dlv-{port}"


def cleanup_stale_debug_binary(*, port: int) -> None:
    """Удаляет оставшийся dlv-бинарник из прошлой отладочной сессии."""
    _dlv_output_path(port).unlink(missing_ok=True)


def run_python_debug(  # noqa: PLR0913
    *,
    command: list[str],
    working_dir: Path,
    environment: dict[str, str],
    port: int,
    matchers: Sequence[StaleDebuggerMatcher],
    print_debug: Callable[[str], None],
) -> int:
    """Запускает Python-отладку дочерним процессом с pre/finally очисткой адаптера.

    Child остаётся в одной foreground process group с Runpoint, поэтому
    терминальный Ctrl-C доходит до target; Runpoint ждёт завершения child и
    возвращает 130 по SIGINT.
    """
    cleanup_stale_debuggers(port=port, matchers=matchers, print_debug=print_debug)
    process = subprocess.Popen(  # noqa: S603 -- command is built explicitly, no shell
        command,
        cwd=str(working_dir),
        env=environment,
    )
    try:
        process.wait()
    except KeyboardInterrupt:
        _terminate_child_process(process)
    finally:
        cleanup_stale_debuggers(port=port, matchers=matchers, print_debug=print_debug)

    return _resolve_exit_code(process.returncode)


def _terminate_child_process(process: subprocess.Popen[bytes]) -> None:
    """Bounded shutdown после Ctrl-C: SIGTERM→SIGKILL; повторный Ctrl-C→SIGKILL."""
    try:
        if _wait_child_exit(process):
            return
        try:
            process.terminate()
        except (ProcessLookupError, PermissionError):
            return
        if _wait_child_exit(process):
            return
    except KeyboardInterrupt:
        pass
    try:
        process.kill()
    except (ProcessLookupError, PermissionError):
        return
    with suppress(KeyboardInterrupt):
        _wait_child_exit(process)


def _wait_child_exit(
    process: subprocess.Popen[bytes],
    *,
    timeout: float = _CHILD_GRACE_SECONDS,
) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _resolve_exit_code(returncode: int | None) -> int:
    if returncode is None:
        return 1
    if returncode < 0:
        return 128 + (-returncode)
    return returncode


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
    dotenv_path = _selected_dotenv_path(
        entrypoint=entrypoint,
        no_env=no_env,
        config_dir=config_dir,
    )

    if dotenv_path is not None:
        envs.update(data.dotenv_values(dotenv_path))

    envs.update(os.environ)

    # можно прокидывать словарь с энвами прямо в entrypoint_factory!
    envs.update(entrypoint.env)
    return envs


def env_loading_notice(
    entrypoint: Entrypoint,
    *,
    no_env: bool,
    config_dir: Path,
) -> str | None:
    """Описывает единственный фактический результат выбора .env."""
    if no_env:
        return "Загрузка .env пропущена: указан флаг --no-env"
    if entrypoint.is_test():
        return "Загрузка .env пропущена: обнаружен запуск тестов"

    dotenv_path = _selected_dotenv_path(
        entrypoint=entrypoint,
        no_env=no_env,
        config_dir=config_dir,
    )
    if dotenv_path is not None:
        return f"Переменные окружения загружены из .env-файла {dotenv_path}"
    return None


def _selected_dotenv_path(
    *,
    entrypoint: Entrypoint,
    no_env: bool,
    config_dir: Path,
) -> Path | None:
    if no_env or entrypoint.is_test() or not entrypoint.load_env_file:
        return None
    return (config_dir / entrypoint.env_file).resolve()


def build_command(
    *,
    working_dir: Path,
    entrypoint: Entrypoint,
    target_args: Sequence[str],
    debug: bool,
    debug_port: int,
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

        command = build_python_command(
            debug=debug,
            port=debug_port,
            entrypoint=entrypoint,
            python_executable=python_executable,
            target_args=target_args,
        )

    return command


def build_python_command(
    *,
    port: int,
    debug: bool,
    python_executable: Path,
    entrypoint: Entrypoint,
    target_args: Sequence[str],
) -> list[str]:
    """Формирует команду обычного или отладочного запуска."""
    target = [*entrypoint.command_args(), *target_args]

    if not debug:
        return [str(python_executable), *target]

    return [
        str(python_executable),
        "-Xfrozen_modules=off",
        "-m",
        "debugpy",
        "--listen",
        f"127.0.0.1:{port}",
        "--wait-for-client",
        "--configure-subProcess",
        "True",
        *target,
    ]


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
) -> list[str]:
    """Формирует команду отладки Go target через single-client Delve."""
    configured_args = entrypoint.command_args()
    operation = configured_args[0]

    if operation == "run":
        delve_command = "debug"
    elif operation == "test":
        delve_command = "test"
    else:
        message = "Go debug поддерживает только команды 'run' и 'test'"
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
        "--output",
        str(_dlv_output_path(port)),
    ]

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
