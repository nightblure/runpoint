"""Проверяет загрузку и поиск конфигурации."""

import json
from pathlib import Path

import pytest

from runpoint.data import find_config_path, load_entrypoints, load_raw_config
from runpoint.domain import Runtime


def test_load_entrypoints_rejects_duplicate_aliases(tmp_path: Path) -> None:
    """Запрещает неоднозначную конфигурацию с повторяющимися алиасами."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        '[{"alias": "api", "command": "first"},{"alias": "api", "command": "second"}]',
        encoding="utf-8",
    )

    with pytest.raises(
        SystemExit,
        match="Алиасы точек входа должны быть уникальными",
    ):
        load_entrypoints(config_path)


def test_load_entrypoints_reads_jsonc_values_and_defaults(tmp_path: Path) -> None:
    """Загружает JSONC и применяет значения путей по умолчанию."""
    config_path = tmp_path / ".runpoint.jsonc"
    config_path.write_text(
        """[
            // The URL contains comment-like characters and must stay intact.
            {
                "alias": "api",
                "command": "service --port 8000",
                "env": {"URL": "https://example.test/api"}
            }
        ]""",
        encoding="utf-8",
    )

    entrypoint = load_entrypoints(config_path)[0]

    assert (
        entrypoint.alias,
        entrypoint.command_args(),
        entrypoint.cwd,
        entrypoint.venv,
        entrypoint.env_file,
        entrypoint.load_env_file,
        dict(entrypoint.env),
    ) == (
        "api",
        ("-m", "service", "--port", "8000"),
        Path(),
        Path(".venv"),
        Path(".env"),
        False,
        {"URL": "https://example.test/api"},
    )


@pytest.mark.parametrize(
    "command",
    [
        "go build cmd/app/main.go && go run cmd/app/main.go",
        "-m api || -m fallback",
        "run ./cmd/api; test ./...",
        "-m api | tee output.log",
        "run ./cmd/api &",
        "-m api > output.log",
        "-m api < input.txt",
        "build ./cmd/app\nrun ./cmd/app",
        "run $(helper)",
        "-m api (value)",
        "-m api `helper`",
        '-m api "$(helper)"',
    ],
)
def test_load_entrypoints_rejects_shell_command_composition(
    tmp_path: Path,
    command: str,
) -> None:
    """Не принимает shell-операторы за аргументы единственной команды."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        json.dumps([{"alias": "unsafe", "command": command}]),
        encoding="utf-8",
    )

    with pytest.raises(
        SystemExit,
        match=r"shell-операторы.*одну команду",
    ):
        load_entrypoints(config_path)


def test_load_entrypoints_preserves_quoted_shell_characters_and_empty_args(
    tmp_path: Path,
) -> None:
    """Не принимает литералы внутри кавычек за shell-композицию."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "alias": "api",
                    "command": "python -m api '' '>' 'a&b' '(value)' '$(literal)'",
                }
            ]
        ),
        encoding="utf-8",
    )

    entrypoint = load_entrypoints(config_path)[0]

    assert entrypoint.command_args() == (
        "-m",
        "api",
        "",
        ">",
        "a&b",
        "(value)",
        "$(literal)",
    )


def test_load_entrypoints_detects_python_and_go_runtimes(
    tmp_path: Path,
) -> None:
    """Выводит runtime из формы настроенной команды."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        """[
            {"alias": "api", "command": "api"},
            {"alias": "worker", "command": "python scripts/worker.PY"},
            {"alias": "go-api", "command": "go run ./cmd/api"}
        ]""",
        encoding="utf-8",
    )

    module_entrypoint, script_entrypoint, go_entrypoint = load_entrypoints(config_path)

    assert module_entrypoint.runtime is Runtime.PYTHON
    assert script_entrypoint.runtime is Runtime.PYTHON
    assert go_entrypoint.runtime is Runtime.GO
    assert go_entrypoint.command_args() == ("run", "./cmd/api")


def test_load_entrypoints_rejects_unrecognized_runtime_shape(tmp_path: Path) -> None:
    """Перечисляет формы команд всех runtime, если ни один не распознан."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        '[{"alias": "format", "command": "--version"}]',
        encoding="utf-8",
    )

    with pytest.raises(
        SystemExit,
        match=(
            r"Не удалось определить runtime.*Поддерживаемые формы.*"
            r"Python.*\.py.*Go.*test.*run.*build"
        ),
    ):
        load_entrypoints(config_path)


def test_load_entrypoints_rejects_removed_runtime_field(
    tmp_path: Path,
) -> None:
    """Публичный loader считает удалённый runtime неизвестным полем."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        '[{"alias": "go-api", "runtime": "go", "command": "go run ./cmd/api"}]',
        encoding="utf-8",
    )

    with pytest.raises(
        SystemExit,
        match=r"Найдены неизвестные поля 'go-api': runtime",
    ):
        load_entrypoints(config_path)


def test_load_entrypoints_rejects_dotenv_for_go_tests(tmp_path: Path) -> None:
    """Не разрешает Go-тестам загружать dotenv."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        """[
            {
                "alias": "go-tests",
                "command": "go test ./...",
                "load_env_file": true
            }
        ]""",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Тесты не должны загружать энвы"):
        load_entrypoints(config_path)


@pytest.mark.parametrize(
    ("operation", "target", "suggested_package"),
    [
        ("run", "cmd/app/main.go", "./cmd/app"),
        ("test", "main.go", "."),
        ("build", "/project/cmd/app/main.go", "/project/cmd/app"),
        ("build -v", "main.go", "."),
    ],
)
def test_load_entrypoints_rejects_go_source_file_targets(
    tmp_path: Path,
    operation: str,
    target: str,
    suggested_package: str,
) -> None:
    """Объясняет риск неполной компиляции и предлагает директорию package."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        json.dumps(
            [{"alias": "go-target", "command": f"go {operation} {target}"}],
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exit_info:
        load_entrypoints(config_path)

    message = str(exit_info.value)
    assert target in message
    assert "компилирует только перечисленные .go-файлы" in message
    assert f"укажите package {suggested_package!r}" in message


def test_load_entrypoints_allows_go_extension_for_build_output(
    tmp_path: Path,
) -> None:
    """Не принимает значение build-флага -o за исходный Go-файл."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        '[{"alias": "go-build", "command": "go build -o dist/app.go ./cmd/app"}]',
        encoding="utf-8",
    )

    entrypoint = load_entrypoints(config_path)[0]

    assert entrypoint.command_args() == (
        "build",
        "-o",
        "dist/app.go",
        "./cmd/app",
    )


@pytest.mark.parametrize(
    "command",
    [
        "go build -overlay overlay.go ./cmd/app",
        "go test -run TestFoo.go ./internal/api",
        "go test ./internal/api -args fixture.go",
    ],
)
def test_load_entrypoints_does_not_treat_go_option_values_as_source_targets(
    tmp_path: Path,
    command: str,
) -> None:
    """Проверяет только package/file-позиции Go-команды."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        json.dumps([{"alias": "go-command", "command": command}]),
        encoding="utf-8",
    )
    raw_config = load_raw_config(config_path)

    entrypoint = load_entrypoints(raw_config)[0]

    assert entrypoint.command_args() == tuple(command.split()[1:])


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[", "Некорректный JSON"),
        ("{}", "должен быть список JSON-объектов"),
        ("[]", "Конфигурация пуста"),
        (
            '[{"alias": "api", "command": "-m api", "unknown": true}]',
            "Найдены неизвестные поля",
        ),
        (
            '[{"alias": "api", "command": "-m api", "env": {"PORT": 8000}}]',
            "Поле env точки входа",
        ),
    ],
)
def test_load_entrypoints_rejects_invalid_config(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    """Отклоняет структурно некорректные конфигурации."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(content, encoding="utf-8")
    raw_config = load_raw_config(config_path)

    with pytest.raises(SystemExit, match=message):
        load_entrypoints(raw_config)


def test_find_config_path_returns_nearest_parent_config(tmp_path: Path) -> None:
    """Выбирает ближайший конфиг при поиске вверх по дереву."""
    outer_config = tmp_path / ".runpoint.json"
    outer_config.touch()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    nearest_config = project_dir / ".runpoint.json"
    nearest_config.touch()
    working_dir = project_dir / "src" / "service"
    working_dir.mkdir(parents=True)

    config_path = find_config_path(
        cwd=working_dir,
        cfg_filename=".runpoint.json",
    )

    assert config_path == nearest_config
