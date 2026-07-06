"""SQLite persistence for simulated paper trades."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path("./data/paper_trades.sqlite")

OPEN = "OPEN"
WON = "WON"
LOST = "LOST"
CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class PaperTrade:
    trade_id: str
    created_at_utc: str
    market_slug: str
    condition_id: str
    token_id: str
    side: str
    outcome: str
    paper_price: float
    paper_size: float
    paper_cost: float
    simulated_latency_ms: int
    fair_probability: float
    edge_before_slippage: float
    edge_after_slippage: float
    status: str
    resolved_at_utc: str | None
    payout: float
    pnl: float
    reason: str


@dataclass(frozen=True)
class SkippedSignalRecord:
    skip_id: str
    created_at_utc: str
    market_slug: str
    condition_id: str
    asset: str
    side: str | None
    outcome: str | None
    token_id: str | None
    reason: str
    fair_probability: float | None
    ask_price: float | None
    ask_size: float | None
    edge: float | None
    seconds_remaining: float | None


@dataclass(frozen=True)
class LedgerSummary:
    total_trades: int
    open_trades: int
    won_trades: int
    lost_trades: int
    cancelled_trades: int
    realized_pnl: float
    open_risk: float
    total_cost: float

    @property
    def win_rate(self) -> float:
        resolved = self.won_trades + self.lost_trades
        if resolved == 0:
            return 0.0
        return self.won_trades / resolved


class SQLiteLedger:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_trades (
                    trade_id TEXT PRIMARY KEY,
                    created_at_utc TEXT NOT NULL,
                    market_slug TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    paper_price REAL NOT NULL,
                    paper_size REAL NOT NULL,
                    paper_cost REAL NOT NULL,
                    simulated_latency_ms INTEGER NOT NULL,
                    fair_probability REAL NOT NULL,
                    edge_before_slippage REAL NOT NULL,
                    edge_after_slippage REAL NOT NULL,
                    status TEXT NOT NULL,
                    resolved_at_utc TEXT,
                    payout REAL NOT NULL,
                    pnl REAL NOT NULL,
                    reason TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_trades_status
                ON paper_trades(status)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_signal_skips (
                    skip_id TEXT PRIMARY KEY,
                    created_at_utc TEXT NOT NULL,
                    market_slug TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    side TEXT,
                    outcome TEXT,
                    token_id TEXT,
                    reason TEXT NOT NULL,
                    fair_probability REAL,
                    ask_price REAL,
                    ask_size REAL,
                    edge REAL,
                    seconds_remaining REAL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_signal_skips_created_at
                ON paper_signal_skips(created_at_utc)
                """
            )

    def record_trade(self, trade: PaperTrade) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_trades (
                    trade_id,
                    created_at_utc,
                    market_slug,
                    condition_id,
                    token_id,
                    side,
                    outcome,
                    paper_price,
                    paper_size,
                    paper_cost,
                    simulated_latency_ms,
                    fair_probability,
                    edge_before_slippage,
                    edge_after_slippage,
                    status,
                    resolved_at_utc,
                    payout,
                    pnl,
                    reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _trade_values(trade),
            )

    def record_signal_skip(self, skip: SkippedSignalRecord) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_signal_skips (
                    skip_id,
                    created_at_utc,
                    market_slug,
                    condition_id,
                    asset,
                    side,
                    outcome,
                    token_id,
                    reason,
                    fair_probability,
                    ask_price,
                    ask_size,
                    edge,
                    seconds_remaining
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _skip_values(skip),
            )

    def list_trades(self, *, status: str | None = None, limit: int | None = None) -> list[PaperTrade]:
        self.initialize()
        query = "SELECT * FROM paper_trades"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at_utc DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_trade_from_row(row) for row in rows]

    def list_signal_skips(self, *, limit: int | None = None) -> list[SkippedSignalRecord]:
        self.initialize()
        query = "SELECT * FROM paper_signal_skips ORDER BY created_at_utc DESC"
        params: list[Any] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_skip_from_row(row) for row in rows]

    def count_signal_skips(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM paper_signal_skips").fetchone()
        return int(row["count"])

    def open_trades(self) -> list[PaperTrade]:
        return self.list_trades(status=OPEN)

    def get_trade(self, trade_id: str) -> PaperTrade | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()
        return _trade_from_row(row) if row is not None else None

    def update_resolution(
        self,
        *,
        trade_id: str,
        status: str,
        resolved_at_utc: str,
        payout: float,
        pnl: float,
        reason: str | None = None,
    ) -> None:
        if status not in {WON, LOST}:
            raise ValueError(f"Resolution status must be {WON} or {LOST}.")

        self.initialize()
        with self._connect() as connection:
            if reason is None:
                connection.execute(
                    """
                    UPDATE paper_trades
                    SET status = ?,
                        resolved_at_utc = ?,
                        payout = ?,
                        pnl = ?
                    WHERE trade_id = ?
                    """,
                    (status, resolved_at_utc, payout, pnl, trade_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE paper_trades
                    SET status = ?,
                        resolved_at_utc = ?,
                        payout = ?,
                        pnl = ?,
                        reason = ?
                    WHERE trade_id = ?
                    """,
                    (status, resolved_at_utc, payout, pnl, reason, trade_id),
                )

    def summarize(self) -> LedgerSummary:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_trades,
                    COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0) AS open_trades,
                    COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0) AS won_trades,
                    COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0) AS lost_trades,
                    COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0) AS cancelled_trades,
                    COALESCE(SUM(CASE WHEN status IN (?, ?) THEN pnl ELSE 0 END), 0) AS realized_pnl,
                    COALESCE(SUM(CASE WHEN status = ? THEN paper_cost ELSE 0 END), 0) AS open_risk,
                    COALESCE(SUM(paper_cost), 0) AS total_cost
                FROM paper_trades
                """,
                (OPEN, WON, LOST, CANCELLED, WON, LOST, OPEN),
            ).fetchone()

        return LedgerSummary(
            total_trades=int(row["total_trades"]),
            open_trades=int(row["open_trades"]),
            won_trades=int(row["won_trades"]),
            lost_trades=int(row["lost_trades"]),
            cancelled_trades=int(row["cancelled_trades"]),
            realized_pnl=float(row["realized_pnl"]),
            open_risk=float(row["open_risk"]),
            total_cost=float(row["total_cost"]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def _trade_values(trade: PaperTrade) -> tuple[Any, ...]:
    return (
        trade.trade_id,
        trade.created_at_utc,
        trade.market_slug,
        trade.condition_id,
        trade.token_id,
        trade.side,
        trade.outcome,
        trade.paper_price,
        trade.paper_size,
        trade.paper_cost,
        trade.simulated_latency_ms,
        trade.fair_probability,
        trade.edge_before_slippage,
        trade.edge_after_slippage,
        trade.status,
        trade.resolved_at_utc,
        trade.payout,
        trade.pnl,
        trade.reason,
    )


def _skip_values(skip: SkippedSignalRecord) -> tuple[Any, ...]:
    return (
        skip.skip_id,
        skip.created_at_utc,
        skip.market_slug,
        skip.condition_id,
        skip.asset,
        skip.side,
        skip.outcome,
        skip.token_id,
        skip.reason,
        skip.fair_probability,
        skip.ask_price,
        skip.ask_size,
        skip.edge,
        skip.seconds_remaining,
    )


def _trade_from_row(row: sqlite3.Row) -> PaperTrade:
    values = {key: row[key] for key in row.keys()}
    return PaperTrade(
        trade_id=str(values["trade_id"]),
        created_at_utc=str(values["created_at_utc"]),
        market_slug=str(values["market_slug"]),
        condition_id=str(values["condition_id"]),
        token_id=str(values["token_id"]),
        side=str(values["side"]),
        outcome=str(values["outcome"]),
        paper_price=float(values["paper_price"]),
        paper_size=float(values["paper_size"]),
        paper_cost=float(values["paper_cost"]),
        simulated_latency_ms=int(values["simulated_latency_ms"]),
        fair_probability=float(values["fair_probability"]),
        edge_before_slippage=float(values["edge_before_slippage"]),
        edge_after_slippage=float(values["edge_after_slippage"]),
        status=str(values["status"]),
        resolved_at_utc=values["resolved_at_utc"],
        payout=float(values["payout"]),
        pnl=float(values["pnl"]),
        reason=str(values["reason"]),
    )


def _skip_from_row(row: sqlite3.Row) -> SkippedSignalRecord:
    values = {key: row[key] for key in row.keys()}
    return SkippedSignalRecord(
        skip_id=str(values["skip_id"]),
        created_at_utc=str(values["created_at_utc"]),
        market_slug=str(values["market_slug"]),
        condition_id=str(values["condition_id"]),
        asset=str(values["asset"]),
        side=_optional_text(values["side"]),
        outcome=_optional_text(values["outcome"]),
        token_id=_optional_text(values["token_id"]),
        reason=str(values["reason"]),
        fair_probability=_optional_float(values["fair_probability"]),
        ask_price=_optional_float(values["ask_price"]),
        ask_size=_optional_float(values["ask_size"]),
        edge=_optional_float(values["edge"]),
        seconds_remaining=_optional_float(values["seconds_remaining"]),
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def save_all(ledger: SQLiteLedger, trades: Iterable[PaperTrade]) -> None:
    for trade in trades:
        ledger.record_trade(trade)
