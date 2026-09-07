"""Проверяет внешний контракт упаковки runpoint."""

import tomllib
from pathlib import Path


def test_project_metadata_exposes_only_runpoint_product() -> None:
    """Публикует новое distribution/package/console-script имя без алиасов."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "runpoint"
    assert pyproject["project"]["version"] == "0.1.0"
    assert pyproject["project"]["scripts"] == {
        "runpoint": "runpoint.cli:main",
    }
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/runpoint"
    ]
