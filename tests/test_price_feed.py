from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polybot import price_feed
from polybot.main import build_parser


FIXED_TIMESTAMP = "2026-07-04T12:00:00Z"
FIXED_NOW = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def test_parse_binance_price_payload() -> None:
    point = price_feed.parse_binance_price(
        {"symbol": "BTCUSDT", "price": "62689.99000000"},
        requested_symbol="BTC",
        timestamp_utc=FIXED_TIMESTAMP,
    )

    assert point.symbol == "BTC"
    assert point.price == 62689.99
    assert point.timestamp_utc == FIXED_TIMESTAMP
    assert point.source == "binance"


def test_parse_coinbase_price_payload() -> None:
    point = price_feed.parse_coinbase_price(
        {"data": {"amount": "62621.665", "base": "BTC", "currency": "USD"}},
        requested_symbol="BTC",
        timestamp_utc=FIXED_TIMESTAMP,
    )

    assert point.symbol == "BTC"
    assert point.price == 62621.665
    assert point.timestamp_utc == FIXED_TIMESTAMP
    assert point.source == "coinbase"


def test_binance_feed_uses_public_get_without_auth_headers(monkeypatch) -> None:
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
            return FakeResponse({"symbol": "ETHUSDT", "price": "3456.78"})

    monkeypatch.setattr(price_feed.httpx, "Client", FakeHttpClient)

    point = price_feed.BinanceSpotPriceFeed(clock=lambda: FIXED_NOW).get_price("ETH")

    assert point.symbol == "ETH"
    assert point.price == 3456.78
    assert point.timestamp_utc == FIXED_TIMESTAMP
    assert calls[0][0] == "init"
    assert "Authorization" not in calls[0][1]["headers"]
    assert calls[1] == ("get", "/api/v3/ticker/price", {"symbol": "ETHUSDT"})


def test_coinbase_feed_uses_public_get_without_auth_headers(monkeypatch) -> None:
    calls = []

    class FakeHttpClient:
        def __init__(self, **kwargs) -> None:
            calls.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, path):
            calls.append(("get", path))
            return FakeResponse({"data": {"amount": "132.50", "base": "SOL", "currency": "USD"}})

    monkeypatch.setattr(price_feed.httpx, "Client", FakeHttpClient)

    point = price_feed.CoinbasePriceFeed(clock=lambda: FIXED_NOW).get_price("SOL")

    assert point.symbol == "SOL"
    assert point.price == 132.5
    assert point.source == "coinbase"
    assert "Authorization" not in calls[0][1]["headers"]
    assert calls[1] == ("get", "/v2/prices/SOL-USD/spot")


def test_fallback_feed_uses_coinbase_when_binance_fails() -> None:
    class BrokenFeed:
        def get_price(self, symbol):
            raise price_feed.PriceFeedError("binance blocked")

    class WorkingFeed:
        def get_price(self, symbol):
            return price_feed.PricePoint(
                symbol=symbol,
                price=100.0,
                timestamp_utc=FIXED_TIMESTAMP,
                source="coinbase",
            )

    point = price_feed.FallbackPriceFeed((BrokenFeed(), WorkingFeed())).get_price("BTC")

    assert point.source == "coinbase"
    assert point.price == 100.0


def test_unsupported_symbol_raises_price_feed_error() -> None:
    with pytest.raises(price_feed.PriceFeedError):
        price_feed.normalize_symbol("DOGE")


def test_cli_parser_includes_price_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["price", "BTC", "ETH"])

    assert args.command == "price"
    assert args.symbols == ["BTC", "ETH"]

