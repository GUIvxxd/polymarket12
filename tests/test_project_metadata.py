from __future__ import annotations

import tomllib
from pathlib import Path


def test_required_dependencies_are_declared() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    dependencies = "\n".join(pyproject["project"]["dependencies"])
    dev_dependencies = "\n".join(pyproject["dependency-groups"]["dev"])

    for package in ("httpx", "pydantic", "pandas", "rich"):
        assert package in dependencies

    for package in ("pytest", "ruff"):
        assert package in dev_dependencies


def test_cli_entrypoint_is_declared() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["polybot"] == "polybot.main:main"

