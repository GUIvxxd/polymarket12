from __future__ import annotations

from io import StringIO

from rich.console import Console

from polybot import clob, live
from polybot.dashboard import PaperDashboard
from polybot.discovery import CryptoUpDownMarket
from polybot.ledger import SQLiteLedger
from polybot.main import build_parser
from polybot.price_feed import PricePoint
from polybot.resolver import ResolverRunSummary


class FakePriceFeed:
    def get_price(self, symbol: str) -> PricePoint:
        return PricePoint(
            symbol=symbol,
            price=101.0,
            timestamp_utc="2026-07-06T12:00:00Z",
            source="fake",
        )


class FakeCLOBClient:
    def __init__(self, up_ask: float) -> None:
        self.up_ask = up_ask

    def fetch_order_book(self, token_id: str) -> clob.TokenOrderBook:
        if token_id == "up-token":
            return clob.TokenOrderBook(
                token_id=token_id,
                market="0xcondition",
                bids=[clob.BookLevel(price=self.up_ask - 0.01, size=50.0)],
                asks=[clob.BookLevel(price=self.up_ask, size=50.0)],
            )
        return clob.TokenOrderBook(
            token_id=token_id,
            market="0xcondition",
            bids=[clob.BookLevel(price=0.39, size=50.0)],
            asks=[clob.BookLevel(price=0.99, size=50.0)],
        )


class FakeResolver:
    def resolve_open_trades(self) -> ResolverRunSummary:
        return ResolverRunSummary(
            checked=0,
            resolved=0,
            won=0,
            lost=0,
            unresolved=0,
            results=[],
        )


def fake_discovery(client, *, limit, include_closed, queries):
    return [make_market()]


def make_market() -> CryptoUpDownMarket:
    return CryptoUpDownMarket(
        market_id="market-1",
        condition_id="0xcondition",
        slug="btc-updown-5m-1",
        title="Bitcoin Up or Down",
        asset="BTC",
        outcomes=["Up", "Down"],
        outcome_prices=[0.5, 0.5],
        clob_token_ids=["up-token", "down-token"],
        best_bid=None,
        best_ask=None,
        start_time="2026-07-06T12:00:00Z",
        end_time="2099-07-06T12:05:00Z",
        active=True,
        closed=False,
        resolution_source="https://data.chain.link/streams/btc-usd",
    )


def make_runner(tmp_path, *, up_ask: float) -> live.LivePaperRunner:
    store = SQLiteLedger(tmp_path / "paper_trades.sqlite")
    return live.LivePaperRunner(
        live.LivePaperConfig(
            symbols=("BTC",),
            db_path=tmp_path / "paper_trades.sqlite",
            stake=10.0,
            min_edge=0.08,
            min_edge_after_slippage=0.05,
            slippage_cents=0.02,
            market_limit=1,
            max_seconds_remaining=10_000_000_000.0,
            iterations=1,
        ),
        gamma_client=None,
        clob_client=FakeCLOBClient(up_ask),
        price_feed=FakePriceFeed(),
        ledger=store,
        trade_resolver=FakeResolver(),
        market_discovery=fake_discovery,
        reference_price_reader=lambda market: 100.0,
        sleep=lambda seconds: None,
    )


def test_one_loop_iteration_creates_trade_for_qualifying_signal(tmp_path) -> None:
    runner = make_runner(tmp_path, up_ask=0.70)

    result = runner.run_once()

    assert result.markets_checked == 1
    assert result.books_checked == 2
    assert len(result.signals) == 1
    assert len(result.trades) == 1
    assert result.ledger_summary.total_trades == 1
    assert result.ledger_summary.open_trades == 1


def test_one_loop_iteration_creates_no_trade_for_non_qualifying_signal(tmp_path) -> None:
    runner = make_runner(tmp_path, up_ask=0.85)

    result = runner.run_once()

    assert result.markets_checked == 1
    assert len(result.signals) == 0
    assert len(result.trades) == 0
    assert len(result.skipped_signals) >= 1
    assert any("edge" in skip.reason for skip in result.skipped_signals)
    assert runner.ledger.count_signal_skips() == len(result.skipped_signals)
    assert result.ledger_summary.total_trades == 0


def test_extract_reference_price_reads_event_metadata() -> None:
    payload = {
        "events": [
            {
                "eventMetadata": {
                    "priceToBeat": 66558.86440848414,
                }
            }
        ]
    }

    assert live.extract_reference_price(payload) == 66558.86440848414


def test_dashboard_renders_loop_result(tmp_path) -> None:
    result = make_runner(tmp_path, up_ask=0.70).run_once()
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=120)

    PaperDashboard(console).render(result)

    assert "Polymarket Paper Bot" in output.getvalue()


def test_cli_parser_includes_run_paper_command() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "run-paper",
            "--symbols",
            "BTC",
            "ETH",
            "--stake",
            "10",
            "--min-edge",
            "0.08",
            "--slippage-cents",
            "0.02",
            "--latency-ms",
            "1500",
            "--iterations",
            "1",
        ]
    )

    assert args.command == "run-paper"
    assert args.symbols == ["BTC", "ETH"]
    assert args.stake == 10
    assert args.iterations == 1
