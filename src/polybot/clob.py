"""Public, unauthenticated CLOB order book reads."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from polybot.discovery import CryptoUpDownMarket


CLOB_BASE_URL = "https://clob.polymarket.com"


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class TokenOrderBook:
    token_id: str
    market: str | None
    bids: list[BookLevel]
    asks: list[BookLevel]
    timestamp: str | None = None
    min_size: float | None = None
    tick_size: float | None = None
    last_trade_price: float | None = None
    available: bool = True
    error: str | None = None

    @property
    def best_bid(self) -> float | None:
        if not self.bids:
            return None
        return max(level.price for level in self.bids)

    @property
    def best_ask(self) -> float | None:
        if not self.asks:
            return None
        return min(level.price for level in self.asks)

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def best_bid_size(self) -> float | None:
        return _size_at_price(self.bids, self.best_bid)

    @property
    def best_ask_size(self) -> float | None:
        return _size_at_price(self.asks, self.best_ask)

    @property
    def top_bids(self) -> list[BookLevel]:
        return sorted(self.bids, key=lambda level: level.price, reverse=True)

    @property
    def top_asks(self) -> list[BookLevel]:
        return sorted(self.asks, key=lambda level: level.price)


@dataclass(frozen=True)
class MarketTokenBook:
    market: CryptoUpDownMarket
    outcome: str
    token_id: str
    book: TokenOrderBook


class CLOBClient:
    """Read-only client for public CLOB book endpoints."""

    def __init__(self, base_url: str = CLOB_BASE_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_order_book(self, token_id: str) -> TokenOrderBook:
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "polybot-paper-research/0.1"},
        ) as client:
            response = client.get("/book", params={"token_id": token_id})
            if response.status_code == 404:
                return unavailable_order_book(token_id, _error_message(response))
            response.raise_for_status()
            return parse_order_book(response.json(), token_id=token_id)


def parse_order_book(payload: dict[str, Any], *, token_id: str | None = None) -> TokenOrderBook:
    resolved_token_id = str(token_id or payload.get("asset_id") or "")
    return TokenOrderBook(
        token_id=resolved_token_id,
        market=_optional_text(payload.get("market")),
        bids=_parse_levels(payload.get("bids")),
        asks=_parse_levels(payload.get("asks")),
        timestamp=_optional_text(payload.get("timestamp")),
        min_size=_to_float(payload.get("min_order_size")),
        tick_size=_to_float(payload.get("tick_size")),
        last_trade_price=_to_float(payload.get("last_trade_price")),
    )


def unavailable_order_book(token_id: str, error: str) -> TokenOrderBook:
    return TokenOrderBook(
        token_id=token_id,
        market=None,
        bids=[],
        asks=[],
        available=False,
        error=error,
    )


def fetch_market_order_books(
    market: CryptoUpDownMarket,
    client: CLOBClient,
) -> list[MarketTokenBook]:
    books: list[MarketTokenBook] = []
    for index, token_id in enumerate(market.clob_token_ids):
        outcome = market.outcomes[index] if index < len(market.outcomes) else f"token-{index + 1}"
        books.append(
            MarketTokenBook(
                market=market,
                outcome=outcome,
                token_id=token_id,
                book=client.fetch_order_book(token_id),
            )
        )
    return books


def enrich_markets_with_books(
    markets: Sequence[CryptoUpDownMarket],
    client: CLOBClient,
) -> list[MarketTokenBook]:
    enriched: list[MarketTokenBook] = []
    for market in markets:
        enriched.extend(fetch_market_order_books(market, client))
    return enriched


def _parse_levels(value: Any) -> list[BookLevel]:
    if not isinstance(value, list):
        return []

    levels: list[BookLevel] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        price = _to_float(item.get("price"))
        size = _to_float(item.get("size"))
        if price is None or size is None:
            continue
        levels.append(BookLevel(price=price, size=size))
    return levels


def _size_at_price(levels: Sequence[BookLevel], price: float | None) -> float | None:
    if price is None:
        return None
    for level in levels:
        if level.price == price:
            return level.size
    return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return f"HTTP {response.status_code}"

