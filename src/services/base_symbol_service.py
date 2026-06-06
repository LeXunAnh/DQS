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