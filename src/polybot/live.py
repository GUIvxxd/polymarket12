"""Live paper-trading loop orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from polybot import clob, discovery
from polybot.gamma import GammaClient
from polybot.ledger import LedgerSummary, PaperTrade, SQLiteLedger, SkippedSignalRecord
from polybot.model import ModelConfig, Signal, SignalRejection, evaluate_signal
from polybot.paper_trader import PaperTrader, PaperTraderConfig
from polybot.price_feed import PriceFeed, PricePoint, build_default_price_feed, timestamp_utc
from polybot.resolver import PaperTradeResolver, ResolverRunSummary


SUPPORTED_LOOP_SYMBOLS = ("BTC", "ETH", "SOL", "XRP")


@dataclass(frozen=True)
class LivePaperConfig:
    symbols: tuple[str, ...] = ("BTC", "ETH")
    db_path: Path = Path("./data/paper_trades.sqlite")
    stake: float = 10.0
    min_edge: float = 0.08
    min_edge_after_slippage: float = 0.05
    slippage_cents: float = 0.02
    latency_ms: int = 1500
    min_liquidity: float = 5.0
    min_seconds_remaining: float = 0.0
    max_seconds_remaining: float = 900.0
    min_bps_distance: float = 5.0
    leading_probability: float = 0.90
    market_limit: int = 10
    interval_seconds: float = 3.0
    iterations: int | None = None


@dataclass(frozen=True)
class SkippedSignal:
    market_slug: str
    side: str | None
    outcome: str | None
    reason: str
    edge: float | None
    seconds_remaining: float | None


@dataclass(frozen=True)
class LoopIterationResult:
    markets_checked: int
    books_checked: int
    prices: list[PricePoint]
    signals: list[Signal]
    trades: list[PaperTrade]
    skipped_signals: list[SkippedSignal]
    ledger_summary: LedgerSummary
    resolver_summary: ResolverRunSummary
    messages: list[str]


class MarketDiscovery(Protocol):
    def __call__(
        self,
        client: GammaClient,
        *,
        limit: int,
        include_closed: bool,
        queries: Sequence[str],
    ) -> list[discovery.CryptoUpDownMarket]:
        """Discover markets for one symbol."""


class ReferencePriceReader(Protocol):
    def __call__(self, market: discovery.CryptoUpDownMarket) -> float | None:
        """Return the market reference price to beat."""


class TradeResolver(Protocol):
    def resolve_open_trades(self) -> ResolverRunSummary:
        """Resolve local OPEN paper trades."""


class LivePaperRunner:
    def __init__(
        self,
        config: LivePaperConfig,
        *,
        gamma_client: GammaClient | None = None,
        clob_client: clob.CLOBClient | None = None,
        price_feed: PriceFeed | None = None,
        ledger: SQLiteLedger | None = None,
        paper_trader: PaperTrader | None = None,
        trade_resolver: TradeResolver | None = None,
        market_discovery: MarketDiscovery = discovery.discover_crypto_up_down_markets,
        reference_price_reader: ReferencePriceReader | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.gamma_client = gamma_client or GammaClient()
        self.clob_client = clob_client or clob.CLOBClient()
        self.price_feed = price_feed or build_default_price_feed()
        self.ledger = ledger or SQLiteLedger(config.db_path)
        self.paper_trader = paper_trader or PaperTrader(
            self.ledger,
            PaperTraderConfig(
                stake=config.stake,
                slippage_cents=config.slippage_cents,
                min_edge_after_slippage=config.min_edge_after_slippage,
                simulated_latency_ms=config.latency_ms,
            ),
        )
        self.trade_resolver = trade_resolver or PaperTradeResolver(self.ledger, self.gamma_client)
        self.market_discovery = market_discovery
        self.reference_price_reader = reference_price_reader or self._read_reference_price
        self.sleep = sleep

    def run(self, on_iteration: Callable[[LoopIterationResult], None] | None = None) -> None:
        completed = 0
        while self.config.iterations is None or completed < self.config.iterations:
            result = self.run_once()
            if on_iteration is not None:
                on_iteration(result)
            completed += 1
            if self.config.iterations is not None and completed >= self.config.iterations:
                break
            self.sleep(self.config.interval_seconds)

    def run_once(self) -> LoopIterationResult:
        messages: list[str] = []
        prices_by_symbol: dict[str, PricePoint] = {}
        signals: list[Signal] = []
        trades: list[PaperTrade] = []
        skipped_signals: list[SkippedSignal] = []
        markets_checked = 0
        books_checked = 0

        markets = self._discover_markets(messages)
        for market in markets:
            markets_checked += 1
            reference_price = self.reference_price_reader(market)
            if reference_price is None:
                messages.append(f"{market.slug}: missing reference price")
                continue

            price_point = prices_by_symbol.get(market.asset)
            if price_point is None:
                try:
                    price_point = self.price_feed.get_price(market.asset)
                except Exception as exc:
                    messages.append(f"{market.asset}: price unavailable: {exc}")
                    continue
                prices_by_symbol[market.asset] = price_point

            try:
                market_books = clob.fetch_market_order_books(market, self.clob_client)
            except Exception as exc:
                messages.append(f"{market.slug}: book unavailable: {exc}")
                continue

            books_checked += len(market_books)
            model_config = ModelConfig(
                start_price=reference_price,
                min_edge=self.config.min_edge,
                min_liquidity=self.config.min_liquidity,
                suggested_stake=self.config.stake,
                min_seconds_remaining=self.config.min_seconds_remaining,
                max_seconds_remaining=self.config.max_seconds_remaining,
                min_bps_distance=self.config.min_bps_distance,
                leading_probability=self.config.leading_probability,
            )
            evaluation = evaluate_signal(
                market,
                market_books,
                price_point,
                model_config,
            )
            for rejection in evaluation.rejections:
                skipped = self._record_signal_rejection(market, rejection)
                skipped_signals.append(skipped)
                messages.append(_skip_message(skipped))

            signal = evaluation.signal
            if signal is None:
                continue

            signals.append(signal)
            trade_result = self.paper_trader.record_signal(signal)
            if trade_result.created and trade_result.trade is not None:
                trades.append(trade_result.trade)
            else:
                skipped = self._record_trade_skip(
                    market,
                    signal,
                    trade_result.skipped_reason or "trade skipped",
                )
                skipped_signals.append(skipped)
                messages.append(_skip_message(skipped))

        resolver_summary = self.trade_resolver.resolve_open_trades()
        ledger_summary = self.ledger.summarize()
        return LoopIterationResult(
            markets_checked=markets_checked,
            books_checked=books_checked,
            prices=list(prices_by_symbol.values()),
            signals=signals,
            trades=trades,
            skipped_signals=skipped_signals,
            ledger_summary=ledger_summary,
            resolver_summary=resolver_summary,
            messages=messages,
        )

    def _discover_markets(self, messages: list[str]) -> list[discovery.CryptoUpDownMarket]:
        markets: list[discovery.CryptoUpDownMarket] = []
        for symbol in self.config.symbols:
            try:
                markets.extend(
                    self.market_discovery(
                        self.gamma_client,
                        limit=self.config.market_limit,
                        include_closed=False,
                        queries=queries_for_symbol(symbol),
                    )
                )
            except Exception as exc:
                messages.append(f"{symbol}: discovery unavailable: {exc}")
        return markets

    def _read_reference_price(self, market: discovery.CryptoUpDownMarket) -> float | None:
        payload = self.gamma_client.fetch_market_by_slug(market.slug)
        if payload is None:
            return None
        return extract_reference_price(payload)

    def _record_signal_rejection(
        self,
        market: discovery.CryptoUpDownMarket,
        rejection: SignalRejection,
    ) -> SkippedSignal:
        record = SkippedSignalRecord(
            skip_id=_skip_id(),
            created_at_utc=timestamp_utc(_utc_now()),
            market_slug=rejection.market_slug,
            condition_id=rejection.condition_id,
            asset=market.asset,
            side=rejection.side,
            outcome=rejection.outcome,
            token_id=rejection.token_id,
            reason=rejection.reason,
            fair_probability=rejection.fair_probability,
            ask_price=rejection.ask_price,
            ask_size=rejection.ask_size,
            edge=rejection.edge,
            seconds_remaining=rejection.seconds_remaining,
        )
        self.ledger.record_signal_skip(record)
        return _skipped_from_record(record)

    def _record_trade_skip(
        self,
        market: discovery.CryptoUpDownMarket,
        signal: Signal,
        reason: str,
    ) -> SkippedSignal:
        record = SkippedSignalRecord(
            skip_id=_skip_id(),
            created_at_utc=timestamp_utc(_utc_now()),
            market_slug=signal.market_slug,
            condition_id=signal.condition_id,
            asset=market.asset,
            side=signal.side,
            outcome=signal.outcome,
            token_id=signal.token_id,
            reason=reason,
            fair_probability=signal.fair_probability,
            ask_price=signal.ask_price,
            ask_size=signal.ask_size,
            edge=signal.edge,
            seconds_remaining=signal.seconds_remaining,
        )
        self.ledger.record_signal_skip(record)
        return _skipped_from_record(record)


def queries_for_symbol(symbol: str) -> tuple[str, str]:
    normalized = normalize_loop_symbol(symbol)
    display_names = {
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "SOL": "Solana",
        "XRP": "XRP",
    }
    return (f"{display_names[normalized]} Up or Down", f"{normalized.lower()}-updown")


def normalize_loop_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    return tuple(normalize_loop_symbol(symbol) for symbol in symbols)


def normalize_loop_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized not in SUPPORTED_LOOP_SYMBOLS:
        raise ValueError(f"Unsupported symbol {symbol!r}. Expected one of {', '.join(SUPPORTED_LOOP_SYMBOLS)}.")
    return normalized


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _skip_id() -> str:
    return uuid4().hex


def _skipped_from_record(record: SkippedSignalRecord) -> SkippedSignal:
    return SkippedSignal(
        market_slug=record.market_slug,
        side=record.side,
        outcome=record.outcome,
        reason=record.reason,
        edge=record.edge,
        seconds_remaining=record.seconds_remaining,
    )


def _skip_message(skip: SkippedSignal) -> str:
    label = "market"
    if skip.outcome:
        label = skip.outcome
    if skip.side:
        label = f"{skip.side} {label}"
    return f"{skip.market_slug}: {label} skipped: {skip.reason}"


def extract_reference_price(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None

    direct = _metadata_price(payload)
    if direct is not None:
        return direct

    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            price = _metadata_price(event)
            if price is not None:
                return price

    return None


def _metadata_price(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("eventMetadata")
    if not isinstance(metadata, dict):
        return None
    return _to_float(metadata.get("priceToBeat"))


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
