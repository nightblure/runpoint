"""Определяет доменные правила точек входа runpoint."""

import dataclasses
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

KNOWN_TEST_RUNNERS = ("test", "pytest", "unittest", "tox", "nox")


@dataclasses.dataclass(frozen=False, slots=True, kw_only=True)
class Entrypoint:
    """Описывает настраиваемую точку входа Python."""

    alias: str
    command: str
    load_env_file: bool
    cwd: Path = Path()
    venv: Path = Path(".venv")
    env_file: Path = Path(".env")
    env: Mapping[str, str] = dataclasses.field(default_factory=dict)
    _command_args: tuple[str, ...] = dataclasses.field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Разбирает и проверяет команду после инициализации."""
        self._command_args = self._parse_command()
        self._validate()

    def command_args(self) -> tuple[str, ...]:
        """Возвращает разобранные аргументы команды."""
        return self._command_args

    def is_test(self) -> bool:
        """Определяет, запускает ли точка входа тесты."""
        return self._target_name().casefold() in KNOWN_TEST_RUNNERS

    def _parse_command(self) -> tuple[str, ...]:
        try:
            args = tuple(shlex.split(self.command, posix=True))
        except ValueError as error:
            message = f"Некорректные кавычки в команде точки входа {self.command!r}"
            raise ValueError(message) from error

        if not args:
            message = "Команда точки входа не должна быть пустой"
            raise ValueError(message)

        return args

    def _validate(self) -> None:
        if not self.alias or not self.alias[0].isalpha():
            message = (
                "Алиас точки входа должен быть непустым и начинаться с буквы: "
                f"{self.alias!r}"
            )
            raise ValueError(message)

        args = self.command_args()

        if args[0] == "-c":
            message = (
                "Режим '-c <python-код>' не поддерживается; "
                "оформите код как модуль или Python-скрипт"
            )
            raise ValueError(message)

        if args[0].startswith("-") and args[0] != "-m":
            message = (
                "Команда точки входа должна начинаться с '-m' или пути к Python-скрипту"
            )
            raise ValueError(message)

        if args[0] == "-m" and len(args) == 1:
            message = f"После {args[0]!r} в команде точки входа должно быть значение"
            raise ValueError(message)

        target_name = self._target_name()
        if target_name.casefold() == "make" or "make " in self.command.lower():
            message = (
                "Точка входа не должна запускать make; "
                "укажите вместо make явную Python-команду"
            )
            raise ValueError(message)

        if self.load_env_file and self.is_test():
            message = "Тесты не должны загружать энвы!"
            raise RuntimeError(message)

    def _target_name(self) -> str:
        args = self.command_args()

        if args[0] == "-m":
            return args[1]

        return Path(args[0]).stem


def entrypoint_factory(  # noqa: PLR0913 -- mirrors the configuration schema
    *,
    alias: str,
    command: str,
    cwd: str | Path = ".",
    venv: str | Path = ".venv",
    env_file: str | Path = ".env",
    load_env_file: bool = False,
    env: Mapping[str, str] | None = None,
) -> Entrypoint:
    """Создаёт точку входа из значений конфигурации."""
    return Entrypoint(
        alias=alias,
        cwd=Path(cwd),
        venv=Path(venv),
        env_file=Path(env_file),
        command=command,
        env=dict(env or {}),
        load_env_file=load_env_file,
    )
