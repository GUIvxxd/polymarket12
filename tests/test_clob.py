from __future__ import annotations

from polybot import clob
from polybot.discovery import CryptoUpDownMarket
from polybot.main import build_parser


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, text: str = "") -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


def test_order_book_parser_computes_best_prices_from_unsorted_levels() -> None:
    book = clob.parse_order_book(
        {
            "market": "0xcondition",
            "asset_id": "token-1",
            "timestamp": "1783173612561",
            "bids": [
                {"price": "0.01", "size": "100"},
                {"price": "0.51", "size": "25.5"},
                {"price": "0.49", "size": "10"},
            ],
            "asks": [
                {"price": "0.99", "size": "10"},
                {"price": "0.52", "size": "30"},
                {"price": "0.57", "size": "40"},
            ],
            "min_order_size": "5",
            "tick_size": "0.01",
            "last_trade_price": "0.480",
        }
    )

    assert book.token_id == "token-1"
    assert book.market == "0xcondition"
    assert book.best_bid == 0.51
    assert book.best_ask == 0.52
    assert book.spread == 0.010000000000000009
    assert book.best_bid_size == 25.5
    assert book.best_ask_size == 30
    assert [level.price for level in book.top_bids] == [0.51, 0.49, 0.01]
    assert [level.price for level in book.top_asks] == [0.52, 0.57, 0.99]


def test_clob_client_fetches_book_with_public_get(monkeypatch) -> None:
    calls = []

    class FakeHttpClient:
        def __init__(self, **kwargs) -> None:
            calls.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, path, *, params):
            calls.append(("get", path, params))
            return FakeResponse(
                {
                    "asset_id": "token-1",
                    "bids": [{"price": "0.4", "size": "10"}],
                    "asks": [{"price": "0.5", "size": "15"}],
                }
            )

    monkeypatch.setattr(clob.httpx, "Client", FakeHttpClient)

    book = clob.CLOBClient().fetch_order_book("token-1")

    assert book.best_bid == 0.4
    assert book.best_ask == 0.5
    assert calls[0][0] == "init"
    assert "Authorization" not in calls[0][1]["headers"]
    assert calls[1] == ("get", "/book", {"token_id": "token-1"})


def test_clob_client_returns_unavailable_book_for_missing_public_book(monkeypatch) -> None:
    class FakeHttpClient:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, path, *, params):
            return FakeResponse(
                {"error": "No orderbook exists for the requested token id"},
                status_code=404,
            )

    monkeypatch.setattr(clob.httpx, "Client", FakeHttpClient)

    book = clob.CLOBClient().fetch_order_book("closed-token")

    assert book.available is False
    assert book.best_bid is None
    assert book.error == "No orderbook exists for the requested token id"


def test_enrich_markets_with_books_maps_outcomes_to_token_ids() -> None:
    market = CryptoUpDownMarket(
        market_id="1",
        condition_id="0xcondition",
        slug="btc-updown-5m-1",
        title="Bitcoin Up or Down",
        asset="BTC",
        outcomes=["Up", "Down"],
        outcome_prices=[0.51, 0.49],
        clob_token_ids=["up-token", "down-token"],
        best_bid=None,
        best_ask=None,
        start_time=None,
        end_time=None,
        active=True,
        closed=False,
        resolution_source=None,
    )

    class FakeCLOBClient:
        def fetch_order_book(self, token_id):
            return clob.TokenOrderBook(
                token_id=token_id,
                market="0xcondition",
                bids=[clob.BookLevel(price=0.4, size=10)],
                asks=[clob.BookLevel(price=0.5, size=10)],
            )

    books = clob.enrich_markets_with_books([market], FakeCLOBClient())

    assert [(item.outcome, item.token_id) for item in books] == [
        ("Up", "up-token"),
        ("Down", "down-token"),
    ]


def test_cli_parser_includes_books_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["books", "--asset", "btc", "--limit", "10"])

    assert args.command == "books"
    assert args.asset == "btc"
    assert args.limit == 10

