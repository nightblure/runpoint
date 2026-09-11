"""Проверяет публичный контракт CLI."""

from pathlib import Path
from unittest.mock import ANY

import pytest
from click import unstyle
from typer.testing import CliRunner

from runpoint import use_cases
from runpoint.cli import LauncherContext, app, main, split_launcher_and_target_args
from runpoint.domain import GlobalConfig, entrypoint_factory

runner = CliRunner()
USAGE_ERROR_EXIT_CODE = 2
CHILD_EXIT_CODE = 42


def test_main_discovers_runpoint_jsonc_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Находит новый JSONC-конфиг через публичный bootstrap CLI."""
    config_path = tmp_path / ".runpoint.jsonc"
    config_path.write_text(
        """{
            "entrypoints": [
                {"alias": "api", "command": "python -m api"}
            ],
            "global_config": {}
        }""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["runpoint", "--list"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0


def test_split_launcher_and_target_args_uses_first_separator() -> None:
    """Передаёт приложению всё после первого двойного дефиса."""
    launcher_args, target_args = split_launcher_and_target_args(
        ("worker", "--debug", "--", "--limit", "10", "--", "tail"),
    )

    assert (launcher_args, target_args) == (
        ["worker", "--debug"],
        ["--limit", "10", "--", "tail"],
    )


def test_list_prints_entrypoints_sorted_by_alias() -> None:
    """Печатает зарегистрированные точки входа в порядке алиасов."""
    context = LauncherContext(
        config_dir=Path("/project"),
        target_args=(),
        global_config=GlobalConfig(),
        entrypoints=(
            entrypoint_factory(alias="worker", command="python worker.py", cwd="src"),
            entrypoint_factory(alias="api", command="python -m api"),
        ),
    )

    result = runner.invoke(app, ["--list"], obj=context)
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert output == "api       python -m api\nworker    python worker.py  (cwd=src)\n"


def test_missing_alias_keeps_cli_error() -> None:
    """Сообщает об обязательном алиасе прежним текстом."""
    context = LauncherContext(
        config_dir=Path(),
        target_args=(),
        global_config=GlobalConfig(),
        entrypoints=(),
    )

    result = runner.invoke(app, [], obj=context)
    output = unstyle(result.output)

    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "Нужно указать алиас точки входа; доступные алиасы: --list" in output


def test_unknown_alias_keeps_cli_error() -> None:
    """Сообщает о неизвестном алиасе прежним текстом."""
    context = LauncherContext(
        config_dir=Path(),
        target_args=(),
        global_config=GlobalConfig(),
        entrypoints=(),
    )

    result = runner.invoke(app, ["missing"], obj=context)
    output = unstyle(result.output)

    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "Алиас 'missing' не найден" in output


def test_run_uses_cli_debug_port_and_delegates_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Передаёт use-case CLI-порт, выбранную точку входа и параметры запуска."""
    entrypoint = entrypoint_factory(alias="worker", command="worker")
    context = LauncherContext(
        config_dir=Path("/project"),
        target_args=("--limit", "10"),
        global_config=GlobalConfig(),
        entrypoints=(entrypoint,),
    )
    calls: list[dict[str, object]] = []

    def launch_entrypoint(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(use_cases, "launch_entrypoint", launch_entrypoint)

    result = runner.invoke(
        app,
        ["worker", "--debug", "--debug-port", "5679", "--no-env"],
        obj=context,
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "config_dir": Path("/project"),
            "entrypoint": entrypoint,
            "target_args": ("--limit", "10"),
            "debug": True,
            "debug_port": 5679,
            "no_env": True,
            "print_message": ANY,
            "print_debug": ANY,
        }
    ]


@pytest.mark.parametrize(
    ("entrypoint_debug_port", "global_debug_port", "expected_debug_port"),
    [
        pytest.param(5680, None, 5680, id="entrypoint"),
        pytest.param(None, 5681, 5681, id="global"),
        pytest.param(5680, 5681, 5680, id="entrypoint-over-global"),
    ],
)
def test_run_selects_configured_debug_port(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint_debug_port: int | None,
    global_debug_port: int | None,
    expected_debug_port: int,
) -> None:
    """Выбирает порт точки входа, затем глобальный порт."""
    entrypoint = entrypoint_factory(
        alias="worker",
        command="worker",
        debug_port=entrypoint_debug_port,
    )
    context = LauncherContext(
        config_dir=Path("/project"),
        target_args=(),
        global_config=GlobalConfig(debug_port=global_debug_port),
        entrypoints=(entrypoint,),
    )
    calls: list[dict[str, object]] = []

    def launch_entrypoint(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(use_cases, "launch_entrypoint", launch_entrypoint)

    result = runner.invoke(app, ["worker", "--debug"], obj=context)

    assert result.exit_code == 0
    assert [call["debug_port"] for call in calls] == [expected_debug_port]


@pytest.mark.parametrize(
    ("entrypoint_debug_port", "global_debug_port", "message"),
    [
        pytest.param(5680, None, "точке входа 'worker'", id="entrypoint-and-cli"),
        pytest.param(None, 5681, "глобальном конфиге", id="global-and-cli"),
    ],
)
def test_run_rejects_cli_debug_port_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint_debug_port: int | None,
    global_debug_port: int | None,
    message: str,
) -> None:
    """Не допускает одновременный CLI-порт и порт из конфигурации."""
    entrypoint = entrypoint_factory(
        alias="worker",
        command="worker",
        debug_port=entrypoint_debug_port,
    )
    context = LauncherContext(
        config_dir=Path("/project"),
        target_args=(),
        global_config=GlobalConfig(debug_port=global_debug_port),
        entrypoints=(entrypoint,),
    )
    monkeypatch.setattr(
        use_cases,
        "launch_entrypoint",
        lambda **_kwargs: pytest.fail("launch_entrypoint не должен вызываться"),
    )

    result = runner.invoke(
        app,
        ["worker", "--debug", "--debug-port", "5682"],
        obj=context,
    )
    output = unstyle(result.output)

    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert message in output


def test_run_propagates_python_debug_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пробрасывает exit code дочернего отладочного процесса."""
    entrypoint = entrypoint_factory(alias="worker", command="worker")
    context = LauncherContext(
        config_dir=Path("/project"),
        target_args=(),
        global_config=GlobalConfig(),
        entrypoints=(entrypoint,),
    )
    monkeypatch.setattr(use_cases, "launch_entrypoint", lambda **_kwargs: CHILD_EXIT_CODE)

    result = runner.invoke(app, ["worker", "--debug"], obj=context)

    assert result.exit_code == CHILD_EXIT_CODE
