"""Rich terminal dashboard for the live paper loop."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from polybot.live import LoopIterationResult
from polybot.model import Signal
from polybot.ledger import PaperTrade


class PaperDashboard:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render(self, result: LoopIterationResult) -> None:
        self.console.clear()
        self.console.print(
            Panel(
                Group(
                    _summary_table(result),
                    _signals_table(result.signals),
                    _trades_table(result.trades),
                    _messages_table(result.messages),
                ),
                title="Polymarket Paper Bot",
                subtitle="public data only",
            )
        )


def _summary_table(result: LoopIterationResult) -> Table:
    summary = result.ledger_summary
    table = Table(title="Paper Ledger", expand=True)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Markets checked", str(result.markets_checked))
    table.add_row("Books checked", str(result.books_checked))
    table.add_row("Signals", str(len(result.signals)))
    table.add_row("Trades created", str(len(result.trades)))
    table.add_row("Resolved this tick", str(result.resolver_summary.resolved))
    table.add_row("Total trades", str(summary.total_trades))
    table.add_row("Open trades", str(summary.open_trades))
    table.add_row("Realized PnL", _money(summary.realized_pnl))
    table.add_row("Open risk", _money(summary.open_risk))
    table.add_row("Win rate", f"{summary.win_rate:.2%}")
    return table


def _signals_table(signals: Sequence[Signal]) -> Table:
    table = Table(title="Latest Signals", expand=True)
    table.add_column("Side")
    table.add_column("Market")
    table.add_column("Outcome")
    table.add_column("Fair", justify="right")
    table.add_column("Ask", justify="right")
    table.add_column("Edge", justify="right")
    for signal in signals[-8:]:
        table.add_row(
            signal.side,
            signal.market_slug,
            signal.outcome,
            _price(signal.fair_probability),
            _price(signal.ask_price),
            _price(signal.edge),
        )
    if not signals:
        table.add_row("", "none", "", "", "", "")
    return table


def _trades_table(trades: Sequence[PaperTrade]) -> Table:
    table = Table(title="Latest Paper Trades", expand=True)
    table.add_column("Trade")
    table.add_column("Market")
    table.add_column("Outcome")
    table.add_column("Price", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Cost", justify="right")
    for trade in trades[-8:]:
        table.add_row(
            trade.trade_id[:8],
            trade.market_slug,
            trade.outcome,
            _price(trade.paper_price),
            f"{trade.paper_size:.4f}",
            _money(trade.paper_cost),
        )
    if not trades:
        table.add_row("", "none", "", "", "", "")
    return table


def _messages_table(messages: Sequence[str]) -> Table:
    table = Table(title="Messages", expand=True)
    table.add_column("Message")
    for message in messages[-8:]:
        table.add_row(message)
    if not messages:
        table.add_row("no warnings")
    return table


def _money(value: float) -> str:
    return f"${value:.2f}"


def _price(value: float) -> str:
    return f"{value:.4f}"

