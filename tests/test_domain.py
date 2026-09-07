"""Проверяет доменные правила точек входа."""

import pytest

from runpoint.domain import entrypoint_factory


@pytest.mark.parametrize(
    "command",
    [
        "-m pytest",
        "-m unittest discover",
        "tests/test.py",
        "-m PyTest",
        "scripts/tox.py",
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
        "-m application",
        "scripts/worker.py",
        "scripts/contest.py",
        "-m pytest_cov",
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
        ("--version", "Команда точки входа должна начинаться"),
        ("-m", "в команде точки входа должно быть значение"),
        ("-m make", "Точка входа не должна запускать make"),
    ],
)
def test_entrypoint_rejects_invalid_commands(command: str, message: str) -> None:
    """Отклоняет команды, которые нельзя безопасно запустить как Python."""
    with pytest.raises(ValueError, match=message):
        entrypoint_factory(alias="invalid", command=command)


def test_entrypoint_rejects_dotenv_for_test_runner() -> None:
    """Не разрешает тестовой точке входа загружать dotenv."""
    with pytest.raises(RuntimeError, match="Тесты не должны загружать энвы"):
        entrypoint_factory(
            alias="tests",
            command="-m pytest",
            load_env_file=True,
        )
