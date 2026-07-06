"""Command-line entrypoint for the paper-trading bot."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from polybot import __version__
from polybot import clob, discovery, ledger, price_feed
from polybot.config import BotConfig
from polybot.gamma import GammaClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polybot",
        description="Polymarket paper-trading research bot.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Show current project mode and ledger defaults.")

    discover_parser = subparsers.add_parser(
        "discover",
        help="Discover public crypto up/down markets from Gamma.",
    )
    discover_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of markets to print.",
    )
    discover_parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include closed markets when active markets are unavailable.",
    )

    books_parser = subparsers.add_parser(
        "books",
        help="Fetch public CLOB books for discovered crypto up/down markets.",
    )
    books_parser.add_argument(
        "--asset",
        choices=("btc", "eth", "sol", "xrp"),
        default="btc",
        help="Crypto asset to inspect.",
    )
    books_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of markets to inspect.",
    )
    books_parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include closed markets when active markets are unavailable.",
    )

    price_parser = subparsers.add_parser(
        "price",
        help="Fetch public crypto spot prices.",
    )
    price_parser.add_argument(
        "symbols",
        nargs="+",
        choices=tuple(symbol.lower() for symbol in price_feed.SUPPORTED_SYMBOLS)
        + price_feed.SUPPORTED_SYMBOLS,
        help="Crypto symbols to fetch, for example BTC ETH.",
    )

    ledger_parser = subparsers.add_parser(
        "ledger",
        help="Inspect the local paper-trading ledger.",
    )
    ledger_subparsers = ledger_parser.add_subparsers(dest="ledger_command")
    summary_parser = ledger_subparsers.add_parser(
        "summary",
        help="Show paper-trading ledger totals.",
    )
    summary_parser.add_argument(
        "--db",
        default=str(BotConfig().db_path),
        help="SQLite ledger path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        config = BotConfig()
        print(f"mode: {config.mode}")
        print(f"ledger: {config.db_path}")
        print(f"default stake: ${config.default_stake}")
        print(f"minimum edge: {config.min_edge}")
        return 0

    if args.command == "discover":
        client = GammaClient()
        markets = discovery.discover_crypto_up_down_markets(
            client,
            limit=args.limit,
            include_closed=args.include_closed,
        )
        _print_discovery_table(markets)
        if not markets and not args.include_closed:
            print("No active crypto up/down markets found. Retry with --include-closed.")
        return 0

    if args.command == "books":
        asset = args.asset.upper()
        gamma_client = GammaClient()
        markets = discovery.discover_crypto_up_down_markets(
            gamma_client,
            limit=args.limit,
            include_closed=args.include_closed,
            queries=_queries_for_asset(asset),
        )
        if not markets:
            _print_books_table([])
            closed_hint = " Retry with --include-closed." if not args.include_closed else ""
            print(f"No {'active ' if not args.include_closed else ''}{asset} markets found.{closed_hint}")
            return 0

        books = clob.enrich_markets_with_books(markets, clob.CLOBClient())
        _print_books_table(books)
        return 0

    if args.command == "price":
        feed = price_feed.build_default_price_feed()
        points: list[price_feed.PricePoint] = []
        for symbol in args.symbols:
            points.append(feed.get_price(symbol))
        _print_price_table(points)
        return 0

    if args.command == "ledger":
        if args.ledger_command == "summary":
            summary = ledger.SQLiteLedger(Path(args.db)).summarize()
            _print_ledger_summary(summary)
            return 0
        parser.print_help()
        return 0

    parser.print_help()
    return 0


def _print_discovery_table(markets: Sequence[discovery.CryptoUpDownMarket]) -> None:
    headers = (
        "asset",
        "slug",
        "end time",
        "state",
        "outcomes",
        "prices",
        "condition",
        "clob ids",
    )
    rows = [
        (
            market.asset,
            market.slug,
            market.end_time or "",
            "closed" if market.closed else "active",
            "/".join(market.outcomes),
            "/".join(f"{price:g}" for price in market.outcome_prices),
            market.short_condition_id,
            "yes" if market.has_clob_token_ids else "no",
        )
        for market in markets
    ]

    widths = [
        max(len(str(row[index])) for row in (headers, *rows)) if rows else len(header)
        for index, header in enumerate(headers)
    ]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def _queries_for_asset(asset: str) -> tuple[str, str]:
    display_names = {
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "SOL": "Solana",
        "XRP": "XRP",
    }
    return (f"{display_names[asset]} Up or Down", f"{asset.lower()}-updown")


def _print_books_table(books: Sequence[clob.MarketTokenBook]) -> None:
    headers = (
        "asset",
        "slug",
        "outcome",
        "token",
        "bid",
        "ask",
        "spread",
        "bid size",
        "ask size",
        "status",
    )
    rows = [
        (
            item.market.asset,
            item.market.slug,
            item.outcome,
            _short_token_id(item.token_id),
            _fmt_price(item.book.best_bid),
            _fmt_price(item.book.best_ask),
            _fmt_price(item.book.spread),
            _fmt_size(item.book.best_bid_size),
            _fmt_size(item.book.best_ask_size),
            "ok" if item.book.available else item.book.error or "unavailable",
        )
        for item in books
    ]

    widths = [
        max(len(str(row[index])) for row in (headers, *rows)) if rows else len(header)
        for index, header in enumerate(headers)
    ]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def _short_token_id(token_id: str) -> str:
    if len(token_id) <= 14:
        return token_id
    return f"{token_id[:6]}...{token_id[-4:]}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4g}"


def _fmt_size(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _print_price_table(points: Sequence[price_feed.PricePoint]) -> None:
    headers = ("symbol", "price", "timestamp UTC", "source")
    rows = [
        (
            point.symbol,
            f"{point.price:.8f}".rstrip("0").rstrip("."),
            point.timestamp_utc,
            point.source,
        )
        for point in points
    ]
    widths = [
        max(len(str(row[index])) for row in (headers, *rows)) if rows else len(header)
        for index, header in enumerate(headers)
    ]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def _print_ledger_summary(summary: ledger.LedgerSummary) -> None:
    rows = [
        ("total trades", str(summary.total_trades)),
        ("open trades", str(summary.open_trades)),
        ("won trades", str(summary.won_trades)),
        ("lost trades", str(summary.lost_trades)),
        ("cancelled trades", str(summary.cancelled_trades)),
        ("win rate", f"{summary.win_rate:.2%}"),
        ("realized pnl", _fmt_money(summary.realized_pnl)),
        ("open risk", _fmt_money(summary.open_risk)),
        ("total cost", _fmt_money(summary.total_cost)),
    ]
    widths = [
        max(len(str(row[index])) for row in (("metric", "value"), *rows))
        for index in range(2)
    ]
    print("metric".ljust(widths[0]) + " | " + "value".ljust(widths[1]))
    print("-" * widths[0] + "-+-" + "-" * widths[1])
    for metric, value in rows:
        print(metric.ljust(widths[0]) + " | " + value.ljust(widths[1]))


def _fmt_money(value: float) -> str:
    return f"${value:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
