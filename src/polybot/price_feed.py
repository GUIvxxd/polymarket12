"""Public crypto spot price feeds."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx


BINANCE_BASE_URL = "https://api.binance.com"
COINBASE_BASE_URL = "https://api.coinbase.com"

SUPPORTED_SYMBOLS = ("BTC", "ETH", "SOL", "XRP")
BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}
COINBASE_PRODUCTS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
}


@dataclass(frozen=True)
class PricePoint:
    symbol: str
    price: float
    timestamp_utc: str
    source: str


class PriceFeed(Protocol):
    def get_price(self, symbol: str) -> PricePoint:
        """Return a public spot price for a supported crypto symbol."""
        ...


class PriceFeedError(RuntimeError):
    """Raised when a public price feed cannot produce a valid price."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BinanceSpotPriceFeed:
    def __init__(
        self,
        base_url: str = BINANCE_BASE_URL,
        timeout: float = 10.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.clock = clock

    def get_price(self, symbol: str) -> PricePoint:
        normalized = normalize_symbol(symbol)
        binance_symbol = BINANCE_SYMBOLS[normalized]
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "polybot-paper-research/0.1"},
        ) as client:
            response = client.get("/api/v3/ticker/price", params={"symbol": binance_symbol})
            response.raise_for_status()
            return parse_binance_price(
                response.json(),
                requested_symbol=normalized,
                timestamp_utc=timestamp_utc(self.clock()),
            )


class CoinbasePriceFeed:
    def __init__(
        self,
        base_url: str = COINBASE_BASE_URL,
        timeout: float = 10.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.clock = clock

    def get_price(self, symbol: str) -> PricePoint:
        normalized = normalize_symbol(symbol)
        product = COINBASE_PRODUCTS[normalized]
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "polybot-paper-research/0.1"},
        ) as client:
            response = client.get(f"/v2/prices/{product}/spot")
            response.raise_for_status()
            return parse_coinbase_price(
                response.json(),
                requested_symbol=normalized,
                timestamp_utc=timestamp_utc(self.clock()),
            )


class FallbackPriceFeed:
    def __init__(self, feeds: Sequence[PriceFeed]) -> None:
        if not feeds:
            raise ValueError("At least one price feed is required.")
        self.feeds = tuple(feeds)

    def get_price(self, symbol: str) -> PricePoint:
        errors: list[str] = []
        for feed in self.feeds:
            try:
                return feed.get_price(symbol)
            except Exception as exc:
                errors.append(f"{feed.__class__.__name__}: {exc}")
        raise PriceFeedError(f"All price feeds failed for {symbol}: {'; '.join(errors)}")


def build_default_price_feed() -> FallbackPriceFeed:
    return FallbackPriceFeed((BinanceSpotPriceFeed(), CoinbasePriceFeed()))


def parse_binance_price(
    payload: dict[str, Any],
    *,
    requested_symbol: str,
    timestamp_utc: str,
) -> PricePoint:
    actual_symbol = str(payload.get("symbol") or "").upper()
    expected_symbol = BINANCE_SYMBOLS[normalize_symbol(requested_symbol)]
    if actual_symbol != expected_symbol:
        raise PriceFeedError(f"Unexpected Binance symbol {actual_symbol!r}; expected {expected_symbol!r}.")

    price = _to_float(payload.get("price"))
    if price is None:
        raise PriceFeedError("Binance response did not include a valid price.")

    return PricePoint(
        symbol=normalize_symbol(requested_symbol),
        price=price,
        timestamp_utc=timestamp_utc,
        source="binance",
    )


def parse_coinbase_price(
    payload: dict[str, Any],
    *,
    requested_symbol: str,
    timestamp_utc: str,
) -> PricePoint:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PriceFeedError("Coinbase response did not include data.")

    actual_symbol = str(data.get("base") or "").upper()
    expected_symbol = normalize_symbol(requested_symbol)
    if actual_symbol != expected_symbol:
        raise PriceFeedError(f"Unexpected Coinbase symbol {actual_symbol!r}; expected {expected_symbol!r}.")

    price = _to_float(data.get("amount"))
    if price is None:
        raise PriceFeedError("Coinbase response did not include a valid price.")

    return PricePoint(
        symbol=expected_symbol,
        price=price,
        timestamp_utc=timestamp_utc,
        source="coinbase",
    )


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized not in SUPPORTED_SYMBOLS:
        raise PriceFeedError(f"Unsupported symbol {symbol!r}. Expected one of {', '.join(SUPPORTED_SYMBOLS)}.")
    return normalized


def timestamp_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
