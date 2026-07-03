"""Command-line entrypoint for the paper-trading bot."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from polybot import __version__
from polybot.config import BotConfig


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

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

