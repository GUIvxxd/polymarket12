"""Command-line entrypoint for the paper-trading bot."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from polybot import discovery
from polybot import __version__
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


if __name__ == "__main__":
    raise SystemExit(main())
