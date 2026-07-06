"""Simulated fills for paper-trading signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from polybot.ledger import OPEN, PaperTrade, SQLiteLedger
from polybot.model import Signal
from polybot.price_feed import timestamp_utc


@dataclass(frozen=True)
class PaperTraderConfig:
    stake: float = 10.0
    slippage_cents: float = 0.02
    min_edge_after_slippage: float = 0.05
    simulated_latency_ms: int = 1500
    max_fill_price: float = 0.99


@dataclass(frozen=True)
class PaperTradeResult:
    created: bool
    trade: PaperTrade | None
    skipped_reason: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _trade_id() -> str:
    return uuid4().hex


class PaperTrader:
    def __init__(
        self,
        ledger: SQLiteLedger,
        config: PaperTraderConfig | None = None,
        clock: Callable[[], datetime] = _utc_now,
        trade_id_factory: Callable[[], str] = _trade_id,
    ) -> None:
        self.ledger = ledger
        self.config = config or PaperTraderConfig()
        self.clock = clock
        self.trade_id_factory = trade_id_factory

    def record_signal(self, signal: Signal) -> PaperTradeResult:
        fill_price = min(signal.ask_price + self.config.slippage_cents, self.config.max_fill_price)
        if fill_price <= 0:
            return PaperTradeResult(False, None, "fill price must be positive")

        edge_before_slippage = signal.fair_probability - signal.ask_price
        edge_after_slippage = signal.fair_probability - fill_price
        if edge_after_slippage < self.config.min_edge_after_slippage:
            return PaperTradeResult(
                created=False,
                trade=None,
                skipped_reason=(
                    f"edge after slippage {edge_after_slippage:.4f} "
                    f"is below {self.config.min_edge_after_slippage:.4f}"
                ),
            )

        if self.config.stake <= 0:
            return PaperTradeResult(False, None, "stake must be positive")

        paper_size = self.config.stake / fill_price
        paper_cost = paper_size * fill_price
        trade = PaperTrade(
            trade_id=self.trade_id_factory(),
            created_at_utc=timestamp_utc(self.clock()),
            market_slug=signal.market_slug,
            condition_id=signal.condition_id,
            token_id=signal.token_id,
            side=signal.side,
            outcome=signal.outcome,
            paper_price=fill_price,
            paper_size=paper_size,
            paper_cost=paper_cost,
            simulated_latency_ms=self.config.simulated_latency_ms,
            fair_probability=signal.fair_probability,
            edge_before_slippage=edge_before_slippage,
            edge_after_slippage=edge_after_slippage,
            status=OPEN,
            resolved_at_utc=None,
            payout=0.0,
            pnl=0.0,
            reason=signal.reason,
        )
        self.ledger.record_trade(trade)
        return PaperTradeResult(created=True, trade=trade)
