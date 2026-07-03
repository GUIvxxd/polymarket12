from __future__ import annotations

import ast
from pathlib import Path


SRC_ROOT = Path("src/polybot")

FORBIDDEN_NAME_FRAGMENTS = (
    "private_key",
    "seed_phrase",
    "api_secret",
    "wallet_credential",
    "sign_order",
    "submit_order",
    "place_order",
    "execute_order",
    "live_trade",
    "real_trade",
)

FORBIDDEN_SOURCE_SNIPPETS = (
    "POST /order",
    "post('/order'",
    'post("/order"',
    "/auth/api-key",
)


def iter_python_sources() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_no_source_names_suggest_live_order_placement() -> None:
    offenders: list[str] = []

    for path in iter_python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_name = path.relative_to(SRC_ROOT).with_suffix("").as_posix().lower()
        names = [module_name]

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name.lower())

        for name in names:
            for fragment in FORBIDDEN_NAME_FRAGMENTS:
                if fragment in name:
                    offenders.append(f"{path}: {name} contains {fragment}")

    assert offenders == []


def test_no_authenticated_trading_endpoint_literals() -> None:
    offenders: list[str] = []

    for path in iter_python_sources():
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SOURCE_SNIPPETS:
            if snippet in text:
                offenders.append(f"{path}: contains {snippet}")

    assert offenders == []

