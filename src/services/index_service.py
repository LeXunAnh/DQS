"""
src/services/index_service.py
════════════════════════════════════════════════════════════════════════════
IndexService — Query & aggregate data from daily_index + index_list tables

Public API
──────────
    svc = IndexService(db)
    svc.get_index_list(market)           → list of index_code strings
    svc.get_ohlcv(index_code, n_days)    → DataFrame with OHLCV columns
    svc.get_all_indices_ohlcv(market, n) → dict {index_code: DataFrame}
    svc.get_index_metadata(market)       → DataFrame with index_list rows

Column contract for get_ohlcv output
──────────────────────────────────────
    trading_date  DATE
    open          NUMERIC   — synthetic: prior day close (see note)
    high          NUMERIC   — approximated from change + vol distribution
    low           NUMERIC   — approximated
    close         NUMERIC   — index_value
    volume        BIGINT    — total_match_vol
    change        NUMERIC   — absolute change
    ratio_change  NUMERIC   — % change
    advances      INT
    declines      INT
    no_changes    INT
    ceilings      INT
    floors        INT

Note on OHLC approximation
───────────────────────────
Index tables in Vietnamese markets (SSI data feed) typically only provide
closing index_value, change, and breadth stats — not intraday OHLC.

We reconstruct a synthetic candlestick as follows:
    open  = prior_close  (previous day's index_value)
    close = index_value  (current day's closing value)
    high  = max(open, close) + atr_estimate * 0.3
    low   = min(open, close) - atr_estimate * 0.3
    atr_estimate = rolling 14-day mean of |change|

This gives visually meaningful candlesticks that reflect daily direction
and approximate intraday range based on recent volatility.
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.database.handler import DatabaseHandler

logger = logging.getLogger(__name__)

_EXCLUDED_CODES: frozenset[str] = frozenset({
    "VNSMALLCAP", "VNXALLSHARE", "VN50 GROWTH", "VNALLSHARE",
    "VNDIVIDEND", "VNMIDCAP", "VNMITECH", "VNSHINE",
})

class IndexService:
    """
    Query and process market index data from the database.

    Usage
    ─────
        db  = DatabaseHandler()
        svc = IndexService(db)

        meta = svc.get_index_metadata("HOSE")
        df   = svc.get_ohlcv("VNINDEX", n_days=365)
        all_ = svc.get_all_indices_ohlcv("HOSE", n_days=180)
    """

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    # ──────────────────────────────────────────────────────────
    # 1. Index Metadata
    # ──────────────────────────────────────────────────────────

    def get_index_metadata(self, market: Optional[str] = None) -> pd.DataFrame:
        """
        Return index_list rows (index_code, index_name, exchange).

        Args:
            market : 'HOSE' | 'HNX' | 'UPCOM' | None (all markets)
        """
        params: dict = {}
        clauses = [
            f"index_code NOT IN ({','.join([f':ex{i}' for i in range(len(_EXCLUDED_CODES))])})"
        ]
        for i, code in enumerate(_EXCLUDED_CODES):
            params[f"ex{i}"] = code

        if market:
            clauses.append("exchange = :market")
            params["market"] = market

        where = "WHERE " + " AND ".join(clauses)

        query = text(f"""
                    SELECT index_code, index_name, exchange
                    FROM index_list
                    {where}
                    ORDER BY exchange, index_code
                """)
        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query, conn, params=params)
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi get_index_metadata: {e}")
            return pd.DataFrame(columns=["index_code", "index_name", "exchange"])

    def get_index_list(self, market: Optional[str] = None) -> list[str]:
        """Return list of index_code strings for the given market."""
        df = self.get_index_metadata(market)
        return df["index_code"].tolist()

    # ──────────────────────────────────────────────────────────
    # 2. Raw Data Fetch
    # ──────────────────────────────────────────────────────────

    def _fetch_raw(self, index_code: str, from_date: str, to_date: str) -> pd.DataFrame:
        """
        Fetch raw daily_index rows for one index_code in a date range.
        """
        query = text("""
            SELECT
                trading_date,
                index_value,
                change,
                ratio_change,
                total_match_vol,
                total_match_val,
                total_vol,
                total_val,
                advances,
                no_changes,
                declines,
                ceilings,
                floors
            FROM daily_index
            WHERE index_code  = :code
              AND trading_date BETWEEN :f AND :t
            ORDER BY trading_date ASC
        """)
        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(
                    query, conn,
                    params={"code": index_code, "f": from_date, "t": to_date},
                )
            df["trading_date"] = pd.to_datetime(df["trading_date"])
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch daily_index {index_code}: {e}")
            return pd.DataFrame()

    # ──────────────────────────────────────────────────────────
    # 3. Synthetic OHLCV Construction
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
        """
        Construct synthetic OHLCV from daily_index raw data.

        Approach:
            open  = previous close (shift 1)
            close = index_value
            atr   = rolling 14-day mean of |change|
            high  = max(open, close) + 0.30 * atr
            low   = min(open, close) - 0.30 * atr
        """
        if raw.empty:
            return pd.DataFrame()

        df = raw.copy().reset_index(drop=True)

        # close = index_value (the official EOD level)
        df["close"] = df["index_value"].astype(float)

        # open = prior close (first bar uses same as close → doji)
        df["open"] = df["close"].shift(1).fillna(df["close"])

        # ATR estimate based on rolling absolute change
        abs_change = df["change"].abs()
        atr = abs_change.rolling(14, min_periods=1).mean()

        df["high"] = (
            df[["open", "close"]].max(axis=1) + 0.30 * atr
        ).round(2)

        df["low"] = (
            df[["open", "close"]].min(axis=1) - 0.30 * atr
        ).clip(lower=0)  # index can't go negative
        df["low"] = df["low"].round(2)

        df["open"]  = df["open"].round(2)
        df["close"] = df["close"].round(2)

        df["volume"] = df["total_match_vol"].fillna(0).astype(np.int64)

        # Keep useful columns
        keep = [
            "trading_date", "open", "high", "low", "close", "volume",
            "change", "ratio_change",
            "advances", "no_changes", "declines", "ceilings", "floors",
            "total_match_val",
        ]
        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)

    # ──────────────────────────────────────────────────────────
    # 4. Public: Single Index OHLCV
    # ──────────────────────────────────────────────────────────

    def get_ohlcv(self, index_code: str, n_days: int = 365, from_date: Optional[str] = None, to_date: Optional[str] = None) -> pd.DataFrame:
        """
        Return synthetic OHLCV DataFrame for one index.

        Args:
            index_code : e.g. 'VNINDEX'
            n_days     : lookback in calendar days (used if from_date is None)
            from_date  : 'YYYY-MM-DD' override
            to_date    : 'YYYY-MM-DD' override (defaults to today)
        """
        today = datetime.now().date()
        if to_date is None:
            to_date = today.strftime("%Y-%m-%d")
        if from_date is None:
            from_date = (today - timedelta(days=n_days)).strftime("%Y-%m-%d")

        raw = self._fetch_raw(index_code, from_date, to_date)
        if raw.empty:
            logger.warning(f"⚠️  Không có dữ liệu cho {index_code}")
            return pd.DataFrame()

        return self._build_ohlcv(raw)

    # ──────────────────────────────────────────────────────────
    # 5. Public: All Indices OHLCV
    # ──────────────────────────────────────────────────────────

    def get_all_indices_ohlcv(self, market: Optional[str] = None, n_days: int = 365) -> dict[str, pd.DataFrame]:
        """
        Return {index_code: ohlcv_df} for all indices in the given market.

        Args:
            market : 'HOSE' | 'HNX' | 'UPCOM' | None (all)
            n_days : lookback calendar days

        Returns:
            Dict keyed by index_code, values are OHLCV DataFrames.
            Indices with no data are excluded.
        """
        codes = self.get_index_list(market)
        if not codes:
            logger.warning(f"⚠️  Không tìm thấy index nào cho market={market}")
            return {}

        result: dict[str, pd.DataFrame] = {}
        for code in codes:
            df = self.get_ohlcv(code, n_days=n_days)
            if not df.empty:
                result[code] = df
            else:
                logger.debug(f"  Bỏ qua {code} — không có dữ liệu")

        logger.info(
            f"✅ Loaded {len(result)}/{len(codes)} indices "
            f"(market={market or 'ALL'}, {n_days}d)"
        )
        return result

    # ──────────────────────────────────────────────────────────
    # 6. Summary Stats (for dashboard cards)
    # ──────────────────────────────────────────────────────────

    def get_latest_snapshot(self, market: Optional[str] = None) -> pd.DataFrame:
        """
        Return the latest trading day snapshot for all indices:
        index_code, index_name, exchange, close, change, ratio_change,
        advances, declines, ceilings, floors, volume.
        """
        params2: dict = {}
        extra_clauses = [
            f"di.index_code NOT IN ({','.join([f':ex{i}' for i in range(len(_EXCLUDED_CODES))])})"
        ]
        for i, code in enumerate(_EXCLUDED_CODES):
            params2[f"ex{i}"] = code

        if market:
            extra_clauses.append("il.exchange = :market")
            params2["market"] = market

        where_clause = "AND " + " AND ".join(extra_clauses)

        query2 = text(f"""
            WITH latest AS (
                SELECT index_code, MAX(trading_date) AS max_date
                FROM daily_index
                GROUP BY index_code
            )
            SELECT
                di.index_code,
                COALESCE(il.index_name, di.index_code) AS index_name,
                COALESCE(il.exchange, '') AS exchange,
                di.trading_date,
                di.index_value  AS close,
                di.change,
                di.ratio_change,
                di.advances,
                di.no_changes,
                di.declines,
                di.ceilings,
                di.floors,
                di.total_match_vol AS volume
            FROM daily_index di
            JOIN latest l
              ON l.index_code = di.index_code
             AND l.max_date   = di.trading_date
            LEFT JOIN index_list il ON il.index_code = di.index_code
            WHERE 1=1 {where_clause}
            ORDER BY di.index_code
        """)

        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query2, conn, params=params2)
            df["trading_date"] = pd.to_datetime(df["trading_date"])
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi get_latest_snapshot: {e}")
            return pd.DataFrame()