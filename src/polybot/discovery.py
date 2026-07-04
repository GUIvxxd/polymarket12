"""Discovery and normalization for crypto up/down Gamma markets."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polybot.gamma import GammaClient


UNKNOWN_ASSET = "UNKNOWN"

SEARCH_QUERIES = (
    "Bitcoin Up or Down",
    "Ethereum Up or Down",
    "Solana Up or Down",
    "XRP Up or Down",
    "btc-updown",
    "eth-updown",
    "sol-updown",
    "xrp-updown",
)

ASSET_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "BTC",
        (
            re.compile(r"\bbtc-updown\b", re.IGNORECASE),
            re.compile(r"\bbitcoin\s+up\s+or\s+down\b", re.IGNORECASE),
        ),
    ),
    (
        "ETH",
        (
            re.compile(r"\beth-updown\b", re.IGNORECASE),
            re.compile(r"\bethereum\s+up\s+or\s+down\b", re.IGNORECASE),
        ),
    ),
    (
        "SOL",
        (
            re.compile(r"\bsol-updown\b", re.IGNORECASE),
            re.compile(r"\bsolana\s+up\s+or\s+down\b", re.IGNORECASE),
        ),
    ),
    (
        "XRP",
        (
            re.compile(r"\bxrp-updown\b", re.IGNORECASE),
            re.compile(r"\bxrp\s+up\s+or\s+down\b", re.IGNORECASE),
        ),
    ),
)


@dataclass(frozen=True)
class CryptoUpDownMarket:
    market_id: str
    condition_id: str
    slug: str
    title: str
    asset: str
    outcomes: list[str]
    outcome_prices: list[float]
    clob_token_ids: list[str]
    best_bid: float | None
    best_ask: float | None
    start_time: str | None
    end_time: str | None
    active: bool
    closed: bool
    resolution_source: str | None

    @property
    def short_condition_id(self) -> str:
        if not self.condition_id:
            return ""
        if len(self.condition_id) <= 14:
            return self.condition_id
        return f"{self.condition_id[:8]}...{self.condition_id[-4:]}"

    @property
    def has_clob_token_ids(self) -> bool:
        return bool(self.clob_token_ids)


def parse_json_array(value: Any) -> list[Any]:
    """Parse Gamma list fields that may arrive as JSON-encoded strings."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not isinstance(value, str):
        return []

    stripped = value.strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return []

    return parsed if isinstance(parsed, list) else []


def detect_asset(slug: str | None, title: str | None) -> str:
    haystack = f"{slug or ''} {title or ''}"
    for asset, patterns in ASSET_PATTERNS:
        if any(pattern.search(haystack) for pattern in patterns):
            return asset
    return UNKNOWN_ASSET


def is_crypto_up_down_payload(
    market: Mapping[str, Any],
    *,
    event: Mapping[str, Any] | None = None,
) -> bool:
    slug = _first_text(market, event, "slug")
    title = _first_text(market, event, "question", "title")
    return detect_asset(slug, title) != UNKNOWN_ASSET


def normalize_market(
    market: Mapping[str, Any],
    *,
    event: Mapping[str, Any] | None = None,
) -> CryptoUpDownMarket:
    slug = _first_text(market, event, "slug")
    title = _first_text(market, event, "question", "title")
    outcomes = [str(item) for item in parse_json_array(market.get("outcomes"))]
    prices = [_to_float(item) for item in parse_json_array(market.get("outcomePrices"))]
    token_ids = [str(item) for item in parse_json_array(market.get("clobTokenIds"))]

    return CryptoUpDownMarket(
        market_id=str(market.get("id") or ""),
        condition_id=str(market.get("conditionId") or ""),
        slug=slug,
        title=title,
        asset=detect_asset(slug, title),
        outcomes=outcomes,
        outcome_prices=[price for price in prices if price is not None],
        clob_token_ids=token_ids,
        best_bid=_to_float(market.get("bestBid")),
        best_ask=_to_float(market.get("bestAsk")),
        start_time=_first_text(market, event, "startDate"),
        end_time=_first_text(market, event, "endDate"),
        active=_first_bool(market, event, "active"),
        closed=_first_bool(market, event, "closed"),
        resolution_source=_first_text(market, event, "resolutionSource") or None,
    )


def discover_crypto_up_down_markets(
    client: GammaClient,
    *,
    limit: int = 20,
    include_closed: bool = False,
    queries: Sequence[str] = SEARCH_QUERIES,
) -> list[CryptoUpDownMarket]:
    if limit <= 0:
        return []

    discovered: list[CryptoUpDownMarket] = []
    seen_keys: set[str] = set()

    for market, event in _candidate_payloads(client, queries=queries, search_limit=limit):
        if not is_crypto_up_down_payload(market, event=event):
            continue
        normalized = normalize_market(market, event=event)
        if normalized.closed and not include_closed:
            continue

        key = normalized.condition_id or normalized.market_id or normalized.slug
        if key in seen_keys:
            continue
        seen_keys.add(key)
        discovered.append(normalized)

        if len(discovered) >= limit:
            break

    return discovered


def _candidate_payloads(
    client: GammaClient,
    *,
    queries: Sequence[str],
    search_limit: int,
) -> Iterable[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    for query in queries:
        payload = client.public_search(query, limit=search_limit)
        for event in _search_events(payload):
            markets = event.get("markets")
            if isinstance(markets, list) and markets:
                for market in markets:
                    if isinstance(market, Mapping):
                        yield market, event
            else:
                yield event, event

    for market in client.fetch_markets(limit=max(search_limit, 100)):
        yield market, None


def _search_events(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    events = payload.get("events")
    if isinstance(events, list):
        return [event for event in events if isinstance(event, Mapping)]

    results = payload.get("results")
    if isinstance(results, list):
        return [result for result in results if isinstance(result, Mapping)]

    return []


def _first_text(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any] | None,
    *keys: str,
) -> str:
    for source in (primary, fallback):
        if source is None:
            continue
        for key in keys:
            value = source.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
    return ""


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_bool(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any] | None,
    key: str,
) -> bool:
    for source in (primary, fallback):
        if source is None or key not in source:
            continue
        value = source[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no", ""}:
                return False
        return bool(value)
    return False
