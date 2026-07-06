from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polybot import ledger
from polybot.model import Signal
from polybot.paper_trader import PaperTrader, PaperTraderConfig


FIXED_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


def make_signal(**overrides) -> Signal:
    values = {
        "side": "BUY_UP",
        "market_slug": "btc-updown-5m-1",
        "condition_id": "0xcondition",
        "token_id": "token-1",
        "outcome": "Up",
        "fair_probability": 0.90,
        "ask_price": 0.70,
        "ask_size": 100.0,
        "edge": 0.20,
        "suggested_stake": 10.0,
        "seconds_remaining": 60.0,
        "reason": "current price is above start price",
    }
    values.update(overrides)
    return Signal(**values)


def test_paper_trader_calculates_fill_cost_and_share_size(tmp_path) -> None:
    store = ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite")
    trader = PaperTrader(
        store,
        PaperTraderConfig(
            stake=10.0,
            slippage_cents=0.02,
            min_edge_after_slippage=0.05,
            simulated_latency_ms=1500,
        ),
        clock=lambda: FIXED_NOW,
        trade_id_factory=lambda: "paper-1",
    )

    result = trader.record_signal(make_signal())

    assert result.created is True
    assert result.trade is not None
    assert result.trade.trade_id == "paper-1"
    assert result.trade.created_at_utc == "2026-07-06T12:00:00Z"
    assert result.trade.paper_price == pytest.approx(0.72)
    assert result.trade.paper_size == pytest.approx(10.0 / 0.72)
    assert result.trade.paper_cost == pytest.approx(10.0)
    assert result.trade.status == ledger.OPEN
    assert result.trade.simulated_latency_ms == 1500

    reloaded = store.get_trade("paper-1")
    assert reloaded is not None
    assert reloaded.paper_price == pytest.approx(0.72)


def test_slippage_reduces_edge(tmp_path) -> None:
    trader = PaperTrader(
        ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite"),
        PaperTraderConfig(stake=10.0, slippage_cents=0.03, min_edge_after_slippage=0.05),
        clock=lambda: FIXED_NOW,
        trade_id_factory=lambda: "paper-1",
    )

    result = trader.record_signal(make_signal(ask_price=0.70, fair_probability=0.90))

    assert result.trade is not None
    assert result.trade.edge_before_slippage == pytest.approx(0.20)
    assert result.trade.edge_after_slippage == pytest.approx(0.17)


def test_trade_is_skipped_if_edge_disappears_after_slippage(tmp_path) -> None:
    store = ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite")
    trader = PaperTrader(
        store,
        PaperTraderConfig(stake=10.0, slippage_cents=0.04, min_edge_after_slippage=0.08),
        clock=lambda: FIXED_NOW,
        trade_id_factory=lambda: "paper-1",
    )

    result = trader.record_signal(make_signal(ask_price=0.80, fair_probability=0.90))

    assert result.created is False
    assert result.trade is None
    assert result.skipped_reason is not None
    assert "below" in result.skipped_reason
    assert store.summarize().total_trades == 0


def test_fill_price_is_capped(tmp_path) -> None:
    trader = PaperTrader(
        ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite"),
        PaperTraderConfig(
            stake=10.0,
            slippage_cents=0.10,
            min_edge_after_slippage=-0.50,
            max_fill_price=0.99,
        ),
        clock=lambda: FIXED_NOW,
        trade_id_factory=lambda: "paper-1",
    )

    result = trader.record_signal(make_signal(ask_price=0.95, fair_probability=0.99))

    assert result.trade is not None
    assert result.trade.paper_price == 0.99

