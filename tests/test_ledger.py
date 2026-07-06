from __future__ import annotations

from polybot import ledger
from polybot.main import build_parser, main


def make_trade(**overrides) -> ledger.PaperTrade:
    values = {
        "trade_id": "trade-1",
        "created_at_utc": "2026-07-06T12:00:00Z",
        "market_slug": "btc-updown-5m-1",
        "condition_id": "0xcondition",
        "token_id": "token-1",
        "side": "BUY_UP",
        "outcome": "Up",
        "paper_price": 0.72,
        "paper_size": 13.8888888889,
        "paper_cost": 10.0,
        "simulated_latency_ms": 1500,
        "fair_probability": 0.9,
        "edge_before_slippage": 0.2,
        "edge_after_slippage": 0.18,
        "status": ledger.OPEN,
        "resolved_at_utc": None,
        "payout": 0.0,
        "pnl": 0.0,
        "reason": "test signal",
    }
    values.update(overrides)
    return ledger.PaperTrade(**values)


def make_skip(**overrides) -> ledger.SkippedSignalRecord:
    values = {
        "skip_id": "skip-1",
        "created_at_utc": "2026-07-06T12:00:00Z",
        "market_slug": "btc-updown-5m-1",
        "condition_id": "0xcondition",
        "asset": "BTC",
        "side": "BUY_UP",
        "outcome": "Up",
        "token_id": "token-1",
        "reason": "Up edge 0.0500 below 0.0800",
        "fair_probability": 0.9,
        "ask_price": 0.85,
        "ask_size": 50.0,
        "edge": 0.05,
        "seconds_remaining": 60.0,
    }
    values.update(overrides)
    return ledger.SkippedSignalRecord(**values)


def test_ledger_persists_and_reloads_trades(tmp_path) -> None:
    db_path = tmp_path / "paper_trades.sqlite"
    store = ledger.SQLiteLedger(db_path)

    store.record_trade(make_trade())
    reloaded = ledger.SQLiteLedger(db_path).get_trade("trade-1")

    assert reloaded is not None
    assert reloaded.trade_id == "trade-1"
    assert reloaded.market_slug == "btc-updown-5m-1"
    assert reloaded.paper_price == 0.72
    assert reloaded.status == ledger.OPEN


def test_ledger_open_trades_and_summary(tmp_path) -> None:
    store = ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite")

    store.record_trade(make_trade(trade_id="open", status=ledger.OPEN, paper_cost=10.0))
    store.record_trade(
        make_trade(
            trade_id="won",
            status=ledger.WON,
            paper_cost=10.0,
            payout=13.0,
            pnl=3.0,
            resolved_at_utc="2026-07-06T12:05:00Z",
        )
    )
    store.record_trade(
        make_trade(
            trade_id="lost",
            status=ledger.LOST,
            paper_cost=10.0,
            payout=0.0,
            pnl=-10.0,
            resolved_at_utc="2026-07-06T12:05:00Z",
        )
    )

    summary = store.summarize()

    assert [trade.trade_id for trade in store.open_trades()] == ["open"]
    assert summary.total_trades == 3
    assert summary.open_trades == 1
    assert summary.won_trades == 1
    assert summary.lost_trades == 1
    assert summary.realized_pnl == -7.0
    assert summary.open_risk == 10.0
    assert summary.total_cost == 30.0
    assert summary.win_rate == 0.5


def test_ledger_updates_trade_resolution(tmp_path) -> None:
    store = ledger.SQLiteLedger(tmp_path / "paper_trades.sqlite")
    store.record_trade(make_trade(paper_size=12.5, paper_cost=10.0))

    store.update_resolution(
        trade_id="trade-1",
        status=ledger.WON,
        resolved_at_utc="2026-07-06T12:05:00Z",
        payout=12.5,
        pnl=2.5,
        reason="resolved winner=Up",
    )

    trade = store.get_trade("trade-1")

    assert trade is not None
    assert trade.status == ledger.WON
    assert trade.resolved_at_utc == "2026-07-06T12:05:00Z"
    assert trade.payout == 12.5
    assert trade.pnl == 2.5
    assert trade.reason == "resolved winner=Up"


def test_ledger_persists_and_counts_skipped_signals(tmp_path) -> None:
    db_path = tmp_path / "paper_trades.sqlite"
    store = ledger.SQLiteLedger(db_path)

    store.record_signal_skip(make_skip())
    store.record_signal_skip(
        make_skip(
            skip_id="skip-2",
            created_at_utc="2026-07-06T12:01:00Z",
            reason="Down edge below threshold",
        )
    )

    reloaded = ledger.SQLiteLedger(db_path).list_signal_skips()

    assert ledger.SQLiteLedger(db_path).count_signal_skips() == 2
    assert [skip.skip_id for skip in reloaded] == ["skip-2", "skip-1"]
    assert reloaded[0].market_slug == "btc-updown-5m-1"
    assert reloaded[0].edge == 0.05


def test_empty_ledger_summary_creates_database(tmp_path) -> None:
    db_path = tmp_path / "nested" / "paper_trades.sqlite"

    summary = ledger.SQLiteLedger(db_path).summarize()

    assert db_path.exists()
    assert summary.total_trades == 0
    assert summary.open_risk == 0.0


def test_ledger_summary_cli_reports_empty_ledger(tmp_path, capsys) -> None:
    db_path = tmp_path / "paper_trades.sqlite"

    exit_code = main(["ledger", "summary", "--db", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "total trades" in output
    assert "open risk" in output
    assert db_path.exists()


def test_cli_parser_includes_ledger_summary_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["ledger", "summary"])

    assert args.command == "ledger"
    assert args.ledger_command == "summary"
