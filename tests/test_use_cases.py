"""Проверяет координацию запуска точки входа."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from runpoint import services
from runpoint.domain import entrypoint_factory
from runpoint.use_cases import launch_entrypoint

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


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
        command="worker --mode safe",
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


@pytest.mark.parametrize(
    ("configured_command", "target_args", "expected_args"),
    [
        (
            "go run ./cmd/api",
            ("--config", "settings.go"),
            ("run", "./cmd/api", "--config", "settings.go"),
        ),
        ("go test ./...", ("-run", "TestAPI"), ("test", "./...", "-run", "TestAPI")),
        ("go build ./cmd/api", (), ("build", "./cmd/api")),
    ],
)
def test_launch_entrypoint_runs_supported_go_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_command: str,
    target_args: tuple[str, ...],
    expected_args: tuple[str, ...],
) -> None:
    """Запускает поддерживаемую Go-команду из PATH без shell."""
    working_dir = tmp_path / "service"
    working_dir.mkdir()
    monkeypatch.setenv("PROCESS_VALUE", "process")
    tool_lookups: list[str] = []
    replacements: list[tuple[Path, tuple[str, ...], dict[str, str]]] = []

    def which(executable: str) -> str:
        tool_lookups.append(executable)
        return "/tools/go"

    def replace_process(
        *,
        working_dir: Path,
        command: Sequence[str],
        environment: dict[str, str],
    ) -> None:
        replacements.append((working_dir, tuple(command), environment))

    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr(services, "replace_process", replace_process)
    entrypoint = entrypoint_factory(
        alias="go-service",
        command=configured_command,
        cwd="service",
        env={"ENTRYPOINT_VALUE": "entrypoint"},
    )
    messages: list[str] = []

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=target_args,
        debug=False,
        debug_port=5678,
        no_debug_wait=False,
        debug_subprocesses=True,
        no_env=False,
        print_message=messages.append,
        print_debug=lambda _message: None,
    )

    replaced_working_dir, command, environment = replacements[0]
    assert tool_lookups == ["go"]
    assert replaced_working_dir == working_dir
    assert command == ("/tools/go", *expected_args)
    assert environment["PROCESS_VALUE"] == "process"
    assert environment["ENTRYPOINT_VALUE"] == "entrypoint"
    assert "PYDEVD_DISABLE_FILE_VALIDATION" not in environment
    assert messages[:2] == [
        f"working_dir: {working_dir}",
        f"cmd: /tools/go {' '.join(expected_args)}",
    ]


@pytest.mark.parametrize(
    ("debug", "executable"),
    [(False, "go"), (True, "dlv")],
)
def test_launch_entrypoint_resolves_relative_go_tool_before_changing_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    debug: bool,  # noqa: FBT001 -- supplied by pytest parametrization
    executable: str,
) -> None:
    """Сохраняет найденный через относительный PATH tool при смене cwd."""
    working_dir = tmp_path / "service"
    working_dir.mkdir()
    entrypoint = entrypoint_factory(
        alias="go-api",
        command="go run ./cmd/api",
        cwd="service",
    )
    commands: list[tuple[str, ...]] = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shutil.which",
        lambda requested: f"tools/{requested}",
    )

    def replace_process(
        *,
        working_dir: Path,
        command: Sequence[str],
        environment: dict[str, str],
    ) -> None:
        del working_dir, environment
        commands.append(tuple(command))

    monkeypatch.setattr(services, "replace_process", replace_process)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=debug,
        debug_port=2345,
        no_debug_wait=False,
        debug_subprocesses=True,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    assert commands[0][0] == str((tmp_path / "tools" / executable).resolve())


def test_launch_entrypoint_debugs_go_run_with_delve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает Go-приложение в headless Delve и передаёт ему target-аргументы."""
    entrypoint = entrypoint_factory(
        alias="go-api",
        command="go run ./cmd/api",
    )
    replacements: list[tuple[Path, tuple[str, ...]]] = []
    debug_messages: list[str] = []

    def replace_process(
        *,
        working_dir: Path,
        command: Sequence[str],
        environment: dict[str, str],
    ) -> None:
        del environment
        replacements.append((working_dir, tuple(command)))

    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    monkeypatch.setattr(services, "replace_process", replace_process)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("--port", "8080"),
        debug=True,
        debug_port=2345,
        no_debug_wait=True,
        debug_subprocesses=True,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=debug_messages.append,
    )

    assert replacements == [
        (
            tmp_path,
            (
                "/tools/dlv",
                "debug",
                "--headless",
                "--listen=127.0.0.1:2345",
                "--api-version=2",
                "--accept-multiclient",
                "--continue",
                "./cmd/api",
                "--",
                "--port",
                "8080",
            ),
        )
    ]
    assert debug_messages == ["dlv: 127.0.0.1:2345 (без ожидания IDE)"]


def test_launch_entrypoint_debugs_go_tests_with_delve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает Go-тесты через dlv test и передаёт аргументы test binary."""
    entrypoint = entrypoint_factory(
        alias="go-tests",
        command="go test ./internal/api",
    )
    commands: list[tuple[str, ...]] = []
    debug_messages: list[str] = []

    def replace_process(
        *,
        working_dir: Path,
        command: Sequence[str],
        environment: dict[str, str],
    ) -> None:
        del working_dir, environment
        commands.append(tuple(command))

    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    monkeypatch.setattr(services, "replace_process", replace_process)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("-test.run", "TestAPI"),
        debug=True,
        debug_port=2346,
        no_debug_wait=False,
        debug_subprocesses=True,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=debug_messages.append,
    )

    assert commands == [
        (
            "/tools/dlv",
            "test",
            "--headless",
            "--listen=127.0.0.1:2346",
            "--api-version=2",
            "--accept-multiclient",
            "./internal/api",
            "--",
            "-test.run",
            "TestAPI",
        )
    ]
    assert debug_messages == ["dlv: 127.0.0.1:2346 (Ожидание подключения IDE)"]


def test_launch_entrypoint_debugs_go_tests_in_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает dlv test без package для тестов текущей директории."""
    entrypoint = entrypoint_factory(
        alias="go-tests",
        command="go test",
    )
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    monkeypatch.setattr(
        services,
        "replace_process",
        lambda **kwargs: commands.append(tuple(kwargs["command"])),
    )

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("-test.run", "TestAPI"),
        debug=True,
        debug_port=2346,
        no_debug_wait=False,
        debug_subprocesses=True,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    assert commands == [
        (
            "/tools/dlv",
            "test",
            "--headless",
            "--listen=127.0.0.1:2346",
            "--api-version=2",
            "--accept-multiclient",
            "--",
            "-test.run",
            "TestAPI",
        )
    ]


def test_launch_entrypoint_rejects_no_debug_wait_for_go_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не передаёт несовместимый флаг --continue команде dlv test."""
    entrypoint = entrypoint_factory(
        alias="go-tests",
        command="go test ./internal/api",
    )

    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    monkeypatch.setattr(services, "replace_process", lambda **_kwargs: None)

    with pytest.raises(SystemExit, match=r"--no-debug-wait.*Go.*test"):
        launch_entrypoint(
            config_dir=tmp_path,
            entrypoint=entrypoint,
            target_args=(),
            debug=True,
            debug_port=2346,
            no_debug_wait=True,
            debug_subprocesses=True,
            no_env=False,
            print_message=lambda _message: None,
            print_debug=lambda _message: None,
        )


def test_launch_entrypoint_rejects_go_build_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отклоняет debug для Go build до поиска не нужного ему Delve."""
    entrypoint = entrypoint_factory(
        alias="go-build",
        command="go build ./cmd/api",
    )

    monkeypatch.setattr("shutil.which", lambda _executable: None)

    with pytest.raises(
        SystemExit,
        match="Отладка Go-команды 'build' не поддерживается",
    ):
        launch_entrypoint(
            config_dir=tmp_path,
            entrypoint=entrypoint,
            target_args=(),
            debug=True,
            debug_port=2345,
            no_debug_wait=False,
            debug_subprocesses=True,
            no_env=False,
            print_message=lambda _message: None,
            print_debug=lambda _message: None,
        )


@pytest.mark.parametrize(
    ("debug", "executable", "tool"),
    [(False, "go", "Go"), (True, "dlv", "Delve")],
)
def test_launch_entrypoint_reports_missing_go_runtime_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    debug: bool,  # noqa: FBT001 -- supplied by pytest parametrization
    executable: str,
    tool: str,
) -> None:
    """Сообщает об отсутствующем go или dlv из PATH."""
    entrypoint = entrypoint_factory(
        alias="go-api",
        command="go run ./cmd/api",
    )

    monkeypatch.setattr("shutil.which", lambda _executable: None)

    with pytest.raises(
        SystemExit,
        match=rf"Исполняемый файл '{executable}' не найден в PATH.*Установите {tool}",
    ):
        launch_entrypoint(
            config_dir=tmp_path,
            entrypoint=entrypoint,
            target_args=(),
            debug=debug,
            debug_port=2345,
            no_debug_wait=False,
            debug_subprocesses=True,
            no_env=False,
            print_message=lambda _message: None,
            print_debug=lambda _message: None,
        )


def test_launch_entrypoint_rejects_ambiguous_go_debug_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не принимает Go-флаг за пакет Delve; target-аргументы требует после `--`."""
    entrypoint = entrypoint_factory(
        alias="go-tests",
        command="go test -run",
    )

    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    monkeypatch.setattr(services, "replace_process", lambda **_kwargs: None)

    with pytest.raises(SystemExit, match=r"Некорректный target.*после '--'"):
        launch_entrypoint(
            config_dir=tmp_path,
            entrypoint=entrypoint,
            target_args=("TestAPI",),
            debug=True,
            debug_port=2345,
            no_debug_wait=False,
            debug_subprocesses=True,
            no_env=False,
            print_message=lambda _message: None,
            print_debug=lambda _message: None,
        )


@pytest.mark.parametrize(
    "target",
    ["./...", "./internal/...", "all", "std", "cmd", "tool"],
)
def test_launch_entrypoint_rejects_multi_package_go_debug_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    """Не передаёт dlv test package-паттерн, способный выбрать несколько пакетов."""
    entrypoint = entrypoint_factory(
        alias="go-tests",
        command=f"go test {target}",
    )

    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    monkeypatch.setattr(services, "replace_process", lambda **_kwargs: None)

    with pytest.raises(SystemExit, match=r"package-паттерн.*один Go package"):
        launch_entrypoint(
            config_dir=tmp_path,
            entrypoint=entrypoint,
            target_args=(),
            debug=True,
            debug_port=2345,
            no_debug_wait=False,
            debug_subprocesses=True,
            no_env=False,
            print_message=lambda _message: None,
            print_debug=lambda _message: None,
        )


def test_launch_entrypoint_prints_debug_status_immediately_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Направляет debug-статус в stderr-функцию прямо перед заменой процесса."""
    python_executable = tmp_path / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    entrypoint = entrypoint_factory(alias="api", command="api")
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
