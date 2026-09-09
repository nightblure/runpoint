"""Загружает конфигурацию и dotenv-данные runpoint."""

from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING, TypeVar, cast

from dotenv import dotenv_values as parse_dotenv

from runpoint.domain import Entrypoint, entrypoint_factory

if TYPE_CHECKING:
    from pathlib import Path

_T = TypeVar("_T")


def find_config_path(*, cwd: Path, cfg_filename: str) -> Path | None:
    """Ищет файл конфигурации в текущей и родительских директориях."""
    directory = cwd.resolve(strict=True)

    while True:
        config_path = directory / cfg_filename
        if config_path.is_file():
            return config_path

        if directory.parent == directory:
            break

        directory = directory.parent

    return None


def load_jsonc(filepath: Path) -> object:
    """Загружает JSON-файл с необязательными комментариями."""
    with filepath.open(encoding="utf-8") as file:
        content = file.read()

    # Регулярное выражение для удаления // и /* */ комментариев
    pattern = r"((?:\".*?(?<!\\)\"|'.*?(?<!\\)')|/\*.*?\*/|//[^\r\n]*)"
    regex = re.compile(pattern, re.MULTILINE | re.DOTALL)

    def _replacer(match: re.Match[str]) -> str:
        match_str = cast("str", match.group(1))

        if match_str.startswith(("/", "/*")):
            return ""  # Удаляем комментарий

        return match_str  # Оставляем обычные строки

    clean_json = regex.sub(_replacer, content)
    return cast("object", json.loads(clean_json))


def _require_type(
    value: object,
    expected_type: type[_T],
    *,
    message: str,
) -> _T:
    if not isinstance(value, expected_type):
        raise SystemExit(message)

    return value


def _require_string_mapping(value: object, *, message: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise SystemExit(message)

    return cast("dict[str, str]", value)


def _parse_entrypoint(
    raw_entrypoint: object,
    *,
    known_fields: frozenset[str],
) -> Entrypoint:
    if not isinstance(raw_entrypoint, dict):
        message = "Каждая точка входа должна быть JSON-объектом"
        raise SystemExit(message)

    config = cast("dict[str, object]", raw_entrypoint)

    try:
        alias = config["alias"]
    except KeyError as error:
        message = "Поле 'alias' не определено"
        raise SystemExit(message) from error

    alias = _require_type(
        alias,
        str,
        message="Поле alias точки входа должно быть строкой",
    )

    unknown_fields = set(config).difference(known_fields)
    if unknown_fields:
        fields = ", ".join(sorted(unknown_fields))
        message = f"Найдены неизвестные поля {alias!r}: {fields}"
        raise SystemExit(message)

    try:
        command = config["command"]
    except KeyError as error:
        message = "Поле 'command' не определено"
        raise SystemExit(message) from error

    cwd = config.get("cwd", ".")
    venv = config.get("venv", ".venv")
    env_file = config.get("env_file", ".env")
    env = config.get("env", {})
    load_env_file = config.get("load_env_file", False)

    command = _require_type(
        command,
        str,
        message=f"Поле command точки входа {alias!r} должно быть строкой",
    )
    cwd = _require_type(
        cwd,
        str,
        message=f"Поле cwd точки входа {alias!r} должно быть строкой",
    )
    venv = _require_type(
        venv,
        str,
        message=f"Поле venv точки входа {alias!r} должно быть строкой",
    )
    env_file = _require_type(
        env_file,
        str,
        message=f"Поле env_file точки входа {alias!r} должно быть строкой",
    )
    load_env_file = _require_type(
        load_env_file,
        bool,
        message=f"Поле load_env_file точки входа {alias!r} должно быть bool",
    )
    env = _require_string_mapping(
        env,
        message=f"Поле env точки входа {alias!r} должно содержать только строки",
    )

    try:
        return entrypoint_factory(
            cwd=cwd,
            venv=venv,
            env_file=env_file,
            env=env,
            alias=alias,
            command=command,
            load_env_file=load_env_file,
        )
    except (RuntimeError, ValueError) as error:
        message = f"Некорректная точка входа {alias!r}: {error}"
        raise SystemExit(message) from error


def load_entrypoints(config_path: Path) -> tuple[Entrypoint, ...]:
    """Загружает и проверяет точки входа из конфигурации."""
    try:
        raw_config = load_jsonc(config_path)
    except json.JSONDecodeError as error:
        message = f"Некорректный JSON в {config_path}: {error}"
        raise SystemExit(message) from error
    except Exception as error:
        message = f"Ошибка парсинга конфига {config_path}: {error}"
        raise SystemExit(message) from error

    if not isinstance(raw_config, list):
        message = f"Корнем {config_path} должен быть список JSON-объектов"
        raise SystemExit(message)

    known_fields = frozenset[str](field.name for field in dataclasses.fields(Entrypoint) if field.init)

    if not raw_config:
        message = "Конфигурация пуста"
        raise SystemExit(message)

    items = cast("list[object]", raw_config)
    entrypoints = tuple(_parse_entrypoint(item, known_fields=known_fields) for item in items)
    aliases = [entrypoint.alias for entrypoint in entrypoints]

    if len(aliases) != len(set(aliases)):
        message = "Алиасы точек входа должны быть уникальными"
        raise SystemExit(message)

    return entrypoints


def dotenv_values(dotenv_path: Path) -> dict[str, str]:
    """Загружает строковые значения из dotenv-файла."""
    if not dotenv_path.is_file():
        message = f".env не является файлом: {dotenv_path}"
        raise SystemExit(message)

    parsed = parse_dotenv(dotenv_path)
    return {key: value for key, value in parsed.items() if value is not None}
