"""Configuration defaults for the paper-trading bot."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class BotConfig:
    """Runtime defaults shared by early CLI commands."""

    db_path: Path = Path("./data/paper_trades.sqlite")
    default_stake: Decimal = Decimal("10")
    min_edge: Decimal = Decimal("0.08")
    mode: str = "paper"

