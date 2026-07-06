"""Resolve open paper trades from public market data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from polybot.discovery import parse_json_array
from polybot.gamma import GammaClient
from polybot.ledger import LOST, WON, PaperTrade, SQLiteLedger
from polybot.price_feed import timestamp_utc


WINNER_PRICE_THRESHOLD = 0.99


@dataclass(frozen=True)
class MarketResolution:
    closed: bool
    winning_outcome: str | None
    reason: str


@dataclass(frozen=True)
class TradeResolutionResult:
    trade_id: str
    market_slug: str
    status: str
    resolved: bool
    winning_outcome: str | None
    payout: float
    pnl: float
    reason: str


@dataclass(frozen=True)
class ResolverRunSummary:
    checked: int
    resolved: int
    won: int
    lost: int
    unresolved: int
    results: list[TradeResolutionResult]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PaperTradeResolver:
    def __init__(
        self,
        ledger: SQLiteLedger,
        gamma_client: GammaClient,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.ledger = ledger
        self.gamma_client = gamma_client
        self.clock = clock

    def resolve_open_trades(self) -> ResolverRunSummary:
        results = [self.resolve_trade(trade) for trade in self.ledger.open_trades()]
        return ResolverRunSummary(
            checked=len(results),
            resolved=sum(1 for result in results if result.resolved),
            won=sum(1 for result in results if result.status == WON),
            lost=sum(1 for result in results if result.status == LOST),
            unresolved=sum(1 for result in results if not result.resolved),
            results=results,
        )

    def resolve_trade(self, trade: PaperTrade) -> TradeResolutionResult:
        market_payload = find_market_payload(self.gamma_client, trade)
        if market_payload is None:
            return _unresolved_result(trade, "market not found in public Gamma data")

        resolution = extract_market_resolution(market_payload)
        if not resolution.closed:
            return _unresolved_result(trade, resolution.reason)

        if resolution.winning_outcome is None:
            return _unresolved_result(trade, resolution.reason)

        won = _normalize_outcome(trade.outcome) == _normalize_outcome(resolution.winning_outcome)
        payout = trade.paper_size if won else 0.0
        pnl = payout - trade.paper_cost
        status = WON if won else LOST
        reason = f"{trade.reason}; resolved winner={resolution.winning_outcome}"

        self.ledger.update_resolution(
            trade_id=trade.trade_id,
            status=status,
            resolved_at_utc=timestamp_utc(self.clock()),
            payout=payout,
            pnl=pnl,
            reason=reason,
        )
        return TradeResolutionResult(
            trade_id=trade.trade_id,
            market_slug=trade.market_slug,
            status=status,
            resolved=True,
            winning_outcome=resolution.winning_outcome,
            payout=payout,
            pnl=pnl,
            reason=reason,
        )


def find_market_payload(
    gamma_client: GammaClient,
    trade: PaperTrade,
) -> Mapping[str, Any] | None:
    if trade.market_slug and hasattr(gamma_client, "fetch_market_by_slug"):
        direct_match = gamma_client.fetch_market_by_slug(trade.market_slug)
        if isinstance(direct_match, Mapping) and _matches_trade(direct_match, trade):
            return direct_match

    for query in _resolution_queries(trade):
        payload = gamma_client.public_search(query, limit=10)
        match = _find_market_in_search_payload(payload, trade)
        if match is not None:
            return match

    for params in _market_lookup_params(trade):
        for market in gamma_client.fetch_markets(limit=20, extra_params=params):
            if _matches_trade(market, trade):
                return market

    return None


def extract_market_resolution(market: Mapping[str, Any]) -> MarketResolution:
    if not _bool_field(market.get("closed")):
        return MarketResolution(False, None, "market is not closed")

    explicit_winner = _explicit_winner(market)
    if explicit_winner is not None:
        return MarketResolution(True, explicit_winner, "winner found in explicit public field")

    outcomes = [str(outcome) for outcome in parse_json_array(market.get("outcomes"))]
    outcome_prices = [_to_float(price) for price in parse_json_array(market.get("outcomePrices"))]
    if not outcomes or len(outcomes) != len(outcome_prices):
        return MarketResolution(True, None, "winner unavailable: outcomes/prices are incomplete")

    priced_outcomes = [
        (outcome, price)
        for outcome, price in zip(outcomes, outcome_prices, strict=False)
        if price is not None
    ]
    winners = [
        outcome
        for outcome, price in priced_outcomes
        if price >= WINNER_PRICE_THRESHOLD
    ]
    if len(winners) == 1:
        return MarketResolution(True, winners[0], "winner inferred from final outcome prices")

    return MarketResolution(True, None, "winner unavailable: no decisive final outcome price")


def _resolution_queries(trade: PaperTrade) -> tuple[str, ...]:
    values = [trade.market_slug, trade.condition_id]
    return tuple(value for value in values if value)


def _market_lookup_params(trade: PaperTrade) -> tuple[dict[str, str], ...]:
    params: list[dict[str, str]] = []
    if trade.market_slug:
        params.append({"slug": trade.market_slug})
    if trade.condition_id:
        params.append({"conditionId": trade.condition_id})
    return tuple(params)


def _find_market_in_search_payload(
    payload: Mapping[str, Any],
    trade: PaperTrade,
) -> Mapping[str, Any] | None:
    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            markets = event.get("markets")
            if isinstance(markets, list):
                for market in markets:
                    if isinstance(market, Mapping) and _matches_trade(market, trade):
                        return market
            if _matches_trade(event, trade):
                return event

    results = payload.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, Mapping) and _matches_trade(result, trade):
                return result

    return None


def _matches_trade(market: Mapping[str, Any], trade: PaperTrade) -> bool:
    slug = str(market.get("slug") or "")
    condition_id = str(market.get("conditionId") or "")
    return bool(
        (trade.market_slug and slug == trade.market_slug)
        or (trade.condition_id and condition_id.lower() == trade.condition_id.lower())
    )


def _explicit_winner(market: Mapping[str, Any]) -> str | None:
    outcomes = [str(outcome) for outcome in parse_json_array(market.get("outcomes"))]
    for key in ("winningOutcome", "winner", "resolvedOutcome"):
        value = market.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if not outcomes:
            return text
        for outcome in outcomes:
            if _normalize_outcome(outcome) == _normalize_outcome(text):
                return outcome
    return None


def _unresolved_result(trade: PaperTrade, reason: str) -> TradeResolutionResult:
    return TradeResolutionResult(
        trade_id=trade.trade_id,
        market_slug=trade.market_slug,
        status=trade.status,
        resolved=False,
        winning_outcome=None,
        payout=trade.payout,
        pnl=trade.pnl,
        reason=reason,
    )


def _normalize_outcome(value: str) -> str:
    return value.strip().lower()


def _bool_field(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
