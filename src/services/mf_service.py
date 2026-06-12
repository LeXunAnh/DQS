"""
src/services/mf_service.py
════════════════════════════════════════════════════════════════════════════
MFService — Stock-level Money Flow Indicator Service

Responsibilities
────────────────
1. Fetch adjusted price data from daily_stock_prices
2. Apply price adjustment (reuses IndicatorService._adjust_prices pattern)
3. Compute all 7 MF indicators via mf_indicators.calc_mf_indicators()
4. Attach sector_name + trading_value for aggregation
5. Upsert results into stock_mf_daily

Public API  (mirrors IndicatorService pattern)
──────────────────────────────────────────────
    svc = MFService(db)
    svc.run_one("SSI")
    svc.run_all("HOSE")
    svc.run_maintenance("HOSE")     # daily cron — only missing dates
    svc.run_single_date("SSI", "2025-01-10")

Design Notes
────────────
• _adjust_prices() is copied from IndicatorService — same logic, kept local
  so this service has no cross-dependency with indicator_service.py.
• Warmup: fetches MIN_HISTORY_DAYS before from_date so z-scores / EMAs
  converge correctly; slices them off before saving.
• sector_name denormalized at write time from stock_sector_mapping JOIN.
• trading_value = total_match_val stored alongside for aggregation weights.
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from src.database.handler import DatabaseHandler
from src.indicators.mf_indicators import calc_mf_indicators
from src.utils.price_utils import adjust_prices
from src.services.base_symbol_service import BaseSymbolService

logger = logging.getLogger(__name__)


class MFService(BaseSymbolService):
    """
    Compute and persist stock-level money flow indicators.

    Usage
    ─────
        db  = DatabaseHandler()
        svc = MFService(db)

        svc.run_one("SSI")                     # one symbol, full history
        svc.run_one("SSI", from_date="2024-01-01")
        svc.run_all("HOSE")                    # all 3-char symbols on HOSE
        svc.run_maintenance("HOSE")            # only missing dates (cron)
    """

    # Warmup: enough bars for MA20, EMA20, rolling STD20 to converge
    _label = "MF Indicators"

    # Warmup: enough bars for MA20, EMA20, rolling STD20 to converge
    MIN_HISTORY_DAYS = 60
    _WARMUP_BUFFER = 40  # larger buffer than IndicatorService — MF uses more rolling ops

    _PRICE_COLUMNS = (
        "trading_date",
        "open_price",
        "highest_price",
        "lowest_price",
        "close_price",
        "close_price_adjusted",
        "total_match_vol",
        "total_match_val",
        "foreign_buy_vol_total",
        "foreign_sell_vol_total",
    )
    # Pre-filter bad rows at DB level — avoids fetching rows that would be
    # dropped later and prevents division-by-zero in adjust_prices()
    _EXTRA_WHERE = "AND close_price > 0 AND close_price_adjusted IS NOT NULL"

    # DB output columns (must match stock_mf_daily schema)
    _OUTPUT_COLS = [
        "date", "symbol", "sector_name",
        "mfi", "cmf", "rvol",
        "nmf", "nmf_zscore", "nmf_accel", "nff_zscore",
        "trading_value",
    ]

    def _fetch_sector(self, symbol: str) -> Optional[str]:
        """Return sector_name for a symbol via stock_sector_mapping JOIN."""
        query = text("""
            SELECT sm.sector_name
            FROM stock_sector_mapping ssm
            JOIN sector_master sm ON sm.sector_id = ssm.sector_id
            WHERE ssm.symbol = :symbol
        """)
        try:
            with self.db.engine.connect() as conn:
                result = conn.execute(query, {"symbol": symbol}).scalar()
            return result
        except Exception as e:
            logger.error(f"❌ Lỗi fetch sector {symbol}: {e}")
            return None

    def _fetch_sector_bulk(self, symbols: list[str]) -> dict[str, str]:
        """
        Fetch sector_name for a list of symbols in one query.
        Returns dict: {symbol: sector_name}
        """
        if not symbols:
            return {}
        query = text("""
            SELECT ssm.symbol, sm.sector_name
            FROM stock_sector_mapping ssm
            JOIN sector_master sm ON sm.sector_id = ssm.sector_id
            WHERE ssm.symbol = ANY(:syms)
        """)
        try:
            with self.db.engine.connect() as conn:
                rows = conn.execute(query, {"syms": symbols}).fetchall()
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.error(f"❌ Lỗi bulk fetch sector: {e}")
            return {}

    def _get_latest_date(self, symbol: str):
        """Latest date already stored in stock_mf_daily for this symbol."""
        query = text(
            "SELECT MAX(date) FROM stock_mf_daily WHERE symbol = :sym"
        )
        try:
            with self.db.engine.connect() as conn:
                return conn.execute(query, {"sym": symbol}).scalar()
        except Exception as e:
            logger.error(f"❌ Lỗi lấy max date stock_mf_daily {symbol}: {e}")
            return None

    def _save(self, df: pd.DataFrame) -> None:
        """Upsert into stock_mf_daily."""
        if df.empty:
            return
        out = df[self._OUTPUT_COLS].copy()
        out = out.dropna(subset=["mfi", "cmf", "rvol"])  # drop pure warmup rows
        if out.empty:
            return
        self.db.save_data(out, "stock_mf_daily", ["date", "symbol"])

    # ──────────────────────────────────────────────────────────
    # Compute Pipeline
    # ──────────────────────────────────────────────────────────
    def _compute(self, symbol: str, raw: pd.DataFrame, sector_name: Optional[str], from_date: Optional[str] = None) -> pd.DataFrame:
        """
        Full pipeline for one symbol:
        1. Adjust prices
        2. Compute all 7 MF indicators
        3. Attach metadata columns (date, symbol, sector_name, trading_value)
        4. Slice to from_date (warmup rows removed)
        """
        df = adjust_prices(raw)
        df = calc_mf_indicators(df)

        # Rename trading_date → date for DB column
        df = df.rename(columns={"trading_date": "date"})

        # Attach metadata
        df["symbol"]        = symbol
        df["sector_name"]   = sector_name
        df["trading_value"] = df["total_match_val"]

        # Slice off warmup rows
        if from_date:
            df = df[df["date"] >= pd.Timestamp(from_date)]

        return df.reset_index(drop=True)

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    def _before_run_all(self, symbols: list[str], from_date) -> None:
        """
        Pre-loop hook called by BaseSymbolService.run_all and run_maintenance.
        Loads all sector mappings in ONE query so run_one avoids N+1 lookups.
        """
        self._sector_map: dict[str, str] = self._fetch_sector_bulk(symbols)

    def run_one(self, symbol: str, from_date: Optional[str] = None) -> int:
        """
        Compute and save MF indicators for ONE symbol.

        Args:
            symbol    : e.g. 'SSI'
            from_date : 'YYYY-MM-DD' — only save from this date.
                        None = full history.
        Returns:
            True if successful, False otherwise.
        """
        raw = self._fetch_prices(symbol, from_date)
        if raw.empty:
            logger.warning(f"⚠️  {symbol}: Không có dữ liệu giá")
            return 0

        if len(raw) < self.MIN_HISTORY_DAYS:
            logger.warning(
                f"⚠️  {symbol}: Chỉ có {len(raw)} bars "
                f"(cần ít nhất {self.MIN_HISTORY_DAYS}), bỏ qua"
            )
            return 0

        # Use pre-fetched map when available (batch runs), else fetch individually
        sector_map = getattr(self, "_sector_map", None)
        sector = sector_map.get(symbol) if sector_map is not None else self._fetch_sector(symbol)

        result = self._compute(symbol, raw, sector, from_date)
        if result.empty:
            return 0

        self._save(result)
        n = len(result)
        logger.info(f"✅ {symbol} [{sector or 'N/A'}]: Ghi {n} rows")
        return n

    def run_single_date(self, symbol: str, date: str) -> Optional[pd.Series]:
        """
        Compute MF indicators for ONE symbol on ONE specific date.
        Useful for debugging and backfilling individual rows.

        Returns:
            pd.Series of indicator values, or None if no data.
        """
        raw = self._fetch_prices(symbol, from_date=date)
        if raw.empty or len(raw) < self.MIN_HISTORY_DAYS:
            logger.warning(f"⚠️  {symbol} @ {date}: không đủ dữ liệu")
            return None

        sector = self._fetch_sector(symbol)
        result = self._compute(symbol, raw, sector, from_date=None)

        row = result[result["date"] == pd.Timestamp(date)]
        if row.empty:
            logger.warning(f"⚠️  {symbol} @ {date}: không tìm thấy ngày trong kết quả")
            return None

        return row.iloc[0]


# ══════════════════════════════════════════════════════════════
# CLI Entry Point
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    db  = DatabaseHandler()
    svc = MFService(db)

    while True:
        print("\n" + "=" * 55)
        print("  MF SERVICE — MONEY FLOW INDICATORS")
        print("=" * 55)
        print("  1. Tính 1 mã (toàn bộ lịch sử)")
        print("  2. Tính 1 mã từ ngày cụ thể")
        print("  3. Tính toàn sàn")
        print("  4. Bảo trì (chỉ ngày còn thiếu)")
        print("  5. Kiểm tra 1 ngày cụ thể (debug)")
        print("  0. Thoát")

        choice = input("\nLựa chọn: ").strip()

        if choice == "1":
            sym = input("Mã (vd SSI): ").strip().upper()
            svc.run_one(sym)

        elif choice == "2":
            sym  = input("Mã: ").strip().upper()
            date = input("Từ ngày (YYYY-MM-DD): ").strip()
            svc.run_one(sym, from_date=date)

        elif choice == "3":
            market = input("Sàn (HOSE/HNX/UPCOM, mặc định HOSE): ").strip() or "HOSE"
            date   = input("Từ ngày YYYY-MM-DD (Enter = toàn bộ): ").strip() or None
            svc.run_all(market, from_date=date)

        elif choice == "4":
            market = input("Sàn (mặc định HOSE): ").strip() or "HOSE"
            svc.run_maintenance(market)

        elif choice == "5":
            sym  = input("Mã: ").strip().upper()
            date = input("Ngày (YYYY-MM-DD): ").strip()
            row  = svc.run_single_date(sym, date)
            if row is not None:
                print(f"\n── MF Indicators {sym} @ {date} ──")
                cols = ["mfi", "cmf", "rvol", "nmf", "nmf_zscore", "nmf_accel", "nff_zscore"]
                for c in cols:
                    print(f"  {c:15s}: {row.get(c)}")
            else:
                print("Không có dữ liệu.")

        elif choice == "0":
            print("Thoát.")
            break
        else:
            print("Nhập 0-5.")
