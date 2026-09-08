"""Проверяет публичный контракт CLI."""

from pathlib import Path
from unittest.mock import ANY

import pytest
from click import unstyle
from typer.testing import CliRunner

from runpoint import use_cases
from runpoint.cli import LauncherContext, app, main, split_launcher_and_target_args
from runpoint.domain import entrypoint_factory

runner = CliRunner()
USAGE_ERROR_EXIT_CODE = 2


def test_main_discovers_runpoint_jsonc_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Находит новый JSONC-конфиг через публичный bootstrap CLI."""
    config_path = tmp_path / ".runpoint.jsonc"
    config_path.write_text(
        '[{"alias": "api", "command": "python -m api"}]',
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
        entrypoints=(
            entrypoint_factory(alias="worker", command="python worker.py", cwd="src"),
            entrypoint_factory(alias="api", command="python -m api"),
        ),
    )

    result = runner.invoke(app, ["--list"], obj=context)
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert output == (
        "api       python -m api\nworker    python worker.py  (cwd=src)\n"
    )


def test_missing_alias_keeps_cli_error() -> None:
    """Сообщает об обязательном алиасе прежним текстом."""
    context = LauncherContext(config_dir=Path(), target_args=(), entrypoints=())

    result = runner.invoke(app, [], obj=context)
    output = unstyle(result.output)

    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "Нужно указать алиас точки входа; доступные алиасы: --list" in output


def test_unknown_alias_keeps_cli_error() -> None:
    """Сообщает о неизвестном алиасе прежним текстом."""
    context = LauncherContext(config_dir=Path(), target_args=(), entrypoints=())

    result = runner.invoke(app, ["missing"], obj=context)
    output = unstyle(result.output)

    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "Алиас 'missing' не найден" in output


def test_run_delegates_selected_entrypoint_and_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Передаёт use-case выбранную точку входа и параметры запуска."""
    entrypoint = entrypoint_factory(alias="worker", command="worker")
    context = LauncherContext(
        config_dir=Path("/project"),
        target_args=("--limit", "10"),
        entrypoints=(entrypoint,),
    )
    calls: list[dict[str, object]] = []

    def launch_entrypoint(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(use_cases, "launch_entrypoint", launch_entrypoint)

    result = runner.invoke(
        app,
        ["worker", "--debug", "--debug-port", "5679", "--no-debug-wait", "--no-env"],
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
            "no_debug_wait": True,
            "debug_subprocesses": True,
            "no_env": True,
            "print_message": ANY,
            "print_debug": ANY,
        }
    ]


def test_run_rejects_only_explicit_debug_subprocesses_for_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejects the Python-only option for Go only when explicitly supplied."""
    entrypoint = entrypoint_factory(
        alias="go-api",
        command="go run ./cmd/api",
    )
    context = LauncherContext(
        config_dir=Path("/project"),
        target_args=(),
        entrypoints=(entrypoint,),
    )
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        use_cases,
        "launch_entrypoint",
        lambda **kwargs: calls.append(kwargs),
    )

    default_result = runner.invoke(app, ["go-api", "--debug"], obj=context)
    explicit_result = runner.invoke(
        app,
        ["go-api", "--debug", "--debug-subprocesses"],
        obj=context,
    )

    assert default_result.exit_code == 0
    assert len(calls) == 1
    assert explicit_result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "--debug-subprocesses не поддерживается для Go" in unstyle(
        explicit_result.output
    )
