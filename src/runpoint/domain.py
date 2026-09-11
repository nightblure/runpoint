"""Определяет доменные правила точек входа runpoint."""

from __future__ import annotations

import dataclasses
import enum
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

KNOWN_TEST_RUNNERS = ("test", "pytest", "unittest", "tox", "nox")
GO_EXECUTABLE = "go"
PYTHON_EXECUTABLES = ("python", "python3")
GO_COMMANDS = ("test", "run", "build")
SHELL_OPERATOR_CHARS = frozenset(";&|<>()")
GO_BUILD_FLAGS_WITH_VALUES = frozenset(
    {
        "-asmflags",
        "-buildmode",
        "-compiler",
        "-covermode",
        "-coverpkg",
        "-gccgoflags",
        "-gcflags",
        "-installsuffix",
        "-ldflags",
        "-mod",
        "-modfile",
        "-o",
        "-overlay",
        "-p",
        "-pgo",
        "-pkgdir",
        "-tags",
        "-toolexec",
    }
)
GO_BUILD_BOOLEAN_FLAGS = frozenset(
    {
        "-a",
        "-asan",
        "-buildvcs",
        "-cover",
        "-json",
        "-linkshared",
        "-modcacherw",
        "-msan",
        "-n",
        "-race",
        "-trimpath",
        "-v",
        "-work",
        "-x",
    }
)
GO_TEST_FLAGS_WITH_VALUES = frozenset(
    {
        "-bench",
        "-benchtime",
        "-blockprofile",
        "-blockprofilerate",
        "-count",
        "-coverprofile",
        "-cpu",
        "-cpuprofile",
        "-exec",
        "-fuzz",
        "-fuzzminimizetime",
        "-fuzztime",
        "-list",
        "-memprofile",
        "-memprofilerate",
        "-mutexprofile",
        "-mutexprofilefraction",
        "-outputdir",
        "-parallel",
        "-run",
        "-shuffle",
        "-skip",
        "-timeout",
        "-trace",
        "-vet",
    }
)
GO_TEST_BOOLEAN_FLAGS = frozenset(
    {
        "-artifacts",
        "-benchmem",
        "-c",
        "-failfast",
        "-fullpath",
        "-short",
        "-v",
    }
)


class Runtime(str, enum.Enum):
    """Определяет поддерживаемые runtime точек входа."""

    PYTHON = "python"
    GO = "go"


def _match_go_command(args: tuple[str, ...]) -> tuple[str, ...] | None:
    """Распознаёт полную Go-команду и возвращает аргументы после 'go'."""
    if args[0] != GO_EXECUTABLE or len(args) == 1:
        return None

    return args[1:]


def _match_python_command(args: tuple[str, ...]) -> tuple[str, ...] | None:
    """Нормализует Python-форму: модуль без '-m' получает префикс '-m'."""
    first = args[0]

    if first in PYTHON_EXECUTABLES:
        return args[1:] or None

    if first in (GO_EXECUTABLE, *GO_COMMANDS):
        return None

    if first.startswith("-"):
        return None

    if first.casefold().endswith(".py"):
        return args

    return ("-m", *args)


RUNTIME_DETECTORS: tuple[
    tuple[Runtime, Callable[[tuple[str, ...]], tuple[str, ...] | None], str],
    ...,
] = (
    (
        Runtime.PYTHON,
        _match_python_command,
        "Python: 'python -m <module>', '<module>' или '<script>.py'",
    ),
    (
        Runtime.GO,
        _match_go_command,
        "Go: 'go test <package>', 'go run <package>' или 'go build <package>'",
    ),
)


def _find_shell_operator(command: str) -> str | None:  # noqa: C901, PLR0911
    quote: str | None = None
    index = 0

    while index < len(command):
        character = command[index]

        if character == "\\" and quote != "'":
            index += 2
            continue

        if quote is not None:
            if character == quote:
                quote = None
            elif quote == '"' and character == "`":
                return "`"
            elif quote == '"' and character == "$" and command[index + 1 : index + 2] == "(":
                return "$("
            index += 1
            continue

        if character in {"'", '"'}:
            quote = character
            index += 1
            continue

        if character in "\r\n":
            return "\\n"

        if character == "`":
            return "`"

        if character == "$" and command[index + 1 : index + 2] == "(":
            return "$("

        if character in SHELL_OPERATOR_CHARS:
            end = index + 1
            while end < len(command) and command[end] in SHELL_OPERATOR_CHARS:
                end += 1
            return command[index:end]

        index += 1

    return None


def _find_go_source_file(args: tuple[str, ...]) -> str | None:  # noqa: C901
    operation = args[0]
    value_flags = GO_BUILD_FLAGS_WITH_VALUES
    known_flags = GO_BUILD_FLAGS_WITH_VALUES | GO_BUILD_BOOLEAN_FLAGS
    if operation == "test":
        value_flags |= GO_TEST_FLAGS_WITH_VALUES
        known_flags |= GO_TEST_FLAGS_WITH_VALUES | GO_TEST_BOOLEAN_FLAGS

    index = 1
    while index < len(args):
        argument = args[index]

        if operation == "test" and argument == "-args":
            return None

        if argument == "--":
            index += 1
            continue

        if argument.startswith("-"):
            flag = argument.partition("=")[0]
            if flag.startswith("-test."):
                flag = f"-{flag.removeprefix('-test.')}"

            if operation == "test" and flag not in known_flags:
                return None

            if "=" not in argument and flag in value_flags:
                index += 2
            else:
                index += 1
            continue

        if argument.casefold().endswith(".go"):
            return argument

        if operation == "run":
            return None

        index += 1

    return None


@dataclasses.dataclass(frozen=False, slots=True, kw_only=True)
class Entrypoint:
    """Описывает настраиваемую точку входа."""

    alias: str
    command: str
    load_env_file: bool
    runtime: Runtime = dataclasses.field(init=False)
    cwd: Path = Path()
    venv: Path = Path(".venv")
    env_file: Path = Path(".env")
    debug_port: int | None = None
    env: Mapping[str, str] = dataclasses.field(default_factory=dict)
    _command_args: tuple[str, ...] = dataclasses.field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Разбирает и проверяет команду после инициализации."""
        self._command_args = self._parse_command()
        self.runtime, self._command_args = self._detect_runtime()
        self._validate()

    def command_args(self) -> tuple[str, ...]:
        """Возвращает разобранные аргументы команды."""
        return self._command_args

    def is_test(self) -> bool:
        """Определяет, запускает ли точка входа тесты."""
        if self.runtime is Runtime.GO:
            return self.command_args()[0] == "test"

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

        shell_operator = _find_shell_operator(self.command)
        if shell_operator is not None:
            message = (
                "Команда точки входа не должна содержать shell-операторы "
                f"(обнаружен {shell_operator!r}); "
                "укажите одну команду без shell-композиции"
            )
            raise ValueError(message)

        return args

    def _detect_runtime(self) -> tuple[Runtime, tuple[str, ...]]:
        """Определяет runtime и возвращает нормализованные аргументы команды."""
        args = self.command_args()
        matches = tuple(
            (runtime, normalized)
            for runtime, matcher, _description in RUNTIME_DETECTORS
            if (normalized := matcher(args)) is not None
        )

        if len(matches) == 1:
            return matches[0]

        if not matches:
            message = self._unrecognized_command_message(args)
            raise ValueError(message)

        runtimes = ", ".join(runtime.value for runtime, _args in matches)
        message = f"Команда неоднозначно соответствует runtime: {runtimes}"
        raise ValueError(message)

    def _unrecognized_command_message(self, args: tuple[str, ...]) -> str:
        """Объясняет, почему форма команды не распознана."""
        first = args[0]

        if first == GO_EXECUTABLE:
            message = "После 'go' в команде точки входа должна быть подкоманда: 'go test', 'go run' или 'go build'"
        elif first in PYTHON_EXECUTABLES:
            message = f"После {first!r} в команде точки входа должен быть модуль или путь к Python-скрипту"
        elif first == "-m":
            message = "Форма '-m <модуль>' без интерпретатора не поддерживается; укажите явно: python -m <модуль>"
        elif first == "-c":
            message = "Режим '-c <python-код>' не поддерживается; оформите код как модуль или Python-скрипт"
        else:
            supported = "; ".join(description for _, _, description in RUNTIME_DETECTORS)
            message = f"Не удалось определить runtime команды точки входа. Поддерживаемые формы: {supported}"

        return message

    def _validate(self) -> None:
        if not self.alias or not self.alias[0].isalpha():
            message = f"Алиас точки входа должен быть непустым и начинаться с буквы: {self.alias!r}"
            raise ValueError(message)

        args = self.command_args()

        if self.runtime is Runtime.GO:
            self._validate_go(args)
            return

        self._validate_python(args)

    def _validate_go(self, args: tuple[str, ...]) -> None:
        if args[0] not in GO_COMMANDS:
            message = "Go-команда точки входа должна начинаться с 'go test', 'go run' или 'go build'"
            raise ValueError(message)

        source_file = _find_go_source_file(args)
        if source_file is not None:
            package = self._go_package_for(source_file)
            message = (
                f"Go-команда {args[0]!r} не должна указывать конкретный "
                f".go-файл {source_file!r}: Go компилирует только перечисленные "
                ".go-файлы и может пропустить соседние файлы того же package; "
                f"укажите package {package!r}"
            )
            raise ValueError(message)

        if self.load_env_file and self.is_test():
            message = "Тесты не должны загружать энвы!"
            raise RuntimeError(message)

    def _validate_python(self, args: tuple[str, ...]) -> None:
        if args[0] == "-c":
            message = "Режим '-c <python-код>' не поддерживается; оформите код как модуль или Python-скрипт"
            raise ValueError(message)

        if args[0].startswith("-") and args[0] != "-m":
            message = "Команда точки входа должна начинаться с '-m' или пути к Python-скрипту"
            raise ValueError(message)

        if args[0] == "-m" and len(args) == 1:
            message = f"После {args[0]!r} в команде точки входа должно быть значение"
            raise ValueError(message)

        target_name = self._target_name()
        if target_name.casefold() == "make" or "make " in self.command.lower():
            message = "Точка входа не должна запускать make; укажите вместо make явную Python-команду"
            raise ValueError(message)

        if self.load_env_file and self.is_test():
            message = "Тесты не должны загружать энвы!"
            raise RuntimeError(message)

    @staticmethod
    def _go_package_for(source_file: str) -> str:
        parent = Path(source_file).parent
        if parent == Path():
            return "."

        package = str(parent)
        if parent.is_absolute() or package.startswith("."):
            return package

        return f"./{package}"

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
    debug_port: int | None = None,
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
        debug_port=debug_port,
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class GlobalConfig:
    debug_port: int | None = None
