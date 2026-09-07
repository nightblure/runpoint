"""Проверяет загрузку и поиск конфигурации."""

from pathlib import Path

import pytest

from runpoint.data import find_config_path, load_entrypoints


def test_load_entrypoints_rejects_duplicate_aliases(tmp_path: Path) -> None:
    """Запрещает неоднозначную конфигурацию с повторяющимися алиасами."""
    config_path = tmp_path / ".runpoint.json"
    config_path.write_text(
        '[{"alias": "api", "command": "-m first"},'
        '{"alias": "api", "command": "-m second"}]',
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
                "command": "-m service --port 8000",
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

    with pytest.raises(SystemExit, match=message):
        load_entrypoints(config_path)


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
