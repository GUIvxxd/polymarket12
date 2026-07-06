"""Analyze paper-trading ledger results."""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from polybot import ledger


DEFAULT_REPORT_DIR = Path("./data/reports")
RESOLVED_STATUSES = {ledger.WON, ledger.LOST}


@dataclass(frozen=True)
class BucketMetric:
    bucket: str
    trade_count: int
    realized_pnl: float
    average_pnl: float


@dataclass(frozen=True)
class AnalysisMetrics:
    starting_balance: float
    ending_balance: float
    realized_pnl: float
    open_pnl: float
    open_risk: float
    total_trades: int
    resolved_trades: int
    unresolved_trades: int
    won_trades: int
    lost_trades: int
    win_rate: float
    average_profit_per_trade: float
    median_profit_per_trade: float
    max_drawdown: float
    skipped_trades_count: int | None


@dataclass(frozen=True)
class AnalysisReport:
    metrics: AnalysisMetrics
    profit_by_symbol: list[BucketMetric]
    profit_by_time_to_expiry_bucket: list[BucketMetric]
    profit_by_edge_bucket: list[BucketMetric]


def analyze_ledger(
    store: ledger.SQLiteLedger,
    *,
    starting_balance: float = 0.0,
) -> AnalysisReport:
    return analyze_trades(
        store.list_trades(),
        starting_balance=starting_balance,
        skipped_trades_count=store.count_signal_skips(),
    )


def analyze_trades(
    trades: Sequence[ledger.PaperTrade],
    *,
    starting_balance: float = 0.0,
    skipped_trades_count: int | None = None,
) -> AnalysisReport:
    resolved = [trade for trade in trades if trade.status in RESOLVED_STATUSES]
    open_trades = [trade for trade in trades if trade.status == ledger.OPEN]
    pnl_values = [trade.pnl for trade in resolved]
    won_trades = [trade for trade in resolved if trade.status == ledger.WON]
    lost_trades = [trade for trade in resolved if trade.status == ledger.LOST]
    realized_pnl = sum(pnl_values)
    resolved_count = len(resolved)

    metrics = AnalysisMetrics(
        starting_balance=starting_balance,
        ending_balance=starting_balance + realized_pnl,
        realized_pnl=realized_pnl,
        open_pnl=0.0,
        open_risk=sum(trade.paper_cost for trade in open_trades),
        total_trades=len(trades),
        resolved_trades=resolved_count,
        unresolved_trades=len(open_trades),
        won_trades=len(won_trades),
        lost_trades=len(lost_trades),
        win_rate=calculate_win_rate(len(won_trades), len(lost_trades)),
        average_profit_per_trade=(realized_pnl / resolved_count if resolved_count else 0.0),
        median_profit_per_trade=(float(median(pnl_values)) if pnl_values else 0.0),
        max_drawdown=calculate_max_drawdown(resolved, starting_balance=starting_balance),
        skipped_trades_count=skipped_trades_count,
    )

    return AnalysisReport(
        metrics=metrics,
        profit_by_symbol=bucket_profit(resolved, bucket_fn=lambda trade: infer_symbol(trade.market_slug)),
        profit_by_time_to_expiry_bucket=bucket_profit(
            resolved,
            bucket_fn=lambda trade: time_to_expiry_bucket(trade),
        ),
        profit_by_edge_bucket=bucket_profit(
            resolved,
            bucket_fn=lambda trade: edge_bucket(trade.edge_after_slippage),
        ),
    )


def calculate_win_rate(won_trades: int, lost_trades: int) -> float:
    resolved = won_trades + lost_trades
    if resolved == 0:
        return 0.0
    return won_trades / resolved


def calculate_max_drawdown(
    trades: Sequence[ledger.PaperTrade],
    *,
    starting_balance: float = 0.0,
) -> float:
    balance = starting_balance
    peak = starting_balance
    max_drawdown = 0.0

    for trade in sorted(trades, key=_trade_sort_key):
        if trade.status not in RESOLVED_STATUSES:
            continue
        balance += trade.pnl
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)

    return max_drawdown


def bucket_profit(
    trades: Iterable[ledger.PaperTrade],
    *,
    bucket_fn,
) -> list[BucketMetric]:
    grouped: dict[str, list[ledger.PaperTrade]] = defaultdict(list)
    for trade in trades:
        grouped[bucket_fn(trade)].append(trade)

    metrics = [
        BucketMetric(
            bucket=bucket,
            trade_count=len(bucket_trades),
            realized_pnl=sum(trade.pnl for trade in bucket_trades),
            average_pnl=(
                sum(trade.pnl for trade in bucket_trades) / len(bucket_trades)
                if bucket_trades
                else 0.0
            ),
        )
        for bucket, bucket_trades in grouped.items()
    ]
    return sorted(metrics, key=lambda item: item.bucket)


def infer_symbol(market_slug: str) -> str:
    normalized = market_slug.lower()
    if normalized.startswith(("btc-", "bitcoin-")) or "bitcoin-up-or-down" in normalized:
        return "BTC"
    if normalized.startswith(("eth-", "ethereum-")) or "ethereum-up-or-down" in normalized:
        return "ETH"
    if normalized.startswith(("sol-", "solana-")) or "solana-up-or-down" in normalized:
        return "SOL"
    if normalized.startswith("xrp-") or "xrp-up-or-down" in normalized:
        return "XRP"
    return "UNKNOWN"


def edge_bucket(edge_after_slippage: float) -> str:
    if edge_after_slippage < 0:
        return "<0%"
    if edge_after_slippage < 0.05:
        return "0-5%"
    if edge_after_slippage < 0.10:
        return "5-10%"
    if edge_after_slippage < 0.20:
        return "10-20%"
    return "20%+"


def time_to_expiry_bucket(trade: ledger.PaperTrade) -> str:
    # The current ledger does not persist seconds remaining at entry.
    return "unknown"


def export_report_csv(
    report: AnalysisReport,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
) -> list[Path]:
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = [
        _write_summary_csv(report.metrics, output_dir / "summary.csv"),
        _write_bucket_csv(report.profit_by_symbol, output_dir / "profit_by_symbol.csv"),
        _write_bucket_csv(
            report.profit_by_time_to_expiry_bucket,
            output_dir / "profit_by_time_to_expiry_bucket.csv",
        ),
        _write_bucket_csv(report.profit_by_edge_bucket, output_dir / "profit_by_edge_bucket.csv"),
    ]
    return paths


def _write_summary_csv(metrics: AnalysisMetrics, path: Path) -> Path:
    rows = [
        ("starting_balance", metrics.starting_balance),
        ("ending_balance", metrics.ending_balance),
        ("realized_pnl", metrics.realized_pnl),
        ("open_pnl", metrics.open_pnl),
        ("open_risk", metrics.open_risk),
        ("total_trades", metrics.total_trades),
        ("resolved_trades", metrics.resolved_trades),
        ("unresolved_trades", metrics.unresolved_trades),
        ("won_trades", metrics.won_trades),
        ("lost_trades", metrics.lost_trades),
        ("win_rate", metrics.win_rate),
        ("average_profit_per_trade", metrics.average_profit_per_trade),
        ("median_profit_per_trade", metrics.median_profit_per_trade),
        ("max_drawdown", metrics.max_drawdown),
        (
            "skipped_trades_count",
            "not_tracked" if metrics.skipped_trades_count is None else metrics.skipped_trades_count,
        ),
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerows(rows)
    return path


def _write_bucket_csv(metrics: Sequence[BucketMetric], path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "trade_count", "realized_pnl", "average_pnl"])
        for item in metrics:
            writer.writerow([item.bucket, item.trade_count, item.realized_pnl, item.average_pnl])
    return path


def _trade_sort_key(trade: ledger.PaperTrade) -> tuple[str, str]:
    return (trade.resolved_at_utc or trade.created_at_utc, trade.trade_id)
