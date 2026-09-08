"""Проверяет доменные правила точек входа."""

import pytest

from runpoint.domain import Runtime, entrypoint_factory


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "unittest discover",
        "python tests/test.py",
        "PyTest",
        "python scripts/tox.py",
    ],
)
def test_entrypoint_recognizes_test_runners(command: str) -> None:
    """Распознаёт поддерживаемые тестовые модули и скрипты."""
    entrypoint = entrypoint_factory(alias="tests", command=command)

    is_test = entrypoint.is_test()

    assert is_test


@pytest.mark.parametrize(
    "command",
    [
        "application",
        "python scripts/worker.py",
        "python scripts/contest.py",
        "pytest_cov",
    ],
)
def test_entrypoint_does_not_misclassify_applications(command: str) -> None:
    """Не принимает обычные приложения за тестовые команды."""
    entrypoint = entrypoint_factory(alias="app", command=command)

    is_test = entrypoint.is_test()

    assert not is_test


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("", "Команда точки входа не должна быть пустой"),
        ("'unterminated", "Некорректные кавычки"),
        ("-c 'print(1)'", "Режим '-c <python-код>' не поддерживается"),
        ("--version", "Не удалось определить runtime"),
        ("-m", "не поддерживается"),
        ("make", "Точка входа не должна запускать make"),
    ],
)
def test_entrypoint_rejects_invalid_commands(command: str, message: str) -> None:
    """Отклоняет команды с некорректной формой."""
    with pytest.raises(ValueError, match=message):
        entrypoint_factory(alias="invalid", command=command)


def test_entrypoint_rejects_dotenv_for_test_runner() -> None:
    """Не разрешает тестовой точке входа загружать dotenv."""
    with pytest.raises(RuntimeError, match="Тесты не должны загружать энвы"):
        entrypoint_factory(
            alias="tests",
            command="pytest",
            load_env_file=True,
        )


def test_entrypoint_detects_go_runtime_from_explicit_go_prefix() -> None:
    """Определяет Go по явному префиксу 'go' и удаляет этот префикс из команды."""
    entrypoint = entrypoint_factory(alias="go-api", command="go run ./cmd/api")

    assert entrypoint.runtime is Runtime.GO
    assert entrypoint.command_args() == ("run", "./cmd/api")


def test_entrypoint_recognizes_go_test_runner() -> None:
    """Распознаёт Go-тесты по подкоманде test."""
    entrypoint = entrypoint_factory(alias="go-tests", command="go test ./internal/api")

    assert entrypoint.is_test()


def test_entrypoint_rejects_unsupported_go_subcommand() -> None:
    """Отклоняет go-подкоманды вне test/run/build."""
    with pytest.raises(ValueError, match=r"go test.*go run.*go build"):
        entrypoint_factory(alias="go-fmt", command="go fmt ./...")


def test_entrypoint_rejects_go_without_subcommand() -> None:
    """Отклоняет 'go' без подкоманды."""
    with pytest.raises(ValueError, match="После 'go'"):
        entrypoint_factory(alias="go-bare", command="go")


def test_entrypoint_normalizes_python_module_command() -> None:
    """Добавляет '-m' к команде запуска Python-модуля."""
    entrypoint = entrypoint_factory(alias="tests", command="pytest")

    assert entrypoint.runtime is Runtime.PYTHON
    assert entrypoint.command_args() == ("-m", "pytest")


def test_entrypoint_normalizes_python_module_command_with_args() -> None:
    """Добавляет '-m' к команде модуля с аргументами."""
    entrypoint = entrypoint_factory(alias="tests", command="pytest -v extra")

    assert entrypoint.command_args() == ("-m", "pytest", "-v", "extra")


def test_entrypoint_detects_python_runtime_from_explicit_python_prefix() -> None:
    """Определяет Python по явному префиксу 'python' и удаляет префикс из команды."""
    entrypoint = entrypoint_factory(
        alias="worker",
        command="python scripts/worker.py",
    )

    assert entrypoint.runtime is Runtime.PYTHON
    assert entrypoint.command_args() == ("scripts/worker.py",)


def test_entrypoint_normalizes_explicit_python_module() -> None:
    """Сохраняет '-m' при явном запуске модуля через 'python'."""
    entrypoint = entrypoint_factory(alias="worker", command="python -m api")

    assert entrypoint.runtime is Runtime.PYTHON
    assert entrypoint.command_args() == ("-m", "api")


def test_entrypoint_accepts_script_without_python_prefix() -> None:
    """Запускает Python-скрипт без явного префикса 'python'."""
    entrypoint = entrypoint_factory(alias="worker", command="scripts/worker.py")

    assert entrypoint.runtime is Runtime.PYTHON
    assert entrypoint.command_args() == ("scripts/worker.py",)
