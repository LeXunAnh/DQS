"""
src/services/base_symbol_service.py
════════════════════════════════════════════════════════════════════════════
BaseSymbolService — Abstract base for per-symbol batch services.

Eliminates the duplicated run_all / run_maintenance orchestration that
was copy-pasted across IndicatorService, MFService, and SignalService.

Subclass contract
─────────────────
Every concrete service MUST implement:

    _get_latest_date(symbol) -> date | None
        Query the service's own output table for the most recent date
        already stored for this symbol.

    run_one(symbol, from_date) -> int | bool
        Compute and persist results for ONE symbol from from_date onward.
        Return truthy on success, falsy on skip/failure.

Every concrete service MAY override:

    _before_run_all(symbols, from_date)
        Called once before the run_all loop starts — useful for bulk
        prefetching (e.g. MFService loads all sector mappings here).
        Default implementation is a no-op.

    _label  : str
        Human-readable name used in log/progress-bar messages.
        Default: the class name.

Shared orchestration provided by this base
──────────────────────────────────────────
    run_all(market, from_date)
        Loop over all symbols on a market, call run_one per symbol,
        collect ok/fail counts, log summary.

    run_maintenance(market)
        Loop over all symbols, query _get_latest_date per symbol,
        skip symbols already up-to-date, compute from_date = last+1d,
        delegate to run_one.

Return-value convention
───────────────────────
    run_all        → int   total "units" processed (signals, rows, …)
    run_maintenance → int  total "units" written
    run_one        → int | bool  (truthy = success; int counts units)

The base treats any truthy run_one return as success and adds it to
the running total — so both bool-returning (IndicatorService) and
int-returning (SignalService) subclasses work without changes.
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text
from tqdm import tqdm

from src.database.handler import DatabaseHandler

logger = logging.getLogger(__name__)


class BaseSymbolService(ABC):
    """
    Abstract base class for services that process data symbol-by-symbol.

    Concrete subclasses implement _get_latest_date() and run_one();
    this class provides run_all() and run_maintenance() for free.
    """

    # Override in subclass to customise log / tqdm labels
    _label: str = ""

    MIN_HISTORY_DAYS: int = 60
    _WARMUP_BUFFER: int = 30

    _PRICE_COLUMNS: tuple[str, ...] = (
        "trading_date",
        "open_price",
        "highest_price",
        "lowest_price",
        "close_price",
        "close_price_adjusted",
    )

    _EXTRA_WHERE: str = ""  # e.g. "AND close_price > 0 AND close_price_adjusted IS NOT NULL"

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    # ── Subclass identity ─────────────────────────────────────────────────────

    @property
    def label(self) -> str:
        """Display name used in log messages and progress bars."""
        return self._label or type(self).__name__

    # ── Abstract interface ────────────────────────────────────────────────────
    @abstractmethod
    def _get_latest_date(self, symbol: str):
        """
        Return the most recent date already stored in the output table
        for this symbol, or None if no data exists yet.

        Implementations typically execute:
            SELECT MAX(<date_col>) FROM <output_table> WHERE symbol = :sym
        """

    @abstractmethod
    def run_one(self, symbol: str, from_date: Optional[str] = None):
        """
        Compute and persist results for ONE symbol.

        Args:
            symbol    : Ticker string, e.g. 'SSI'
            from_date : 'YYYY-MM-DD' lower bound (inclusive).
                        None → process full history.

        Returns:
            Truthy on success (bool True or int > 0).
            Falsy on skip / failure (bool False, 0, or None).
        """

    # ── Optional hook ─────────────────────────────────────────────────────────
    def _before_run_all(self, symbols: list[str], from_date: Optional[str]) -> None:
        """
        Called once before the run_all loop begins.

        Override to perform bulk prefetching or any one-time setup that
        would be wasteful to repeat per symbol (e.g. loading sector maps).
        Default: no-op.
        """

    def _fetch_prices(self, symbol: str, from_date: Optional[str] = None) -> pd.DataFrame:
        """
        Fetch daily_stock_prices for one symbol with warmup prepended.

        The exact columns fetched and any extra WHERE filters are
        controlled by subclass class-level constants:
            _PRICE_COLUMNS  — columns to SELECT
            _EXTRA_WHERE    — additional WHERE predicates
            MIN_HISTORY_DAYS + _WARMUP_BUFFER — warmup window size

        Always loads extra bars before from_date so that rolling windows
        (MA, EMA, rolling STD) converge before the first saved row.
        Warmup rows are NOT stripped here — the caller's _compute()
        handles that via a from_date slice.

        Args:
            symbol    : ticker, e.g. 'SSI'
            from_date : 'YYYY-MM-DD' — fetch from (warmup before) this date.
                        None → fetch full history.

        Returns:
            DataFrame sorted ascending by trading_date, or empty DataFrame
            on error.
        """
        params: dict = {"symbol": symbol}
        date_clause: str = ""

        if from_date:
            warmup = (
                    datetime.strptime(from_date, "%Y-%m-%d")
                    - timedelta(days=self.MIN_HISTORY_DAYS + self._WARMUP_BUFFER)
            ).strftime("%Y-%m-%d")
            date_clause = "AND trading_date >= :warmup"
            params["warmup"] = warmup

        col_list = ",\n                ".join(self._PRICE_COLUMNS)

        query = text(f"""
            SELECT
                {col_list}
            FROM daily_stock_prices
            WHERE symbol = :symbol
              {self._EXTRA_WHERE}
              {date_clause}
            ORDER BY trading_date ASC
        """)

        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query, conn, params=params)
            df["trading_date"] = pd.to_datetime(df["trading_date"])
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch giá {symbol}: {e}")
            return pd.DataFrame()

    # ── Shared orchestration ──────────────────────────────────────────────────
    def run_all(self, market: str = "HOSE", from_date: Optional[str] = None) -> int:
        """
        Process ALL symbols on a market.

        Args:
            market    : 'HOSE' | 'HNX' | 'UPCOM'
            from_date : 'YYYY-MM-DD' — None = full history.

        Returns:
            Total units processed (sum of run_one return values).
        """
        symbols = self.db.get_all_symbols_except_CQ(
            market=market, only_companies=True
        )
        logger.info(
            f"🚀 {self.label}: {len(symbols)} mã | sàn {market}"
            + (f" | từ {from_date}" if from_date else " | toàn bộ lịch sử")
        )

        # One-time pre-loop hook (bulk prefetch, etc.)
        self._before_run_all(symbols, from_date)

        total = ok = fail = 0
        pbar = tqdm(symbols, desc=f"{self.label} {market}", unit="sym")

        for sym in pbar:
            pbar.set_postfix({"current": sym})
            try:
                result = self.run_one(sym, from_date)
                if result:
                    total += int(result)
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                logger.error(f"❌ {sym}: {e}")
                fail += 1

        logger.info(
            f"✅ {self.label} run_all xong: "
            f"{ok} thành công / {fail} thất bại | {total} units"
        )
        return total

    def run_maintenance(self, market: str = "HOSE") -> int:
        """
        Maintenance mode — only process dates not yet in the output table.
        Safe to run daily after market close.

        For each symbol:
          1. Query _get_latest_date(symbol)
          2. Skip if already up-to-date (last >= today)
          3. Set from_date = last + 1 day  (or None if no history)
          4. Delegate to run_one(symbol, from_date)

        Args:
            market : 'HOSE' | 'HNX' | 'UPCOM'

        Returns:
            Total units written across all updated symbols.
        """
        symbols = self.db.get_all_symbols_except_CQ(
            market=market, only_companies=True
        )
        logger.info(
            f"🔄 {self.label} bảo trì: {len(symbols)} mã | sàn {market}"
        )

        today = datetime.now().date()
        total = ok = skip = fail = 0
        pbar = tqdm(symbols, desc=f"Maintenance {self.label} {market}", unit="sym")

        for sym in pbar:
            pbar.set_postfix({"current": sym})
            try:
                last = self._get_latest_date(sym)

                if last and last >= today:
                    skip += 1
                    continue

                from_date = (
                    (last + timedelta(days=1)).strftime("%Y-%m-%d")
                    if last else None
                )

                result = self.run_one(sym, from_date)
                if result:
                    total += int(result)
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                logger.error(f"❌ {sym}: {e}")
                fail += 1

        logger.info(
            f"✅ {self.label} bảo trì xong: "
            f"{ok} cập nhật / {skip} bỏ qua / {fail} lỗi | {total} units"
        )
        return total