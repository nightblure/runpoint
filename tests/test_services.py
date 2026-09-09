"""Проверяет runtime-сервисы запуска."""

import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

import psutil
import pytest

from runpoint.domain import entrypoint_factory
from runpoint.services import (
    DebugpyAdapterMatcher,
    DelveMatcher,
    build_go_debug_command,
    build_python_command,
    cleanup_stale_debug_binary,
    cleanup_stale_debuggers,
    load_env_variables,
    resolve_python_executable,
    run_python_debug,
    validate_target,
)


def test_build_command_configures_debugpy_and_preserves_target_args() -> None:
    """Формирует полный debugpy-вызов с аргументами приложения."""
    entrypoint = entrypoint_factory(alias="worker", command="worker --mode safe")

    command = build_python_command(
        port=5679,
        debug=True,
        python_executable=Path("/project/.venv/bin/python"),
        entrypoint=entrypoint,
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
        "True",
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
        entrypoint=entrypoint,
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


def test_build_go_debug_command_runs_without_accept_multiclient_or_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает Go через single-client Delve без --accept-multiclient и --continue."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    entrypoint = entrypoint_factory(alias="go-api", command="go run ./cmd/api")

    command = build_go_debug_command(
        delve_executable=Path("/opt/bin/dlv"),
        entrypoint=entrypoint,
        target_args=("--port", "8080"),
        port=2345,
    )

    assert command == [
        "/opt/bin/dlv",
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
    ]


def test_build_go_debug_command_tests_without_accept_multiclient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запускает Go-тесты через single-client Delve."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    entrypoint = entrypoint_factory(alias="go-tests", command="go test ./internal/api")

    command = build_go_debug_command(
        delve_executable=Path("/opt/bin/dlv"),
        entrypoint=entrypoint,
        target_args=("-test.run", "TestAPI"),
        port=2346,
    )

    assert command == [
        "/opt/bin/dlv",
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


# --- Stale debugger matchers -------------------------------------------------


_ADAPTER_CMDLINE = [
    "/project/.venv/bin/python",
    "/project/.venv/lib/python3.12/site-packages/debugpy/adapter",
    "--for-server",
    "4242",
    "--port",
    "5678",
    "--access-token",
    "abc",
]


def test_debugpy_adapter_matcher_matches_exact_port() -> None:
    """Распознаёт debugpy adapter с точным --port."""
    matcher = DebugpyAdapterMatcher()

    assert matcher.matches(cmdline=_ADAPTER_CMDLINE, port=5678)


def test_debugpy_adapter_matcher_matches_main_py_path() -> None:
    """Допускает путь к adapter/__main__.py."""
    cmdline = list(_ADAPTER_CMDLINE)
    cmdline[1] = cmdline[1] + "/__main__.py"

    assert DebugpyAdapterMatcher().matches(cmdline=cmdline, port=5678)


def test_debugpy_adapter_matcher_matches_equals_form() -> None:
    """Допускает форму --port=5678."""
    cmdline = ["python", "/p/debugpy/adapter", "--for-server", "1", "--port=5678"]

    assert DebugpyAdapterMatcher().matches(cmdline=cmdline, port=5678)


def test_debugpy_adapter_matcher_rejects_different_port() -> None:
    """Не совпадает по чужому порту."""
    assert not DebugpyAdapterMatcher().matches(cmdline=_ADAPTER_CMDLINE, port=5679)


def test_debugpy_adapter_matcher_rejects_debugpy_server() -> None:
    """Не трогает обычный python -m debugpy (debug-сервер target)."""
    cmdline = [
        "python",
        "-Xfrozen_modules=off",
        "-m",
        "debugpy",
        "--listen",
        "127.0.0.1:5678",
        "--wait-for-client",
        "--configure-subProcess",
        "True",
        "-m",
        "app",
    ]

    assert not DebugpyAdapterMatcher().matches(cmdline=cmdline, port=5678)


def test_debugpy_adapter_matcher_rejects_non_adapter_python() -> None:
    """Не трогает произвольный python-процесс с --port."""
    cmdline = ["python", "app.py", "--port", "5678"]

    assert not DebugpyAdapterMatcher().matches(cmdline=cmdline, port=5678)


def test_debugpy_adapter_matcher_rejects_adapter_without_for_server() -> None:
    """Требует --for-server как маркер адаптера."""
    cmdline = ["python", "/p/debugpy/adapter", "--port", "5678"]

    assert not DebugpyAdapterMatcher().matches(cmdline=cmdline, port=5678)


def test_debugpy_adapter_matcher_rejects_empty_cmdline() -> None:
    """Не падает на пустой cmdline."""
    assert not DebugpyAdapterMatcher().matches(cmdline=[], port=5678)


_DLV_DEBUG_CMDLINE = [
    "/opt/bin/dlv",
    "debug",
    "--headless",
    "--listen=127.0.0.1:2345",
    "--api-version=2",
    "./cmd/api",
]


def test_delve_matcher_matches_listen_equals_form() -> None:
    """Распознаёт dlv debug --headless --listen=host:port."""
    assert DelveMatcher().matches(cmdline=_DLV_DEBUG_CMDLINE, port=2345)


def test_delve_matcher_matches_separate_listen_value() -> None:
    """Допускает форму --listen 127.0.0.1:port отдельным аргументом."""
    cmdline = [
        "/opt/bin/dlv",
        "test",
        "--headless",
        "--listen",
        "127.0.0.1:2345",
        "./pkg",
    ]

    assert DelveMatcher().matches(cmdline=cmdline, port=2345)


def test_delve_matcher_matches_delve_executable_name() -> None:
    """Допускает исполняемый файл с именем delve."""
    cmdline = ["/opt/bin/delve", "debug", "--headless", "--listen=127.0.0.1:2345", "."]

    assert DelveMatcher().matches(cmdline=cmdline, port=2345)


def test_delve_matcher_matches_empty_host_listen_form() -> None:
    """Допускает форму --listen=:port."""
    cmdline = ["/opt/bin/dlv", "dap", "--listen=:2345"]

    assert DelveMatcher().matches(cmdline=cmdline, port=2345)


def test_delve_matcher_rejects_different_port() -> None:
    """Не совпадает по чужому порту."""
    assert not DelveMatcher().matches(cmdline=_DLV_DEBUG_CMDLINE, port=2346)


def test_delve_matcher_rejects_non_delve_process() -> None:
    """Не трогает процесс с --listen, не являющийся dlv/delve."""
    cmdline = ["python", "app.py", "--listen", "127.0.0.1:2345"]

    assert not DelveMatcher().matches(cmdline=cmdline, port=2345)


def test_delve_matcher_rejects_interactive_delve_without_listen() -> None:
    """Не трогает интерактивный dlv без --listen."""
    cmdline = ["/opt/bin/dlv", "debug", "main.go"]

    assert not DelveMatcher().matches(cmdline=cmdline, port=2345)


def test_delve_matcher_rejects_empty_cmdline() -> None:
    """Не падает на пустой cmdline."""
    assert not DelveMatcher().matches(cmdline=[], port=2345)


# --- Stale debugger cleanup -------------------------------------------------


class _FakeProcess:
    """Имитирует psutil.Process для тестов очистки."""

    def __init__(  # noqa: PLR0913
        self,
        pid: int,
        cmdline: list[str],
        *,
        cmdline_raises: BaseException | None = None,
        terminate_raises: BaseException | None = None,
        kill_raises: BaseException | None = None,
        wait_after_term: BaseException | None = None,
        wait_after_kill: BaseException | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.pid = pid
        self._cmdline = list(cmdline)
        self._cmdline_raises = cmdline_raises
        self._terminate_raises = terminate_raises
        self._kill_raises = kill_raises
        self._wait_after_term = wait_after_term
        self._wait_after_kill = wait_after_kill
        self._events = events
        self._last: str | None = None
        self.terminated = False
        self.killed = False

    def cmdline(self) -> list[str]:
        if self._cmdline_raises is not None:
            raise self._cmdline_raises
        return list(self._cmdline)

    def terminate(self) -> None:
        self.terminated = True
        self._last = "term"
        if self._events is not None:
            self._events.append("cleanup")
        if self._terminate_raises is not None:
            raise self._terminate_raises

    def kill(self) -> None:
        self.killed = True
        self._last = "kill"
        if self._kill_raises is not None:
            raise self._kill_raises

    def wait(self, *, timeout: float | None = None) -> None:  # noqa: ARG002
        if self._last == "term" and self._wait_after_term is not None:
            raise self._wait_after_term
        if self._last == "kill" and self._wait_after_kill is not None:
            raise self._wait_after_kill


def _patch_processes(
    monkeypatch: pytest.MonkeyPatch,
    processes: list[_FakeProcess],
) -> None:
    monkeypatch.setattr(
        "runpoint.services.psutil.process_iter",
        lambda: iter(processes),
    )


def _adapter_cmdline(port: int) -> list[str]:
    return [
        "python",
        "/p/debugpy/adapter",
        "--for-server",
        "4242",
        "--port",
        str(port),
    ]


def test_cleanup_terminates_matching_debugger_on_exact_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Завершает зависший debugpy adapter на точном порту через SIGTERM."""
    proc = _FakeProcess(101, _adapter_cmdline(5678))
    messages: list[str] = []
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=messages.append,
    )

    assert proc.terminated
    assert not proc.killed
    assert any("PID 101" in message for message in messages)


def test_cleanup_skips_foreign_process_on_different_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не трогает adapter на другом порту."""
    proc = _FakeProcess(101, _adapter_cmdline(9999))
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert not proc.terminated


def test_cleanup_skips_non_debugger_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не трогает произвольный python-процесс."""
    proc = _FakeProcess(101, ["python", "app.py", "--port", "5678"])
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert not proc.terminated


def test_cleanup_escalates_to_sigkill_when_terminate_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """После bounded-ожидания повторяет terminate через kill."""
    proc = _FakeProcess(
        101,
        _adapter_cmdline(5678),
        wait_after_term=psutil.TimeoutExpired(2),
    )
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert proc.terminated
    assert proc.killed


def test_cleanup_logs_when_sigkill_does_not_reap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сообщает, если процесс не завершился даже после SIGKILL."""
    proc = _FakeProcess(
        101,
        _adapter_cmdline(5678),
        wait_after_term=psutil.TimeoutExpired(2),
        wait_after_kill=psutil.TimeoutExpired(2),
    )
    messages: list[str] = []
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=messages.append,
    )

    assert proc.terminated
    assert proc.killed
    assert any("SIGKILL" in message for message in messages)


def test_cleanup_skips_vanished_process_during_cmdline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пропускает процесс, исчезнувший до чтения cmdline."""
    proc = _FakeProcess(101, [], cmdline_raises=psutil.NoSuchProcess(101))
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert not proc.terminated


def test_cleanup_skips_access_denied_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пропускает процесс без прав на чтение cmdline."""
    proc = _FakeProcess(101, [], cmdline_raises=psutil.AccessDenied(101))
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert not proc.terminated


def test_cleanup_terminates_only_matching_among_many(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Завершает только matching debugger среди набора процессов."""
    foreign_port = _FakeProcess(101, _adapter_cmdline(9999))
    matching = _FakeProcess(202, _adapter_cmdline(5678))
    foreign_app = _FakeProcess(303, ["python", "app.py", "--port", "5678"])
    _patch_processes(monkeypatch, [foreign_port, matching, foreign_app])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert not foreign_port.terminated
    assert matching.terminated
    assert not foreign_app.terminated


def test_cleanup_handles_process_vanished_before_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не падает, если matching процесс исчез перед SIGTERM."""
    proc = _FakeProcess(
        101,
        _adapter_cmdline(5678),
        terminate_raises=psutil.NoSuchProcess(101),
    )
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert not proc.killed


def test_cleanup_terminates_stale_delve_with_delve_matcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Универсальный механизм завершает и зависший Delve через DelveMatcher."""
    proc = _FakeProcess(
        101,
        ["/opt/bin/dlv", "debug", "--headless", "--listen=127.0.0.1:2345", "."],
    )
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=2345,
        matchers=(DelveMatcher(),),
        print_debug=lambda _message: None,
    )

    assert proc.terminated


def test_cleanup_logs_when_terminate_raises_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Логирует и продолжает, если нет прав на SIGTERM."""
    proc = _FakeProcess(
        101,
        _adapter_cmdline(5678),
        terminate_raises=psutil.AccessDenied(101),
    )
    messages: list[str] = []
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=messages.append,
    )

    assert not proc.killed
    assert any("PID 101" in m and "освободите" in m for m in messages)


def test_cleanup_skips_zombie_process_on_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Зомби-процесс уже мёртв — пропускает без попытки kill."""
    proc = _FakeProcess(
        101,
        _adapter_cmdline(5678),
        terminate_raises=psutil.ZombieProcess(101),
    )
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert not proc.killed


def test_cleanup_logs_when_kill_raises_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Логирует, если SIGKILL недоступен из-за прав."""
    proc = _FakeProcess(
        101,
        _adapter_cmdline(5678),
        wait_after_term=psutil.TimeoutExpired(2),
        kill_raises=psutil.AccessDenied(101),
    )
    messages: list[str] = []
    _patch_processes(monkeypatch, [proc])

    cleanup_stale_debuggers(
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=messages.append,
    )

    assert proc.terminated
    assert proc.killed
    assert any("PID 101" in m and "освободите" in m for m in messages)


def test_run_python_debug_proceeds_after_failed_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запуск продолжается, даже если SIGKILL не завершил зависший adapter."""
    psutil_fake = _FakeProcess(
        101,
        _adapter_cmdline(5678),
        wait_after_term=psutil.TimeoutExpired(2),
        wait_after_kill=psutil.TimeoutExpired(2),
    )
    popen_fake = _FakePopen(returncode=0)
    _patch_processes(monkeypatch, [psutil_fake])
    _patch_popen(monkeypatch, popen_fake, [], [])

    code = run_python_debug(
        command=["python", "-m", "app"],
        working_dir=Path("/project"),
        environment={},
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert code == 0
    assert psutil_fake.terminated
    assert psutil_fake.killed


def test_cleanup_stale_debug_binary_removes_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Удаляет оставшийся dlv-бинарник перед запуском."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    binary = tmp_path / "runpoint-dlv-2345"
    binary.touch()

    cleanup_stale_debug_binary(port=2345)

    assert not binary.exists()


def test_cleanup_stale_debug_binary_skips_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не падает, если бинарник уже отсутствует."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    cleanup_stale_debug_binary(port=2345)


# --- Python debug lifecycle -------------------------------------------------

_SIGINT_EXIT_CODE = 130
_SIGTERM_EXIT_CODE = 143


class _FakePopen:
    """Имитирует subprocess.Popen для тестов lifecycle отладки."""

    def __init__(
        self,
        *,
        returncode: int | None = 0,
        wait_sequence: Sequence[BaseException | None] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.returncode = returncode
        self._wait_sequence: Sequence[BaseException | None] = (
            wait_sequence if wait_sequence is not None else ()
        )
        self._waits = 0
        self._events = events
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int | None:  # noqa: ARG002
        if self._events is not None:
            self._events.append("wait")

        index = self._waits
        self._waits += 1
        if index < len(self._wait_sequence):
            outcome = self._wait_sequence[index]
            if outcome is not None:
                raise outcome
        return self.returncode

    def terminate(self) -> None:
        if self._events is not None:
            self._events.append("terminate")
        self.terminated = True

    def kill(self) -> None:
        if self._events is not None:
            self._events.append("kill")
        self.killed = True


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakePopen,
    events: list[str],
    calls: list[dict[str, object]],
) -> None:
    def popen(
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> _FakePopen:
        events.append("launch")
        calls.append({"command": command, "cwd": cwd, "env": env, "kwargs": kwargs})
        return fake

    monkeypatch.setattr("runpoint.services.subprocess.Popen", popen)


def test_run_python_debug_cleans_before_launch_and_in_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Очистка адаптера выполняется до запуска и в finally, код 0."""
    events: list[str] = []
    psutil_fake = _FakeProcess(101, _adapter_cmdline(5678), events=events)
    popen_fake = _FakePopen(returncode=0, events=events)
    _patch_processes(monkeypatch, [psutil_fake])
    calls: list[dict[str, object]] = []
    _patch_popen(monkeypatch, popen_fake, events, calls)

    code = run_python_debug(
        command=["python", "-m", "app"],
        working_dir=Path("/project"),
        environment={"X": "1"},
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert code == 0
    assert events == ["cleanup", "launch", "wait", "cleanup"]
    assert calls == [
        {
            "command": ["python", "-m", "app"],
            "cwd": "/project",
            "env": {"X": "1"},
            "kwargs": {},
        }
    ]


def test_run_python_debug_returns_nonzero_exit_code_and_cleans_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При ненулевом exit code finally всё равно очищает adapter."""
    events: list[str] = []
    psutil_fake = _FakeProcess(101, _adapter_cmdline(5678), events=events)
    popen_fake = _FakePopen(returncode=1, events=events)
    _patch_processes(monkeypatch, [psutil_fake])
    _patch_popen(monkeypatch, popen_fake, events, [])

    code = run_python_debug(
        command=["python", "-m", "app"],
        working_dir=Path("/project"),
        environment={},
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert code == 1
    assert events == ["cleanup", "launch", "wait", "cleanup"]


def test_run_python_debug_returns_130_on_sigint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C: ждёт завершения child и возвращает 130, finally чистит adapter."""
    events: list[str] = []
    psutil_fake = _FakeProcess(101, _adapter_cmdline(5678), events=events)
    popen_fake = _FakePopen(
        returncode=-2,
        wait_sequence=[KeyboardInterrupt()],
        events=events,
    )
    _patch_processes(monkeypatch, [psutil_fake])
    _patch_popen(monkeypatch, popen_fake, events, [])

    code = run_python_debug(
        command=["python", "-m", "app"],
        working_dir=Path("/project"),
        environment={},
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert code == _SIGINT_EXIT_CODE
    assert events == ["cleanup", "launch", "wait", "wait", "cleanup"]


def test_run_python_debug_translates_signal_death_to_shell_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Гибель child от сигнала отображается как 128 + signum."""
    events: list[str] = []
    psutil_fake = _FakeProcess(101, _adapter_cmdline(5678), events=events)
    popen_fake = _FakePopen(returncode=-15, events=events)
    _patch_processes(monkeypatch, [psutil_fake])
    _patch_popen(monkeypatch, popen_fake, events, [])

    code = run_python_debug(
        command=["python", "-m", "app"],
        working_dir=Path("/project"),
        environment={},
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert code == _SIGTERM_EXIT_CODE
    assert events == ["cleanup", "launch", "wait", "cleanup"]


def test_run_python_debug_escalates_to_sigterm_when_child_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C + child не умирает по SIGINT → SIGTERM → bounded wait → SIGKILL."""
    events: list[str] = []
    psutil_fake = _FakeProcess(101, _adapter_cmdline(5678), events=events)
    popen_fake = _FakePopen(
        returncode=-9,
        wait_sequence=[
            KeyboardInterrupt(),
            subprocess.TimeoutExpired("test", 5),
            subprocess.TimeoutExpired("test", 5),
        ],
        events=events,
    )
    _patch_processes(monkeypatch, [psutil_fake])
    _patch_popen(monkeypatch, popen_fake, events, [])

    code = run_python_debug(
        command=["python", "-m", "app"],
        working_dir=Path("/project"),
        environment={},
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert code == 128 + 9
    assert popen_fake.terminated
    assert popen_fake.killed
    assert events == [
        "cleanup",
        "launch",
        "wait",
        "wait",
        "terminate",
        "wait",
        "kill",
        "wait",
        "cleanup",
    ]


def test_run_python_debug_sigkill_on_double_ctrl_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повторный Ctrl-C → немедленный SIGKILL, минуя SIGTERM."""
    events: list[str] = []
    psutil_fake = _FakeProcess(101, _adapter_cmdline(5678), events=events)
    popen_fake = _FakePopen(
        returncode=-9,
        wait_sequence=[
            KeyboardInterrupt(),
            KeyboardInterrupt(),
        ],
        events=events,
    )
    _patch_processes(monkeypatch, [psutil_fake])
    _patch_popen(monkeypatch, popen_fake, events, [])

    code = run_python_debug(
        command=["python", "-m", "app"],
        working_dir=Path("/project"),
        environment={},
        port=5678,
        matchers=(DebugpyAdapterMatcher(),),
        print_debug=lambda _message: None,
    )

    assert code == 128 + 9
    assert popen_fake.killed
    assert not popen_fake.terminated
    assert events == [
        "cleanup",
        "launch",
        "wait",
        "wait",
        "kill",
        "wait",
        "cleanup",
    ]
