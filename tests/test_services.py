"""Проверяет runtime-сервисы запуска."""

from pathlib import Path

import pytest

from runpoint.domain import entrypoint_factory
from runpoint.services import (
    build_python_command,
    load_env_variables,
    resolve_python_executable,
    validate_target,
)


def test_build_command_configures_debugpy_and_preserves_target_args() -> None:
    """Формирует полный debugpy-вызов с аргументами приложения."""
    entrypoint = entrypoint_factory(alias="worker", command="worker --mode safe")

    command = build_python_command(
        port=5679,
        debug=True,
        python_executable=Path("/project/.venv/bin/python"),
        wait_for_client=True,
        entrypoint=entrypoint,
        debug_subprocesses=False,
        target_args=("--limit", "10"),
    )

    assert command == [
        "/project/.venv/bin/python",
        "-Xfrozen_modules=off",
        "-m",
        "debugpy",
        "--listen",
        "127.0.0.1:5679",
        "--wait-for-client",
        "--configure-subProcess",
        "False",
        "-m",
        "worker",
        "--mode",
        "safe",
        "--limit",
        "10",
    ]


def test_build_command_without_debug_runs_target_directly() -> None:
    """Не добавляет debugpy к обычному запуску."""
    entrypoint = entrypoint_factory(
        alias="worker", command="python worker.py --mode safe"
    )

    command = build_python_command(
        port=5678,
        debug=False,
        python_executable=Path("/project/.venv/bin/python"),
        wait_for_client=True,
        entrypoint=entrypoint,
        debug_subprocesses=True,
        target_args=("--limit", "10"),
    )

    assert command == [
        "/project/.venv/bin/python",
        "worker.py",
        "--mode",
        "safe",
        "--limit",
        "10",
    ]


def test_load_env_variables_applies_documented_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Даёт явной конфигурации приоритет над process env и dotenv."""
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "SHARED=dotenv\nDOTENV_ONLY=dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED", "process")
    monkeypatch.setenv("PROCESS_ONLY", "process")
    entrypoint = entrypoint_factory(
        alias="api",
        command="api",
        load_env_file=True,
        env={"SHARED": "entrypoint", "ENTRYPOINT_ONLY": "entrypoint"},
    )

    environment = load_env_variables(
        entrypoint,
        no_env=False,
        config_dir=tmp_path,
    )

    assert {
        key: environment[key]
        for key in (
            "SHARED",
            "DOTENV_ONLY",
            "PROCESS_ONLY",
            "ENTRYPOINT_ONLY",
        )
    } == {
        "SHARED": "entrypoint",
        "DOTENV_ONLY": "dotenv",
        "PROCESS_ONLY": "process",
        "ENTRYPOINT_ONLY": "entrypoint",
    }


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "unittest discover",
        "tox",
        "nox",
        "python test.py",
    ],
)
def test_load_env_variables_skips_dotenv_for_test_runners(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не загружает dotenv для поддерживаемых тестовых команд."""
    (tmp_path / ".env").write_text("DOTENV_ONLY=dotenv\n", encoding="utf-8")
    monkeypatch.delenv("DOTENV_ONLY", raising=False)
    monkeypatch.setenv("PROCESS_ONLY", "process")
    entrypoint = entrypoint_factory(
        alias="tests",
        command=command,
        env={"ENTRYPOINT_ONLY": "entrypoint"},
    )
    entrypoint.load_env_file = True

    environment = load_env_variables(
        entrypoint,
        no_env=False,
        config_dir=tmp_path,
    )

    assert "DOTENV_ONLY" not in environment
    assert (
        environment["PROCESS_ONLY"],
        environment["ENTRYPOINT_ONLY"],
    ) == ("process", "entrypoint")


def test_load_env_variables_skips_missing_dotenv_for_test_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не обращается к отсутствующему dotenv тестовой команды."""
    monkeypatch.setenv("PROCESS_ONLY", "process")
    entrypoint = entrypoint_factory(
        alias="tests",
        command="pytest",
        env_file="missing.env",
        env={"ENTRYPOINT_ONLY": "entrypoint"},
    )
    entrypoint.load_env_file = True

    environment = load_env_variables(
        entrypoint,
        no_env=False,
        config_dir=tmp_path,
    )

    assert (
        environment["PROCESS_ONLY"],
        environment["ENTRYPOINT_ONLY"],
    ) == ("process", "entrypoint")


def test_load_env_variables_no_env_skips_missing_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не читает dotenv при явном отключении, но сохраняет остальные источники."""
    monkeypatch.setenv("PROCESS_VALUE", "process")
    entrypoint = entrypoint_factory(
        alias="api",
        command="api",
        env_file="missing.env",
        load_env_file=True,
        env={"ENTRYPOINT_VALUE": "entrypoint"},
    )

    environment = load_env_variables(
        entrypoint,
        no_env=True,
        config_dir=tmp_path,
    )

    assert (
        environment["PROCESS_VALUE"],
        environment["ENTRYPOINT_VALUE"],
    ) == ("process", "entrypoint")


def test_resolve_python_executable_falls_back_to_python3(tmp_path: Path) -> None:
    """Использует bin/python3, если bin/python отсутствует."""
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    python_executable = bin_dir / "python3"
    python_executable.touch()
    entrypoint = entrypoint_factory(alias="api", command="api")

    resolved = resolve_python_executable(
        working_dir=tmp_path,
        entrypoint=entrypoint,
    )

    assert resolved == python_executable


def test_validate_target_rejects_missing_script(tmp_path: Path) -> None:
    """Сообщает об отсутствующем файловом target."""
    entrypoint = entrypoint_factory(alias="worker", command="python missing.py")

    with pytest.raises(SystemExit, match=r"Python-скрипт .* не найден"):
        validate_target(working_dir=tmp_path, entrypoint=entrypoint)


def test_validate_target_accepts_module_without_file_lookup(tmp_path: Path) -> None:
    """Не требует локального файла для запуска через -m."""
    entrypoint = entrypoint_factory(alias="worker", command="external_worker")

    validate_target(working_dir=tmp_path, entrypoint=entrypoint)
