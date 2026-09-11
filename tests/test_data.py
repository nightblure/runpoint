"""Проверяет загрузку и поиск конфигурации."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from runpoint.data import find_config_path, load_entrypoints, load_global_config, load_raw_config
from runpoint.domain import GlobalConfig, Runtime

if TYPE_CHECKING:
    from collections.abc import Callable

DEBUG_PORT = 5679


@pytest.fixture
def config_file_factory(tmp_path: Path) -> Callable[[object], Path]:
    """Создаёт JSON-конфигурацию для проверки публичных loaders."""

    def create(raw_config: object) -> Path:
        config_path = tmp_path / ".runpoint.json"
        config_path.write_text(json.dumps(raw_config), encoding="utf-8")
        return config_path

    return create


def test_load_entrypoints_rejects_duplicate_aliases(
    config_file_factory: Callable[[object], Path],
) -> None:
    """Запрещает неоднозначную конфигурацию с повторяющимися алиасами."""
    config_path = config_file_factory(
        {
            "entrypoints": [
                {"alias": "api", "command": "first"},
                {"alias": "api", "command": "second"},
            ]
        }
    )
    raw_config = load_raw_config(config_path)

    with pytest.raises(
        SystemExit,
        match="Алиасы точек входа должны быть уникальными",
    ):
        load_entrypoints(raw_config)


def test_load_entrypoints_reads_jsonc_values_and_defaults(tmp_path: Path) -> None:
    """Загружает JSONC и применяет значения путей по умолчанию."""
    config_path = tmp_path / ".runpoint.jsonc"
    config_path.write_text(
        """{
            "entrypoints": [
                // The URL contains comment-like characters and must stay intact.
                {
                    "alias": "api",
                    "command": "service --port 8000",
                    "env": {"URL": "https://example.test/api"}
                }
            ],
            "global_config": {}
        }""",
        encoding="utf-8",
    )
    raw_config = load_raw_config(config_path)

    entrypoint = load_entrypoints(raw_config)[0]

    assert (
        entrypoint.alias,
        entrypoint.command_args(),
        entrypoint.cwd,
        entrypoint.venv,
        entrypoint.env_file,
        entrypoint.load_env_file,
        entrypoint.debug_port,
        dict(entrypoint.env),
    ) == (
        "api",
        ("-m", "service", "--port", "8000"),
        Path(),
        Path(".venv"),
        Path(".env"),
        False,
        None,
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
        "-m api \u0022$(helper)\u0022",
    ],
)
def test_load_entrypoints_rejects_shell_command_composition(
    config_file_factory: Callable[[object], Path],
    command: str,
) -> None:
    """Не принимает shell-операторы за аргументы единственной команды."""
    config_path = config_file_factory({"entrypoints": [{"alias": "unsafe", "command": command}]})
    raw_config = load_raw_config(config_path)

    with pytest.raises(
        SystemExit,
        match=r"shell-операторы.*одну команду",
    ):
        load_entrypoints(raw_config)


def test_load_entrypoints_preserves_quoted_shell_characters_and_empty_args(
    config_file_factory: Callable[[object], Path],
) -> None:
    """Не принимает литералы внутри кавычек за shell-композицию."""
    config_path = config_file_factory(
        {
            "entrypoints": [
                {
                    "alias": "api",
                    "command": "python -m api '' '>' 'a&b' '(value)' '$(literal)'",
                }
            ]
        }
    )
    raw_config = load_raw_config(config_path)

    entrypoint = load_entrypoints(raw_config)[0]

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
    config_file_factory: Callable[[object], Path],
) -> None:
    """Выводит runtime из формы настроенной команды."""
    config_path = config_file_factory(
        {
            "entrypoints": [
                {"alias": "api", "command": "api"},
                {"alias": "worker", "command": "python scripts/worker.PY"},
                {"alias": "go-api", "command": "go run ./cmd/api"},
            ]
        }
    )
    raw_config = load_raw_config(config_path)

    module_entrypoint, script_entrypoint, go_entrypoint = load_entrypoints(raw_config)

    assert module_entrypoint.runtime is Runtime.PYTHON
    assert script_entrypoint.runtime is Runtime.PYTHON
    assert go_entrypoint.runtime is Runtime.GO
    assert go_entrypoint.command_args() == ("run", "./cmd/api")


def test_load_entrypoints_rejects_unrecognized_runtime_shape(
    config_file_factory: Callable[[object], Path],
) -> None:
    """Перечисляет формы команд всех runtime, если ни один не распознан."""
    config_path = config_file_factory({"entrypoints": [{"alias": "format", "command": "--version"}]})
    raw_config = load_raw_config(config_path)

    with pytest.raises(
        SystemExit,
        match=(
            r"Не удалось определить runtime.*Поддерживаемые формы.*"
            r"Python.*\.py.*Go.*test.*run.*build"
        ),
    ):
        load_entrypoints(raw_config)


def test_load_entrypoints_rejects_dotenv_for_go_tests(
    config_file_factory: Callable[[object], Path],
) -> None:
    """Не разрешает Go-тестам загружать dotenv."""
    config_path = config_file_factory(
        {
            "entrypoints": [
                {
                    "alias": "go-tests",
                    "command": "go test ./...",
                    "load_env_file": True,
                }
            ]
        }
    )
    raw_config = load_raw_config(config_path)

    with pytest.raises(SystemExit, match="Тесты не должны загружать энвы"):
        load_entrypoints(raw_config)


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
    config_file_factory: Callable[[object], Path],
    operation: str,
    target: str,
    suggested_package: str,
) -> None:
    """Объясняет риск неполной компиляции и предлагает директорию package."""
    config_path = config_file_factory({"entrypoints": [{"alias": "go-target", "command": f"go {operation} {target}"}]})
    raw_config = load_raw_config(config_path)

    with pytest.raises(SystemExit) as exit_info:
        load_entrypoints(raw_config)

    message = str(exit_info.value)
    assert target in message
    assert "компилирует только перечисленные .go-файлы" in message
    assert f"укажите package {suggested_package!r}" in message


def test_load_entrypoints_allows_go_extension_for_build_output(
    config_file_factory: Callable[[object], Path],
) -> None:
    """Не принимает значение build-флага -o за исходный Go-файл."""
    config_path = config_file_factory(
        {
            "entrypoints": [
                {
                    "alias": "go-build",
                    "command": "go build -o dist/app.go ./cmd/app",
                }
            ]
        }
    )
    raw_config = load_raw_config(config_path)

    entrypoint = load_entrypoints(raw_config)[0]

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
    config_file_factory: Callable[[object], Path],
    command: str,
) -> None:
    """Проверяет только package/file-позиции Go-команды."""
    config_path = config_file_factory({"entrypoints": [{"alias": "go-command", "command": command}]})
    raw_config = load_raw_config(config_path)

    entrypoint = load_entrypoints(raw_config)[0]

    assert entrypoint.command_args() == tuple(command.split()[1:])


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[", "Некорректный JSON"),
        ("[]", "должен быть JSON-объект"),
        ("{}", "Конфигурация пуста"),
        (
            '{"entrypoints": [], "unknown": true}',
            "Найден неизвестный ключ 'unknown'",
        ),
    ],
)
def test_load_raw_config_rejects_invalid_root(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    """Отклоняет ошибки JSON и корневой схемы на файловом этапе."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        load_raw_config(config_path)


@pytest.mark.parametrize(
    ("entrypoint", "message"),
    [
        (
            {"alias": "api", "command": "python -m api", "unknown": True},
            "Найдены неизвестные поля",
        ),
        (
            {"alias": "api", "command": "python -m api", "env": {"PORT": 8000}},
            "Поле env точки входа",
        ),
        (
            {"alias": "api", "command": "python -m api", "debug_port": "5679"},
            "Поле debug_port точки входа",
        ),
    ],
)
def test_load_entrypoints_rejects_invalid_entrypoint(
    config_file_factory: Callable[[object], Path],
    entrypoint: dict[str, object],
    message: str,
) -> None:
    """Отклоняет поля точки входа после успешной загрузки корня."""
    config_path = config_file_factory({"entrypoints": [entrypoint]})
    raw_config = load_raw_config(config_path)

    with pytest.raises(SystemExit, match=message):
        load_entrypoints(raw_config)


def test_load_entrypoints_reads_debug_port(
    config_file_factory: Callable[[object], Path],
) -> None:
    """Загружает порт отладки точки входа."""
    config_path = config_file_factory(
        {
            "entrypoints": [
                {
                    "alias": "api",
                    "command": "python -m api",
                    "debug_port": DEBUG_PORT,
                }
            ]
        }
    )
    raw_config = load_raw_config(config_path)

    entrypoint = load_entrypoints(raw_config)[0]

    assert entrypoint.debug_port == DEBUG_PORT


@pytest.mark.parametrize(
    ("raw_config", "expected"),
    [
        ({"entrypoints": []}, GlobalConfig()),
        (
            {"entrypoints": [], "global_config": {"debug_port": DEBUG_PORT}},
            GlobalConfig(debug_port=DEBUG_PORT),
        ),
    ],
)
def test_load_global_config_reads_values_and_defaults(
    config_file_factory: Callable[[object], Path],
    raw_config: dict[str, object],
    expected: GlobalConfig,
) -> None:
    """Загружает глобальный порт отладки и применяет default без значения."""
    config_path = config_file_factory(raw_config)
    loaded_raw_config = load_raw_config(config_path)

    global_config = load_global_config(loaded_raw_config)

    assert global_config == expected


@pytest.mark.parametrize(
    ("raw_global_config", "message"),
    [
        (["invalid"], "global config должен быть JSON-объектом"),
        ({"debug_port": "5679"}, "Поле debug_port в глобальном конфиге должно быть int"),
        ({"unknown": True}, "Найдены неизвестные поля в глобальном конфиге: unknown"),
    ],
)
def test_load_global_config_rejects_invalid_config(
    config_file_factory: Callable[[object], Path],
    raw_global_config: object,
    message: str,
) -> None:
    """Проверяет тип и неизвестные поля глобальной конфигурации."""
    config_path = config_file_factory({"entrypoints": [], "global_config": raw_global_config})
    raw_config = load_raw_config(config_path)

    with pytest.raises(SystemExit, match=message):
        load_global_config(raw_config)


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
