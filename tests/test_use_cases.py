"""Проверяет координацию запуска точки входа."""

from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING, TypedDict

import pytest

from runpoint import services
from runpoint.domain import Runtime, entrypoint_factory
from runpoint.use_cases import launch_entrypoint

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from runpoint.services import StaleDebuggerMatcher


class _ReplaceCall(TypedDict):
    working_dir: Path
    command: tuple[str, ...]
    environment: dict[str, str]


class _RunCall(TypedDict):
    command: tuple[str, ...]
    working_dir: Path
    environment: dict[str, str]
    port: int
    matchers: tuple[StaleDebuggerMatcher, ...]
    print_debug: Callable[[str], None]


class _CleanupCall(TypedDict):
    port: int
    matchers: tuple[StaleDebuggerMatcher, ...]


def _stub_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    sink: list[str] | None = None,
) -> list[_CleanupCall]:
    calls: list[_CleanupCall] = []

    def cleanup(
        *,
        port: int,
        matchers: tuple[StaleDebuggerMatcher, ...],
        print_debug: Callable[[str], None],
    ) -> None:
        del print_debug
        calls.append({"port": port, "matchers": matchers})
        if sink is not None:
            sink.append("cleanup")

    monkeypatch.setattr(services, "cleanup_stale_debuggers", cleanup)
    return calls


def _stub_replace(
    monkeypatch: pytest.MonkeyPatch,
    sink: list[str] | None = None,
) -> list[_ReplaceCall]:
    calls: list[_ReplaceCall] = []

    def replace(
        *,
        working_dir: Path,
        command: Sequence[str],
        environment: dict[str, str],
    ) -> None:
        calls.append(
            {
                "working_dir": working_dir,
                "command": tuple(command),
                "environment": environment,
            }
        )
        if sink is not None:
            sink.append("replace")

    monkeypatch.setattr(services, "replace_process", replace)
    return calls


def _stub_run_python_debug(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_code: int = 0,
) -> list[_RunCall]:
    calls: list[_RunCall] = []

    def run(  # noqa: PLR0913
        *,
        command: Sequence[str],
        working_dir: Path,
        environment: dict[str, str],
        port: int,
        matchers: tuple[StaleDebuggerMatcher, ...],
        print_debug: Callable[[str], None],
    ) -> int:
        calls.append(
            {
                "command": tuple(command),
                "working_dir": working_dir,
                "environment": environment,
                "port": port,
                "matchers": matchers,
                "print_debug": print_debug,
            }
        )
        return return_code

    monkeypatch.setattr(services, "run_python_debug", run)
    return calls


def _make_python_dir(tmp_path: Path) -> Path:
    working_dir = tmp_path / "application"
    python_executable = working_dir / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    return working_dir


_CHILD_EXIT_CODE = 42
_PY_DEBUG_PORT = 5679
_GO_DEBUG_PORT = 2345


# --- Non-debug launches -----------------------------------------------------


def test_launch_entrypoint_skips_debug_cleanup_for_normal_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обычный запуск не вызывает очистку зависших отладчиков."""
    python_executable = tmp_path / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    monkeypatch.setattr(
        services,
        "cleanup_stale_debuggers",
        lambda **_kwargs: pytest.fail("cleanup вызван для обычного запуска"),
    )
    _stub_replace(monkeypatch)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint_factory(alias="api", command="api"),
        target_args=(),
        debug=False,
        debug_port=5678,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )


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
    replacements = _stub_replace(monkeypatch)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("--limit", "10"),
        debug=False,
        debug_port=5678,
        no_env=False,
        print_message=messages.append,
        print_debug=lambda _message: None,
    )

    replaced = replacements[0]
    assert replaced["working_dir"] == working_dir
    assert replaced["command"] == (
        str(python_executable),
        "-m",
        "worker",
        "--mode",
        "safe",
        "--limit",
        "10",
    )
    assert {
        key: replaced["environment"][key]
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
        f"Переменные окружения загружены из .env-файла {dotenv_path}",
    ]


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
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/go")
    replacements = _stub_replace(monkeypatch)
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
        no_env=False,
        print_message=messages.append,
        print_debug=lambda _message: None,
    )

    replaced = replacements[0]
    assert replaced["working_dir"] == working_dir
    assert replaced["command"] == ("/tools/go", *expected_args)
    assert replaced["environment"]["PROCESS_VALUE"] == "process"
    assert replaced["environment"]["ENTRYPOINT_VALUE"] == "entrypoint"
    assert "PYDEVD_DISABLE_FILE_VALIDATION" not in replaced["environment"]
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

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda requested: f"tools/{requested}")
    _stub_cleanup(monkeypatch)
    replacements = _stub_replace(monkeypatch)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=debug,
        debug_port=2345,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    assert replacements[0]["command"][0] == str(
        (tmp_path / "tools" / executable).resolve()
    )


@pytest.mark.parametrize(
    ("command", "no_env", "load_env_file", "notice_kind"),
    [
        ("api", True, True, "no_env"),
        ("pytest", False, True, "test"),
        ("pytest", True, True, "no_env"),
        ("api", False, True, "loaded"),
        ("api", False, False, None),
    ],
)
def test_launch_entrypoint_prints_one_truthful_dotenv_notice(  # noqa: PLR0913, PLR0917
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    no_env: bool,  # noqa: FBT001 -- supplied by pytest parametrization
    load_env_file: bool,  # noqa: FBT001 -- supplied by pytest parametrization
    notice_kind: str | None,
) -> None:
    """Сообщает ровно о фактически выбранном результате загрузки .env."""
    python_executable = tmp_path / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.touch()
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("FROM_DOTENV=yes\n", encoding="utf-8")
    entrypoint = entrypoint_factory(alias="application", command=command)
    entrypoint.load_env_file = load_env_file
    messages: list[str] = []
    _stub_replace(monkeypatch)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=False,
        debug_port=5678,
        no_env=no_env,
        print_message=messages.append,
        print_debug=lambda _message: None,
    )

    expected_notices = {
        "no_env": ["Загрузка .env пропущена: указан флаг --no-env"],
        "test": ["Загрузка .env пропущена: обнаружен запуск тестов"],
        "loaded": [f"Переменные окружения загружены из .env-файла {dotenv_path}"],
        None: [],
    }
    assert messages[2:] == expected_notices[notice_kind]


# --- Python debug lifecycle -------------------------------------------------


def test_launch_entrypoint_returns_python_debug_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Возвращает exit code дочернего отладочного процесса Python."""
    working_dir = _make_python_dir(tmp_path)
    entrypoint = entrypoint_factory(alias="api", command="api", cwd="application")
    run_calls = _stub_run_python_debug(monkeypatch, return_code=42)

    code = launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=True,
        debug_port=5679,
        no_env=True,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    assert code == _CHILD_EXIT_CODE
    assert len(run_calls) == 1
    call = run_calls[0]
    assert call["port"] == _PY_DEBUG_PORT
    assert call["working_dir"] == working_dir.resolve()
    assert call["matchers"] == services.debugger_matchers(Runtime.PYTHON)
    assert call["command"][-1] == "api"


def test_launch_entrypoint_prints_debugpy_status_before_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Направляет debug-статус в stderr перед запуском Python-отладки."""
    _make_python_dir(tmp_path)
    entrypoint = entrypoint_factory(alias="api", command="api", cwd="application")
    events: list[tuple[str, object]] = []

    def run(**_kwargs: object) -> int:
        events.append(("run", ""))
        return 0

    monkeypatch.setattr(services, "run_python_debug", run)

    def print_message(message: str) -> None:
        events.append(("stdout", message))

    def print_debug(message: str) -> None:
        events.append(("stderr", message))

    code = launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=True,
        debug_port=5679,
        no_env=True,
        print_message=print_message,
        print_debug=print_debug,
    )

    assert code == 0
    assert events[-2:] == [("stderr", "debugpy: 127.0.0.1:5679"), ("run", "")]


# --- Go debug lifecycle -----------------------------------------------------


def test_launch_entrypoint_go_debug_runs_delve_without_accept_multiclient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает single-client Delve без --accept-multiclient и --continue."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    entrypoint = entrypoint_factory(alias="go-api", command="go run ./cmd/api")
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    _stub_cleanup(monkeypatch)
    replacements = _stub_replace(monkeypatch)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("--port", "8080"),
        debug=True,
        debug_port=2345,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    command = replacements[0]["command"]
    assert "--accept-multiclient" not in command
    assert "--continue" not in command
    assert command == (
        "/tools/dlv",
        "debug",
        "--headless",
        "--listen=127.0.0.1:2345",
        "--api-version=2",
        "--output",
        str(tmp_path / "runpoint-dlv-2345"),
        "./cmd/api",
        "--",
        "--port",
        "8080",
    )


def test_launch_entrypoint_go_debug_tests_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает dlv test для одного package без --accept-multiclient."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    entrypoint = entrypoint_factory(alias="go-tests", command="go test ./internal/api")
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    _stub_cleanup(monkeypatch)
    replacements = _stub_replace(monkeypatch)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("-test.run", "TestAPI"),
        debug=True,
        debug_port=2346,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    assert replacements[0]["command"] == (
        "/tools/dlv",
        "test",
        "--headless",
        "--listen=127.0.0.1:2346",
        "--api-version=2",
        "--output",
        str(tmp_path / "runpoint-dlv-2346"),
        "./internal/api",
        "--",
        "-test.run",
        "TestAPI",
    )


def test_launch_entrypoint_go_debug_tests_in_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает dlv test без package для тестов текущей директории."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    entrypoint = entrypoint_factory(alias="go-tests", command="go test")
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    _stub_cleanup(monkeypatch)
    replacements = _stub_replace(monkeypatch)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=("-test.run", "TestAPI"),
        debug=True,
        debug_port=2346,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    assert replacements[0]["command"] == (
        "/tools/dlv",
        "test",
        "--headless",
        "--listen=127.0.0.1:2346",
        "--api-version=2",
        "--output",
        str(tmp_path / "runpoint-dlv-2346"),
        "--",
        "-test.run",
        "TestAPI",
    )


def test_launch_entrypoint_go_debug_cleans_stale_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Очистка зависшего Delve выполняется до замены процесса."""
    entrypoint = entrypoint_factory(alias="go-api", command="go run ./cmd/api")
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    events: list[str] = []
    cleanup_calls = _stub_cleanup(monkeypatch, sink=events)
    _stub_replace(monkeypatch, sink=events)

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=True,
        debug_port=2345,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=lambda _message: None,
    )

    assert events == ["cleanup", "replace"]
    assert cleanup_calls[0]["port"] == _GO_DEBUG_PORT
    assert cleanup_calls[0]["matchers"] == services.debugger_matchers(Runtime.GO)


def test_launch_entrypoint_go_debug_prints_dlv_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сообщает адрес Delve без квалификатора ожидания."""
    entrypoint = entrypoint_factory(alias="go-api", command="go run ./cmd/api")
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    _stub_cleanup(monkeypatch)
    _stub_replace(monkeypatch)
    debug_messages: list[str] = []

    launch_entrypoint(
        config_dir=tmp_path,
        entrypoint=entrypoint,
        target_args=(),
        debug=True,
        debug_port=2345,
        no_env=False,
        print_message=lambda _message: None,
        print_debug=debug_messages.append,
    )

    assert debug_messages == ["dlv: 127.0.0.1:2345"]


def test_launch_entrypoint_rejects_go_build_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отклоняет debug для Go build до поиска не нужного ему Delve."""
    entrypoint = entrypoint_factory(alias="go-build", command="go build ./cmd/api")
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
    entrypoint = entrypoint_factory(alias="go-api", command="go run ./cmd/api")
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
            no_env=False,
            print_message=lambda _message: None,
            print_debug=lambda _message: None,
        )


def test_launch_entrypoint_rejects_ambiguous_go_debug_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не принимает Go-флаг за пакет Delve; target-аргументы требует после `--`."""
    entrypoint = entrypoint_factory(alias="go-tests", command="go test -run")
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    _stub_replace(monkeypatch)

    with pytest.raises(SystemExit, match=r"Некорректный target.*после '--'"):
        launch_entrypoint(
            config_dir=tmp_path,
            entrypoint=entrypoint,
            target_args=("TestAPI",),
            debug=True,
            debug_port=2345,
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
    entrypoint = entrypoint_factory(alias="go-tests", command=f"go test {target}")
    monkeypatch.setattr("shutil.which", lambda _executable: "/tools/dlv")
    _stub_replace(monkeypatch)

    with pytest.raises(SystemExit, match=r"package-паттерн.*один Go package"):
        launch_entrypoint(
            config_dir=tmp_path,
            entrypoint=entrypoint,
            target_args=(),
            debug=True,
            debug_port=2345,
            no_env=False,
            print_message=lambda _message: None,
            print_debug=lambda _message: None,
        )
