"""Fair-value models and paper signal generation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from polybot.clob import MarketTokenBook
from polybot.discovery import CryptoUpDownMarket
from polybot.price_feed import PricePoint


BUY_UP = "BUY_UP"
BUY_DOWN = "BUY_DOWN"


@dataclass(frozen=True)
class FairValueEstimate:
    fair_up_probability: float
    fair_down_probability: float
    reason: str


@dataclass(frozen=True)
class NearExpiryThresholdModel:
    min_seconds_remaining: float
    max_seconds_remaining: float
    min_bps_distance: float
    leading_probability: float = 0.90

    def estimate(
        self,
        *,
        start_price: float,
        current_price: float,
        seconds_remaining: float,
    ) -> FairValueEstimate:
        if start_price <= 0 or current_price <= 0:
            return _neutral("missing or invalid price input")

        if seconds_remaining < self.min_seconds_remaining:
            return _neutral("below minimum seconds remaining")

        if seconds_remaining > self.max_seconds_remaining:
            return _neutral("above maximum seconds remaining")

        distance_bps = ((current_price - start_price) / start_price) * 10_000
        if abs(distance_bps) < self.min_bps_distance:
            return _neutral(
                f"distance {distance_bps:.2f} bps below {self.min_bps_distance:.2f} bps threshold"
            )

        leading = _clamp_probability(self.leading_probability)
        trailing = 1.0 - leading
        if distance_bps > 0:
            return FairValueEstimate(
                fair_up_probability=leading,
                fair_down_probability=trailing,
                reason=f"current price is {distance_bps:.2f} bps above start price",
            )

        return FairValueEstimate(
            fair_up_probability=trailing,
            fair_down_probability=leading,
            reason=f"current price is {abs(distance_bps):.2f} bps below start price",
        )


@dataclass(frozen=True)
class VolatilityProbabilityModel:
    short_term_volatility: float

    def estimate(
        self,
        *,
        start_price: float,
        current_price: float,
        seconds_remaining: float,
    ) -> FairValueEstimate:
        if start_price <= 0 or current_price <= 0:
            return _neutral("missing or invalid price input")

        if seconds_remaining <= 0:
            return _deterministic_expired_estimate(start_price, current_price)

        if self.short_term_volatility <= 0:
            return _neutral("short-term volatility must be positive")

        denominator = self.short_term_volatility * math.sqrt(seconds_remaining)
        if denominator <= 0:
            return _neutral("volatility denominator must be positive")

        z_score = math.log(current_price / start_price) / denominator
        fair_up = _normal_cdf(z_score)
        fair_down = 1.0 - fair_up
        return FairValueEstimate(
            fair_up_probability=fair_up,
            fair_down_probability=fair_down,
            reason=f"volatility-adjusted z-score {z_score:.4f}",
        )


class FairValueModel(Protocol):
    def estimate(
        self,
        *,
        start_price: float,
        current_price: float,
        seconds_remaining: float,
    ) -> FairValueEstimate:
        """Estimate up/down fair probabilities."""


@dataclass(frozen=True)
class ModelConfig:
    start_price: float
    min_edge: float = 0.08
    min_liquidity: float = 5.0
    suggested_stake: float = 10.0
    min_seconds_remaining: float = 0.0
    max_seconds_remaining: float = 900.0
    min_bps_distance: float = 5.0
    leading_probability: float = 0.90
    short_term_volatility: float | None = None
    seconds_remaining: float | None = None
    now_utc: datetime | None = None
    fair_value_model: FairValueModel | None = None


@dataclass(frozen=True)
class Signal:
    side: str
    market_slug: str
    condition_id: str
    token_id: str
    outcome: str
    fair_probability: float
    ask_price: float
    ask_size: float
    edge: float
    suggested_stake: float
    seconds_remaining: float
    reason: str


@dataclass(frozen=True)
class SignalRejection:
    market_slug: str
    condition_id: str
    side: str | None
    outcome: str | None
    token_id: str | None
    reason: str
    fair_probability: float | None = None
    ask_price: float | None = None
    ask_size: float | None = None
    edge: float | None = None
    seconds_remaining: float | None = None


@dataclass(frozen=True)
class SignalEvaluation:
    signal: Signal | None
    rejections: list[SignalRejection]


def decide_signal(
    market: CryptoUpDownMarket,
    orderbook: Sequence[MarketTokenBook],
    price_point: PricePoint,
    model_config: ModelConfig,
) -> Signal | None:
    return evaluate_signal(market, orderbook, price_point, model_config).signal


def evaluate_signal(
    market: CryptoUpDownMarket,
    orderbook: Sequence[MarketTokenBook],
    price_point: PricePoint,
    model_config: ModelConfig,
) -> SignalEvaluation:
    if market.closed:
        return SignalEvaluation(
            signal=None,
            rejections=[_market_rejection(market, "market closed")],
        )

    if not market.active:
        return SignalEvaluation(
            signal=None,
            rejections=[_market_rejection(market, "market inactive")],
        )

    seconds_remaining = _seconds_remaining(market, model_config)
    if seconds_remaining is None:
        return SignalEvaluation(
            signal=None,
            rejections=[_market_rejection(market, "seconds remaining unavailable")],
        )

    if seconds_remaining < model_config.min_seconds_remaining:
        return SignalEvaluation(
            signal=None,
            rejections=[
                _market_rejection(
                    market,
                    (
                        f"seconds remaining {seconds_remaining:.2f} below "
                        f"{model_config.min_seconds_remaining:.2f}"
                    ),
                    seconds_remaining=seconds_remaining,
                )
            ],
        )

    if seconds_remaining > model_config.max_seconds_remaining:
        return SignalEvaluation(
            signal=None,
            rejections=[
                _market_rejection(
                    market,
                    (
                        f"seconds remaining {seconds_remaining:.2f} above "
                        f"{model_config.max_seconds_remaining:.2f}"
                    ),
                    seconds_remaining=seconds_remaining,
                )
            ],
        )

    estimate = _select_model(model_config).estimate(
        start_price=model_config.start_price,
        current_price=price_point.price,
        seconds_remaining=seconds_remaining,
    )

    evaluated_candidates = [
        _evaluate_candidate_signal(
            side=BUY_UP,
            outcome="Up",
            fair_probability=estimate.fair_up_probability,
            market=market,
            orderbook=orderbook,
            model_config=model_config,
            seconds_remaining=seconds_remaining,
            model_reason=estimate.reason,
        ),
        _evaluate_candidate_signal(
            side=BUY_DOWN,
            outcome="Down",
            fair_probability=estimate.fair_down_probability,
            market=market,
            orderbook=orderbook,
            model_config=model_config,
            seconds_remaining=seconds_remaining,
            model_reason=estimate.reason,
        ),
    ]
    signals = [signal for signal, _rejection in evaluated_candidates if signal is not None]
    rejections = [
        rejection for _signal, rejection in evaluated_candidates if rejection is not None
    ]
    if not signals:
        return SignalEvaluation(signal=None, rejections=rejections)

    return SignalEvaluation(
        signal=max(signals, key=lambda signal: signal.edge),
        rejections=rejections,
    )


def _select_model(model_config: ModelConfig) -> FairValueModel:
    if model_config.fair_value_model is not None:
        return model_config.fair_value_model

    if model_config.short_term_volatility is not None:
        return VolatilityProbabilityModel(model_config.short_term_volatility)

    return NearExpiryThresholdModel(
        min_seconds_remaining=model_config.min_seconds_remaining,
        max_seconds_remaining=model_config.max_seconds_remaining,
        min_bps_distance=model_config.min_bps_distance,
        leading_probability=model_config.leading_probability,
    )


def _evaluate_candidate_signal(
    *,
    side: str,
    outcome: str,
    fair_probability: float,
    market: CryptoUpDownMarket,
    orderbook: Sequence[MarketTokenBook],
    model_config: ModelConfig,
    seconds_remaining: float,
    model_reason: str,
) -> tuple[Signal | None, SignalRejection | None]:
    book = _find_book(orderbook, outcome)
    if book is None:
        return (
            None,
            _candidate_rejection(
                market=market,
                side=side,
                outcome=outcome,
                token_id=None,
                reason=f"{outcome} book unavailable",
                fair_probability=fair_probability,
                seconds_remaining=seconds_remaining,
            ),
        )

    if not book.book.available:
        reason = f"{outcome} book unavailable"
        if book.book.error:
            reason = f"{reason}: {book.book.error}"
        return (
            None,
            _candidate_rejection(
                market=market,
                side=side,
                outcome=outcome,
                token_id=book.token_id,
                reason=reason,
                fair_probability=fair_probability,
                seconds_remaining=seconds_remaining,
            ),
        )

    ask_price = book.book.best_ask
    ask_size = book.book.best_ask_size
    if ask_price is None or ask_size is None:
        return (
            None,
            _candidate_rejection(
                market=market,
                side=side,
                outcome=book.outcome,
                token_id=book.token_id,
                reason=f"{book.outcome} missing best ask",
                fair_probability=fair_probability,
                ask_price=ask_price,
                ask_size=ask_size,
                seconds_remaining=seconds_remaining,
            ),
        )

    if ask_size < model_config.min_liquidity:
        return (
            None,
            _candidate_rejection(
                market=market,
                side=side,
                outcome=book.outcome,
                token_id=book.token_id,
                reason=(
                    f"{book.outcome} ask size {ask_size:.4f} below "
                    f"{model_config.min_liquidity:.4f}"
                ),
                fair_probability=fair_probability,
                ask_price=ask_price,
                ask_size=ask_size,
                seconds_remaining=seconds_remaining,
            ),
        )

    edge = fair_probability - ask_price
    if edge < model_config.min_edge:
        return (
            None,
            _candidate_rejection(
                market=market,
                side=side,
                outcome=book.outcome,
                token_id=book.token_id,
                reason=f"{book.outcome} edge {edge:.4f} below {model_config.min_edge:.4f}",
                fair_probability=fair_probability,
                ask_price=ask_price,
                ask_size=ask_size,
                edge=edge,
                seconds_remaining=seconds_remaining,
            ),
        )

    return (
        Signal(
            side=side,
            market_slug=market.slug,
            condition_id=market.condition_id,
            token_id=book.token_id,
            outcome=book.outcome,
            fair_probability=fair_probability,
            ask_price=ask_price,
            ask_size=ask_size,
            edge=edge,
            suggested_stake=model_config.suggested_stake,
            seconds_remaining=seconds_remaining,
            reason=f"{model_reason}; edge {edge:.4f} >= {model_config.min_edge:.4f}",
        ),
        None,
    )


def _market_rejection(
    market: CryptoUpDownMarket,
    reason: str,
    *,
    seconds_remaining: float | None = None,
) -> SignalRejection:
    return SignalRejection(
        market_slug=market.slug,
        condition_id=market.condition_id,
        side=None,
        outcome=None,
        token_id=None,
        reason=reason,
        seconds_remaining=seconds_remaining,
    )


def _candidate_rejection(
    *,
    market: CryptoUpDownMarket,
    side: str,
    outcome: str,
    token_id: str | None,
    reason: str,
    fair_probability: float | None,
    ask_price: float | None = None,
    ask_size: float | None = None,
    edge: float | None = None,
    seconds_remaining: float | None = None,
) -> SignalRejection:
    return SignalRejection(
        market_slug=market.slug,
        condition_id=market.condition_id,
        side=side,
        outcome=outcome,
        token_id=token_id,
        reason=reason,
        fair_probability=fair_probability,
        ask_price=ask_price,
        ask_size=ask_size,
        edge=edge,
        seconds_remaining=seconds_remaining,
    )


def _find_book(orderbook: Sequence[MarketTokenBook], outcome: str) -> MarketTokenBook | None:
    normalized = outcome.lower()
    for item in orderbook:
        if item.outcome.strip().lower() == normalized:
            return item
    return None


def _seconds_remaining(
    market: CryptoUpDownMarket,
    model_config: ModelConfig,
) -> float | None:
    if model_config.seconds_remaining is not None:
        return model_config.seconds_remaining

    if not market.end_time:
        return None

    end_time = _parse_utc_datetime(market.end_time)
    if end_time is None:
        return None

    now = model_config.now_utc or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (end_time - now.astimezone(UTC)).total_seconds()


def _parse_utc_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _neutral(reason: str) -> FairValueEstimate:
    return FairValueEstimate(
        fair_up_probability=0.5,
        fair_down_probability=0.5,
        reason=reason,
    )


def _deterministic_expired_estimate(start_price: float, current_price: float) -> FairValueEstimate:
    if current_price > start_price:
        return FairValueEstimate(1.0, 0.0, "current price settled above start price")
    if current_price < start_price:
        return FairValueEstimate(0.0, 1.0, "current price settled below start price")
    return _neutral("current price equals start price")


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _clamp_probability(value: float) -> float:
    return min(max(value, 0.5), 1.0)
