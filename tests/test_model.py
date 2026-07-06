from __future__ import annotations

import pytest

from polybot.clob import BookLevel, MarketTokenBook, TokenOrderBook
from polybot.discovery import CryptoUpDownMarket
from polybot.model import (
    BUY_DOWN,
    BUY_UP,
    ModelConfig,
    NearExpiryThresholdModel,
    VolatilityProbabilityModel,
    decide_signal,
    evaluate_signal,
)
from polybot.price_feed import PricePoint


def make_market(*, active: bool = True, closed: bool = False) -> CryptoUpDownMarket:
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
        start_time=None,
        end_time="2026-07-06T12:05:00Z",
        active=active,
        closed=closed,
        resolution_source="https://data.chain.link/streams/btc-usd",
    )


def make_books(
    *,
    up_ask: float = 0.70,
    up_ask_size: float = 50.0,
    down_ask: float = 0.40,
    down_ask_size: float = 50.0,
) -> list[MarketTokenBook]:
    market = make_market()
    return [
        MarketTokenBook(
            market=market,
            outcome="Up",
            token_id="up-token",
            book=TokenOrderBook(
                token_id="up-token",
                market="0xcondition",
                bids=[BookLevel(price=0.69, size=10.0)],
                asks=[BookLevel(price=up_ask, size=up_ask_size)],
            ),
        ),
        MarketTokenBook(
            market=market,
            outcome="Down",
            token_id="down-token",
            book=TokenOrderBook(
                token_id="down-token",
                market="0xcondition",
                bids=[BookLevel(price=0.39, size=10.0)],
                asks=[BookLevel(price=down_ask, size=down_ask_size)],
            ),
        ),
    ]


def make_price(price: float = 101.0) -> PricePoint:
    return PricePoint(
        symbol="BTC",
        price=price,
        timestamp_utc="2026-07-06T12:04:00Z",
        source="binance",
    )


def test_near_expiry_model_marks_up_as_likely_when_price_is_above_start() -> None:
    estimate = NearExpiryThresholdModel(
        min_seconds_remaining=1,
        max_seconds_remaining=120,
        min_bps_distance=5,
    ).estimate(start_price=100.0, current_price=101.0, seconds_remaining=60)

    assert estimate.fair_up_probability == 0.90
    assert estimate.fair_down_probability == pytest.approx(0.10)
    assert "above start price" in estimate.reason


def test_near_expiry_model_marks_down_as_likely_when_price_is_below_start() -> None:
    estimate = NearExpiryThresholdModel(
        min_seconds_remaining=1,
        max_seconds_remaining=120,
        min_bps_distance=5,
    ).estimate(start_price=100.0, current_price=99.0, seconds_remaining=60)

    assert estimate.fair_up_probability == pytest.approx(0.10)
    assert estimate.fair_down_probability == 0.90
    assert "below start price" in estimate.reason


def test_near_expiry_model_is_neutral_when_distance_is_too_small() -> None:
    estimate = NearExpiryThresholdModel(
        min_seconds_remaining=1,
        max_seconds_remaining=120,
        min_bps_distance=20,
    ).estimate(start_price=100.0, current_price=100.01, seconds_remaining=60)

    assert estimate.fair_up_probability == 0.5
    assert estimate.fair_down_probability == 0.5


def test_volatility_probability_model_returns_probability_above_half_for_up_move() -> None:
    estimate = VolatilityProbabilityModel(short_term_volatility=0.001).estimate(
        start_price=100.0,
        current_price=101.0,
        seconds_remaining=60,
    )

    assert 0.5 < estimate.fair_up_probability < 1.0
    assert estimate.fair_down_probability == pytest.approx(1.0 - estimate.fair_up_probability)
    assert "z-score" in estimate.reason


def test_decide_signal_creates_buy_up_when_fair_probability_beats_ask() -> None:
    signal = decide_signal(
        make_market(),
        make_books(up_ask=0.70, up_ask_size=50.0),
        make_price(101.0),
        ModelConfig(
            start_price=100.0,
            min_edge=0.08,
            min_liquidity=5.0,
            suggested_stake=10.0,
            min_bps_distance=5.0,
            seconds_remaining=60.0,
        ),
    )

    assert signal is not None
    assert signal.side == BUY_UP
    assert signal.outcome == "Up"
    assert signal.fair_probability == 0.90
    assert signal.ask_price == 0.70
    assert signal.edge == pytest.approx(0.20)
    assert signal.suggested_stake == 10.0


def test_decide_signal_creates_buy_down_when_down_side_has_edge() -> None:
    signal = decide_signal(
        make_market(),
        make_books(up_ask=0.95, down_ask=0.70, down_ask_size=50.0),
        make_price(99.0),
        ModelConfig(
            start_price=100.0,
            min_edge=0.08,
            min_liquidity=5.0,
            min_bps_distance=5.0,
            seconds_remaining=60.0,
        ),
    )

    assert signal is not None
    assert signal.side == BUY_DOWN
    assert signal.outcome == "Down"
    assert signal.edge == pytest.approx(0.20)


def test_decide_signal_returns_none_when_edge_is_below_threshold() -> None:
    signal = decide_signal(
        make_market(),
        make_books(up_ask=0.85, up_ask_size=50.0),
        make_price(101.0),
        ModelConfig(
            start_price=100.0,
            min_edge=0.08,
            min_liquidity=5.0,
            min_bps_distance=5.0,
            seconds_remaining=60.0,
        ),
    )

    assert signal is None


def test_decide_signal_returns_none_when_liquidity_is_too_low() -> None:
    signal = decide_signal(
        make_market(),
        make_books(up_ask=0.70, up_ask_size=2.0),
        make_price(101.0),
        ModelConfig(
            start_price=100.0,
            min_edge=0.08,
            min_liquidity=5.0,
            min_bps_distance=5.0,
            seconds_remaining=60.0,
        ),
    )

    assert signal is None


def test_decide_signal_returns_none_when_market_is_closed() -> None:
    signal = decide_signal(
        make_market(closed=True),
        make_books(up_ask=0.70, up_ask_size=50.0),
        make_price(101.0),
        ModelConfig(
            start_price=100.0,
            min_edge=0.08,
            min_liquidity=5.0,
            min_bps_distance=5.0,
            seconds_remaining=60.0,
        ),
    )

    assert signal is None


def test_evaluate_signal_reports_edge_and_liquidity_rejections() -> None:
    evaluation = evaluate_signal(
        make_market(),
        make_books(up_ask=0.70, up_ask_size=2.0, down_ask=0.99),
        make_price(101.0),
        ModelConfig(
            start_price=100.0,
            min_edge=0.08,
            min_liquidity=5.0,
            min_bps_distance=5.0,
            seconds_remaining=60.0,
        ),
    )

    reasons = [rejection.reason for rejection in evaluation.rejections]

    assert evaluation.signal is None
    assert any("ask size" in reason for reason in reasons)
    assert any("edge" in reason for reason in reasons)
    assert {rejection.side for rejection in evaluation.rejections} == {BUY_UP, BUY_DOWN}


def test_evaluate_signal_reports_market_level_rejection() -> None:
    evaluation = evaluate_signal(
        make_market(active=False),
        make_books(),
        make_price(101.0),
        ModelConfig(
            start_price=100.0,
            min_edge=0.08,
            min_liquidity=5.0,
            seconds_remaining=60.0,
        ),
    )

    assert evaluation.signal is None
    assert len(evaluation.rejections) == 1
    assert evaluation.rejections[0].reason == "market inactive"
