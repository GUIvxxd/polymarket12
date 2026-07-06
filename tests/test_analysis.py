from __future__ import annotations

import csv

from polybot import analysis, ledger
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
        "paper_price": 0.75,
        "paper_size": 13.3333333333,
        "paper_cost": 10.0,
        "simulated_latency_ms": 1500,
        "fair_probability": 0.9,
        "edge_before_slippage": 0.15,
        "edge_after_slippage": 0.13,
        "status": ledger.WON,
        "resolved_at_utc": "2026-07-06T12:05:00Z",
        "payout": 13.0,
        "pnl": 3.0,
        "reason": "test signal",
    }
    values.update(overrides)
    return ledger.PaperTrade(**values)


def test_win_rate_calculation() -> None:
    assert analysis.calculate_win_rate(3, 1) == 0.75
    assert analysis.calculate_win_rate(0, 0) == 0.0


def test_drawdown_calculation_uses_chronological_resolved_pnl() -> None:
    trades = [
        make_trade(trade_id="1", pnl=10.0, resolved_at_utc="2026-07-06T12:01:00Z"),
        make_trade(trade_id="2", pnl=-5.0, resolved_at_utc="2026-07-06T12:02:00Z"),
        make_trade(trade_id="3", pnl=-20.0, resolved_at_utc="2026-07-06T12:03:00Z"),
        make_trade(trade_id="4", pnl=10.0, resolved_at_utc="2026-07-06T12:04:00Z"),
    ]

    assert analysis.calculate_max_drawdown(trades, starting_balance=100.0) == 25.0


def test_analysis_metrics_and_bucket_aggregation() -> None:
    trades = [
        make_trade(trade_id="btc-win", market_slug="btc-updown-5m-1", pnl=3.0),
        make_trade(
            trade_id="eth-loss",
            market_slug="ethereum-up-or-down-july-3-2026",
            status=ledger.LOST,
            pnl=-10.0,
            edge_after_slippage=0.08,
        ),
        make_trade(
            trade_id="btc-open",
            market_slug="btc-updown-5m-2",
            status=ledger.OPEN,
            resolved_at_utc=None,
            pnl=0.0,
            paper_cost=10.0,
        ),
    ]

    report = analysis.analyze_trades(trades, starting_balance=100.0)

    assert report.metrics.starting_balance == 100.0
    assert report.metrics.ending_balance == 93.0
    assert report.metrics.realized_pnl == -7.0
    assert report.metrics.open_risk == 10.0
    assert report.metrics.total_trades == 3
    assert report.metrics.resolved_trades == 2
    assert report.metrics.unresolved_trades == 1
    assert report.metrics.win_rate == 0.5
    assert report.metrics.average_profit_per_trade == -3.5
    assert report.metrics.median_profit_per_trade == -3.5

    symbol_pnl = {item.bucket: item.realized_pnl for item in report.profit_by_symbol}
    edge_counts = {item.bucket: item.trade_count for item in report.profit_by_edge_bucket}
    expiry_buckets = {item.bucket for item in report.profit_by_time_to_expiry_bucket}

    assert symbol_pnl == {"BTC": 3.0, "ETH": -10.0}
    assert edge_counts == {"10-20%": 1, "5-10%": 1}
    assert expiry_buckets == {"unknown"}


def test_export_report_csv_writes_summary_and_bucket_files(tmp_path) -> None:
    report = analysis.analyze_trades(
        [make_trade(trade_id="btc-win", market_slug="btc-updown-5m-1", pnl=3.0)],
        starting_balance=100.0,
    )

    paths = analysis.export_report_csv(report, tmp_path)

    assert {path.name for path in paths} == {
        "summary.csv",
        "profit_by_symbol.csv",
        "profit_by_time_to_expiry_bucket.csv",
        "profit_by_edge_bucket.csv",
    }

    with (tmp_path / "summary.csv").open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert ["metric", "value"] in rows
    assert ["realized_pnl", "3.0"] in rows


def test_analyze_cli_prints_and_exports_report(tmp_path, capsys) -> None:
    db_path = tmp_path / "paper_trades.sqlite"
    report_dir = tmp_path / "reports"
    store = ledger.SQLiteLedger(db_path)
    store.record_trade(make_trade(trade_id="btc-win", pnl=3.0))

    exit_code = main(
        [
            "analyze",
            "--db",
            str(db_path),
            "--starting-balance",
            "100",
            "--report-dir",
            str(report_dir),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Analysis Summary" in output
    assert "Profit By Symbol" in output
    assert (report_dir / "summary.csv").exists()


def test_cli_parser_includes_analyze_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["analyze", "--starting-balance", "100"])

    assert args.command == "analyze"
    assert args.starting_balance == 100.0

