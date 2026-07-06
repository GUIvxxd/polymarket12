from __future__ import annotations

from datetime import UTC, datetime

from polybot import ledger
from polybot.main import build_parser, main
from polybot.resolver import PaperTradeResolver, extract_market_resolution


FIXED_NOW = datetime(2026, 7, 6, 12, 5, tzinfo=UTC)


class FakeGammaClient:
    def __init__(self, markets):
        self.markets = markets

    def fetch_market_by_slug(self, slug: str):
        for market in self.markets:
            if market.get("slug") == slug:
                return market
        return None

    def public_search(self, query: str, *, limit: int | None = None):
        matching = [
            market
            for market in self.markets
            if market.get("slug") == query or market.get("conditionId") == query
        ]
        return {"events": [{"markets": matching}]}

    def fetch_markets(self, *, limit: int = 100, offset: int = 0, extra_params=None):
        extra_params = extra_params or {}
        return [
            market
            for market in self.markets
            if all(str(market.get(key)) == str(value) for key, value in extra_params.items())
        ]


def make_trade(**overrides) -> ledger.PaperTrade:
    values = {
        "trade_id": "trade-1",
        "created_at_utc": "2026-07-06T12:00:00Z",
        "market_slug": "btc-updown-5m-1",
        "condition_id": "0xcondition",
        "token_id": "up-token",
        "side": "BUY_UP",
        "outcome": "Up",
        "paper_price": 0.80,
        "paper_size": 12.5,
        "paper_cost": 10.0,
        "simulated_latency_ms": 1500,
        "fair_probability": 0.9,
        "edge_before_slippage": 0.10,
        "edge_after_slippage": 0.08,
        "status": ledger.OPEN,
        "resolved_at_utc": None,
        "payout": 0.0,
        "pnl": 0.0,
        "reason": "test signal",
    }
    values.update(overrides)
    return ledger.PaperTrade(**values)


def make_market(**overrides):
    values = {
        "slug": "btc-updown-5m-1",
        "conditionId": "0xcondition",
        "closed": True,
        "outcomes": '["Up", "Down"]',
        "outcomePrices": '["1", "0"]',
        "umaResolutionStatus": "resolved",
    }
    values.update(overrides)
    return values


def test_extract_market_resolution_infers_winner_from_final_prices() -> None:
    resolution = extract_market_resolution(make_market(outcomePrices='["0", "1"]'))

    assert resolution.closed is True
    assert resolution.winning_outcome == "Down"
    assert "final outcome prices" in resolution.reason


def test_extract_market_resolution_leaves_unknown_winner_unresolved() -> None:
    resolution = extract_market_resolution(make_market(outcomePrices='["0.5", "0.5"]'))

    assert resolution.closed is True
    assert resolution.winning_outcome is None
    assert "unavailable" in resolution.reason


def test_extract_market_resolution_requires_closed_market() -> None:
    resolution = extract_market_resolution(make_market(closed=False, outcomePrices='["1", "0"]'))

    assert resolution.closed is False
    assert resolution.winning_outcome is None


def test_winning_trade_resolves_with_positive_pnl(tmp_path) -> None:
    store = ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite")
    store.record_trade(make_trade(outcome="Up", paper_size=12.5, paper_cost=10.0))
    resolver = PaperTradeResolver(
        store,
        FakeGammaClient([make_market(outcomePrices='["1", "0"]')]),
        clock=lambda: FIXED_NOW,
    )

    summary = resolver.resolve_open_trades()
    trade = store.get_trade("trade-1")

    assert summary.resolved == 1
    assert summary.won == 1
    assert trade is not None
    assert trade.status == ledger.WON
    assert trade.resolved_at_utc == "2026-07-06T12:05:00Z"
    assert trade.payout == 12.5
    assert trade.pnl == 2.5


def test_losing_trade_resolves_with_negative_pnl(tmp_path) -> None:
    store = ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite")
    store.record_trade(make_trade(outcome="Down", paper_size=12.5, paper_cost=10.0))
    resolver = PaperTradeResolver(
        store,
        FakeGammaClient([make_market(outcomePrices='["1", "0"]')]),
        clock=lambda: FIXED_NOW,
    )

    summary = resolver.resolve_open_trades()
    trade = store.get_trade("trade-1")

    assert summary.resolved == 1
    assert summary.lost == 1
    assert trade is not None
    assert trade.status == ledger.LOST
    assert trade.payout == 0.0
    assert trade.pnl == -10.0


def test_unknown_winner_remains_open(tmp_path) -> None:
    store = ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite")
    store.record_trade(make_trade())
    resolver = PaperTradeResolver(
        store,
        FakeGammaClient([make_market(outcomePrices='["0.5", "0.5"]')]),
        clock=lambda: FIXED_NOW,
    )

    summary = resolver.resolve_open_trades()
    trade = store.get_trade("trade-1")

    assert summary.resolved == 0
    assert summary.unresolved == 1
    assert trade is not None
    assert trade.status == ledger.OPEN
    assert trade.resolved_at_utc is None


def test_resolve_cli_reports_empty_ledger(tmp_path, capsys) -> None:
    db_path = tmp_path / "paper_trades.sqlite"

    exit_code = main(["resolve", "--db", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "checked" in output
    assert "resolved" in output
    assert db_path.exists()


def test_cli_parser_includes_resolve_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["resolve"])

    assert args.command == "resolve"
