"""Проверяет координацию запуска точки входа."""

from __future__ import annotations

from typing import TYPE_CHECKING

from runpoint import services
from runpoint.domain import entrypoint_factory
from runpoint.use_cases import launch_entrypoint

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest


def test_launch_entrypoint_replaces_process_with_resolved_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Передаёт сервису замены процесса команду, cwd и собранное окружение."""
    working_dir = tmp_path / "application"
    python_executable = working_dir / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("DOTENV_VALUE=dotenv\n", encoding="utf-8")
    monkeypatch.setenv("PROCESS_VALUE", "process")
    entrypoint = entrypoint_factory(
        alias="worker",
        command="-m worker --mode safe",
        cwd="application",
        load_env_file=True,
        env={"ENTRYPOINT_VALUE": "entrypoint"},
    )
    messages: list[str] = []
    debug_messages: list[str] = []
    replacements: list[tuple[Path, tuple[str, ...], dict[str, str]]] = []

    def replace_process(
        *,
        working_dir: Path,
        command: Sequence[str],
        environment: dict[str, str],
    ) -> None:
        replacements.append((working_dir, tuple(command), environment))

    monkeypatch.setattr(services, "replace_process", replace_process)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("--limit", "10"),
        debug=False,
        debug_port=5678,
        no_debug_wait=False,
        debug_subprocesses=True,
        no_env=False,
        print_message=messages.append,
        print_debug=debug_messages.append,
    )

    replaced_working_dir, command, environment = replacements[0]
    assert replaced_working_dir == working_dir
    assert command == (
        str(python_executable),
        "-m",
        "worker",
        "--mode",
        "safe",
        "--limit",
        "10",
    )
    assert {
        key: environment[key]
        for key in (
            "DOTENV_VALUE",
            "PROCESS_VALUE",
            "ENTRYPOINT_VALUE",
            "PYDEVD_DISABLE_FILE_VALIDATION",
        )
    } == {
        "DOTENV_VALUE": "dotenv",
        "PROCESS_VALUE": "process",
        "ENTRYPOINT_VALUE": "entrypoint",
        "PYDEVD_DISABLE_FILE_VALIDATION": "1",
    }
    assert messages == [
        f"working_dir: {working_dir}",
        f"cmd: {python_executable} -m worker --mode safe --limit 10",
        f"Переменные окружения загружены из {dotenv_path}",
    ]
    assert debug_messages == []


def test_launch_entrypoint_prints_debug_status_immediately_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Направляет debug-статус в stderr-функцию прямо перед заменой процесса."""
    python_executable = tmp_path / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    entrypoint = entrypoint_factory(alias="api", command="-m api")
    events: list[tuple[str, str]] = []

    def print_message(message: str) -> None:
        events.append(("stdout", message))

    def print_debug(message: str) -> None:
        events.append(("stderr", message))

    def replace_process(
        *,
        working_dir: Path,
        command: Sequence[str],
        environment: dict[str, str],
    ) -> None:
        del working_dir, command, environment
        events.append(("replace", ""))

    monkeypatch.setattr(services, "replace_process", replace_process)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=True,
        debug_port=5679,
        no_debug_wait=True,
        debug_subprocesses=False,
        no_env=True,
        print_message=print_message,
        print_debug=print_debug,
    )

    assert events[-2:] == [
        ("stderr", "debugpy: 127.0.0.1:5679 (без ожидания IDE)"),
        ("replace", ""),
    ]
    assert events[:3] == [
        ("stdout", f"working_dir: {tmp_path}"),
        (
            "stdout",
            (
                f"cmd: {python_executable} -Xfrozen_modules=off -m debugpy "
                "--listen 127.0.0.1:5679 --configure-subProcess False -m api"
            ),
        ),
        ("stdout", "Загрузка энвов пропущена из-за флага --no-env"),
    ]
