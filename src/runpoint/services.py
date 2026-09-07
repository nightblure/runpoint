"""Выполняет runtime-операции запуска точки входа."""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Never

from runpoint import data

if TYPE_CHECKING:
    from collections.abc import Sequence

    from runpoint.domain import Entrypoint


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
            f"{script_path.resolve()}. Для запуска модуля используйте '-m <module>', "
            "например '-m pytest'."
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


def env_loading_notice(
    entrypoint: Entrypoint,
    *,
    no_env: bool,
    config_dir: Path,
) -> str | None:
    """Возвращает уведомление о загрузке окружения, если оно требуется."""
    if no_env:
        return "Загрузка энвов пропущена из-за флага --no-env"

    if entrypoint.is_test():
        return "Загрузка энвов пропущена: обнаружен запуск тестов"

    if entrypoint.load_env_file:
        dotenv_path = (config_dir / entrypoint.env_file).resolve()
        return f"Переменные окружения загружены из {dotenv_path}"

    return None


def build_command(  # noqa: PLR0913 -- arguments map directly to CLI options
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


def replace_process(
    *,
    working_dir: Path,
    command: list[str],
    environment: dict[str, str],
) -> Never:
    """Заменяет текущий процесс настроенной Python-командой."""
    # execvpe does not flush Python buffers. No subprocess is created: on POSIX
    # the current process image is replaced while PID/process group/stdio stay.
    sys.stdout.flush()
    sys.stderr.flush()
    os.chdir(working_dir)
    os.execvpe(command[0], command, environment)  # noqa: S606 -- no shell is used
