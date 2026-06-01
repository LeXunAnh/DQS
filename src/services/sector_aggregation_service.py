"""
src/services/sector_aggregation_service.py
════════════════════════════════════════════════════════════════════════════
SectorAggregationService — Sector-Level Money Flow Aggregation

Responsibilities
────────────────
1. Read stock_mf_daily for a date range
2. For each (date, sector_name) group compute:
     A. Liquidity-weighted mean  → institutional concentration metrics
     B. Cross-sectional median   → breadth participation metrics
     C. Breadth participation %  → % stocks meeting threshold conditions
     D. Coverage quality gate    → n_stocks, coverage_pct
3. Upsert results into sector_factor_daily

Public API  (mirrors MFService / IndicatorService pattern)
──────────────────────────────────────────────────────────
    svc = SectorAggregationService(db)
    svc.run_date("2025-05-28")           # single date
    svc.run_range("2025-01-01", "2025-05-28")
    svc.run_maintenance()                # only missing dates (daily cron)
    svc.run_all(from_date="2024-01-01")  # full rebuild

Aggregation Logic
─────────────────
Weighted mean
    w_i        = trading_value_i / Σ trading_value  (per sector per date)
    weighted_X = Σ (X_i × w_i)

    Edge cases handled:
    • All weights zero (no trading)     → NaN
    • Individual NaN indicators         → excluded from sum + weight
    • trading_value NaN / zero          → excluded from weight

Median
    Standard cross-sectional median, NaN-safe.

Breadth metrics (fraction 0.0–1.0)
    breadth_cmf_positive  = #(CMF > 0)   / n_valid_cmf
    breadth_mfi_above_50  = #(MFI > 50)  / n_valid_mfi
    breadth_accel_above_1 = #(accel > 1) / n_valid_accel
    breadth_nff_positive  = #(nff_z > 0) / n_valid_nff_z

Coverage
    n_stocks     = stocks with at least one valid indicator that day
    coverage_pct = n_stocks / total_stocks_in_sector
                   (total from stock_sector_mapping)

Quality gate
    Sectors with n_stocks < MIN_STOCKS_REQUIRED are saved but flagged
    (coverage_pct will be low) — the scoring layer decides whether to
    exclude them.

════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime, date as date_type, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from tqdm import tqdm

from src.database.handler import DatabaseHandler

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# Aggregation helpers  (pure functions — easy to unit-test)
# ══════════════════════════════════════════════════════════════

def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    """
    Liquidity-weighted mean, NaN-safe.
    Excludes rows where value OR weight is NaN / zero.
    """
    mask = values.notna() & weights.notna() & (weights > 0)
    if mask.sum() == 0:
        return np.nan
    w = weights[mask]
    v = values[mask]
    return float((v * w).sum() / w.sum())


def _safe_median(values: pd.Series) -> float:
    """Cross-sectional median, NaN-safe."""
    clean = values.dropna()
    return float(clean.median()) if len(clean) > 0 else np.nan


def _breadth(values: pd.Series, condition) -> float:
    """
    Fraction [0.0, 1.0] of non-NaN values satisfying condition.
    condition: callable, e.g. lambda x: x > 0
    Returns NaN when no valid values.
    """
    clean = values.dropna()
    if len(clean) == 0:
        return np.nan
    return float(condition(clean).sum() / len(clean))


def _aggregate_group(group: pd.DataFrame) -> dict:
    """
    Aggregate one (date, sector_name) group into a single row dict.
    Called via groupby.apply — keeps aggregation logic in one place.
    """
    w = group["trading_value"]   # liquidity weights

    return {
        # ── Weighted (institutional) ────────────────────────
        "weighted_mfi":   _weighted_mean(group["mfi"],        w),
        "weighted_cmf":   _weighted_mean(group["cmf"],        w),
        "weighted_rvol":  _weighted_mean(group["rvol"],       w),
        "weighted_nmf_z": _weighted_mean(group["nmf_zscore"], w),
        "weighted_accel": _weighted_mean(group["nmf_accel"],  w),
        "weighted_nff_z": _weighted_mean(group["nff_zscore"], w),

        # ── Median (breadth) ─────────────────────────────────
        "median_mfi":   _safe_median(group["mfi"]),
        "median_cmf":   _safe_median(group["cmf"]),
        "median_rvol":  _safe_median(group["rvol"]),
        "median_nmf_z": _safe_median(group["nmf_zscore"]),
        "median_accel": _safe_median(group["nmf_accel"]),
        "median_nff_z": _safe_median(group["nff_zscore"]),

        # ── Breadth participation ────────────────────────────
        "breadth_cmf_positive":  _breadth(group["cmf"],       lambda x: x > 0),
        "breadth_mfi_above_50":  _breadth(group["mfi"],       lambda x: x > 50),
        "breadth_accel_above_1": _breadth(group["nmf_accel"], lambda x: x > 1),
        "breadth_nff_positive":  _breadth(group["nff_zscore"],lambda x: x > 0),

        # ── Coverage ─────────────────────────────────────────
        # n_stocks counted here; coverage_pct added after merge with totals
        "n_stocks": int(group["symbol"].nunique()),
    }


# ══════════════════════════════════════════════════════════════
# SectorAggregationService
# ══════════════════════════════════════════════════════════════

class SectorAggregationService:

    # Minimum stocks for a sector row to be considered meaningful
    # (still saved — caller / scoring layer decides to exclude)
    MIN_STOCKS_REQUIRED: int = 3

    # Output columns matching sector_factor_daily schema
    _OUTPUT_COLS = [
        "date", "sector_name",
        "weighted_mfi",  "median_mfi",
        "weighted_cmf",  "median_cmf",
        "weighted_rvol", "median_rvol",
        "weighted_nmf_z","median_nmf_z",
        "weighted_accel","median_accel",
        "weighted_nff_z","median_nff_z",
        "breadth_cmf_positive",
        "breadth_mfi_above_50",
        "breadth_accel_above_1",
        "breadth_nff_positive",
        "n_stocks",
        "coverage_pct",
    ]

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    # ──────────────────────────────────────────────────────────
    # DB Helpers
    # ──────────────────────────────────────────────────────────

    def _fetch_stock_mf(
        self,
        from_date: str,
        to_date: str,
        sector_name: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Fetch stock_mf_daily for a date range.
        Optional sector_name filter for targeted re-runs.
        """
        params: dict = {"from_date": from_date, "to_date": to_date}
        sector_clause = ""
        if sector_name:
            sector_clause = "AND sector_name = :sector_name"
            params["sector_name"] = sector_name

        query = text(f"""
            SELECT
                date, symbol, sector_name,
                mfi, cmf, rvol,
                nmf_zscore, nmf_accel, nff_zscore,
                trading_value
            FROM stock_mf_daily
            WHERE date BETWEEN :from_date AND :to_date
              AND sector_name IS NOT NULL
              {sector_clause}
            ORDER BY date ASC, sector_name, symbol
        """)

        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query, conn, params=params)
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch stock_mf_daily: {e}")
            return pd.DataFrame()

    def _fetch_sector_totals(self) -> dict[str, int]:
        """
        Total stock count per sector from stock_sector_mapping.
        Used to compute coverage_pct.
        Returns: {sector_name: total_count}
        """
        query = text("""
            SELECT sm.sector_name, COUNT(ssm.symbol) AS total
            FROM stock_sector_mapping ssm
            JOIN sector_master sm ON sm.sector_id = ssm.sector_id
            GROUP BY sm.sector_name
        """)
        try:
            with self.db.engine.connect() as conn:
                rows = conn.execute(query).fetchall()
            return {row[0]: int(row[1]) for row in rows}
        except Exception as e:
            logger.error(f"❌ Lỗi fetch sector totals: {e}")
            return {}

    def _get_latest_aggregation_date(self) -> Optional[date_type]:
        """Latest date stored in sector_factor_daily."""
        query = text("SELECT MAX(date) FROM sector_factor_daily")
        try:
            with self.db.engine.connect() as conn:
                result = conn.execute(query).scalar()
            return result
        except Exception as e:
            logger.error(f"❌ Lỗi lấy max date sector_factor_daily: {e}")
            return None

    def _get_available_mf_dates(
        self,
        from_date: str,
        to_date: str,
    ) -> list[date_type]:
        """Distinct dates available in stock_mf_daily for a range."""
        query = text("""
            SELECT DISTINCT date
            FROM stock_mf_daily
            WHERE date BETWEEN :from_date AND :to_date
              AND sector_name IS NOT NULL
            ORDER BY date ASC
        """)
        try:
            with self.db.engine.connect() as conn:
                rows = conn.execute(
                    query, {"from_date": from_date, "to_date": to_date}
                ).fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"❌ Lỗi lấy available dates: {e}")
            return []

    def _save(self, df: pd.DataFrame) -> None:
        """Upsert into sector_factor_daily."""
        if df.empty:
            return
        out = df[self._OUTPUT_COLS].copy()
        # Thin sectors still saved — scoring layer handles MIN_STOCKS gate
        self.db.save_data(out, "sector_factor_daily", ["date", "sector_name"])

    # ──────────────────────────────────────────────────────────
    # Core Aggregation
    # ──────────────────────────────────────────────────────────

    def _aggregate(self, stock_df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate a stock_mf_daily DataFrame into sector_factor_daily rows.

        Steps:
        1. groupby (date, sector_name) → _aggregate_group per group
        2. Attach coverage_pct using sector totals
        3. Return clean output DataFrame

        Input  : stock_mf_daily rows (any date range)
        Output : sector_factor_daily rows (one per date × sector)
        """
        if stock_df.empty:
            return pd.DataFrame()

        # ── Step 1: Group aggregation ────────────────────────
        records = []
        for (dt, sector), grp in stock_df.groupby(
            ["date", "sector_name"], sort=True
        ):
            row = _aggregate_group(grp)
            row["date"]        = dt
            row["sector_name"] = sector
            records.append(row)

        if not records:
            return pd.DataFrame()

        result = pd.DataFrame(records)

        # ── Step 2: Coverage % ──────────────────────────────
        sector_totals = self._fetch_sector_totals()

        def _coverage(row) -> float:
            total = sector_totals.get(row["sector_name"], 0)
            if total == 0:
                return np.nan
            return round(row["n_stocks"] / total, 4)

        result["coverage_pct"] = result.apply(_coverage, axis=1)

        # ── Step 3: Log thin sectors ────────────────────────
        thin = result[result["n_stocks"] < self.MIN_STOCKS_REQUIRED]
        if not thin.empty:
            for _, r in thin.iterrows():
                logger.debug(
                    f"⚠️  Thin sector {r['sector_name']} @ {r['date']}: "
                    f"only {r['n_stocks']} stocks"
                )

        return result[self._OUTPUT_COLS].reset_index(drop=True)

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    def run_date(self, date: str) -> int:
        """
        Aggregate all sectors for a SINGLE date.
        Useful for daily EOD cron or targeted recompute.

        Returns: number of sector rows written.
        """
        stock_df = self._fetch_stock_mf(date, date)
        if stock_df.empty:
            logger.warning(f"⚠️  Không có stock_mf_daily data cho ngày {date}")
            return 0

        result = self._aggregate(stock_df)
        if result.empty:
            return 0

        self._save(result)
        n = len(result)
        logger.info(f"✅ Aggregated {n} sectors @ {date}")
        return n

    def run_range(
        self,
        from_date: str,
        to_date: str,
        batch_days: int = 30,
    ) -> int:
        """
        Aggregate all sectors for a DATE RANGE in batches.

        Batching avoids loading the entire history into memory at once.
        batch_days=30 is a good default — tune up for fast machines.

        Returns: total sector-date rows written.
        """
        # Discover which dates have stock_mf data in the range
        available = self._get_available_mf_dates(from_date, to_date)
        if not available:
            logger.warning(
                f"⚠️  Không có stock_mf_daily data trong "
                f"{from_date} → {to_date}"
            )
            return 0

        logger.info(
            f"🚀 Aggregating {len(available)} ngày "
            f"({from_date} → {to_date}) | batch={batch_days}d"
        )

        total = 0
        pbar  = tqdm(
            range(0, len(available), batch_days),
            desc="Aggregating sectors",
            unit="batch",
        )

        for i in pbar:
            batch_dates = available[i : i + batch_days]
            b_from = str(batch_dates[0])
            b_to   = str(batch_dates[-1])
            pbar.set_postfix({"range": f"{b_from}→{b_to}"})

            stock_df = self._fetch_stock_mf(b_from, b_to)
            if stock_df.empty:
                continue

            result = self._aggregate(stock_df)
            if result.empty:
                continue

            self._save(result)
            total += len(result)

        logger.info(f"✅ Hoàn tất aggregation: {total} sector-date rows")
        return total

    def run_maintenance(self) -> int:
        """
        Maintenance mode — only aggregate dates not yet in sector_factor_daily.
        Safe to run daily after MFService.run_maintenance() completes.

        Returns: total sector-date rows written.
        """
        last = self._get_latest_aggregation_date()
        today = datetime.now().date()

        if last and last >= today:
            logger.info("✅ sector_factor_daily đã cập nhật đến hôm nay")
            return 0

        from_date = (
            (last + timedelta(days=1)).strftime("%Y-%m-%d")
            if last else "2021-01-01"
        )
        to_date = today.strftime("%Y-%m-%d")

        logger.info(
            f"🔄 Bảo trì aggregation: {from_date} → {to_date}"
        )
        return self.run_range(from_date, to_date)

    def run_all(self, from_date: str = "2021-01-01") -> int:
        """
        Full rebuild of sector_factor_daily from from_date to today.
        Use for initial population or after re-running MFService.run_all().

        Returns: total sector-date rows written.
        """
        to_date = datetime.now().date().strftime("%Y-%m-%d")
        logger.info(f"🚀 Full rebuild sector_factor_daily: {from_date} → {to_date}")
        return self.run_range(from_date, to_date, batch_days=30)


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
    svc = SectorAggregationService(db)

    while True:
        print("\n" + "=" * 55)
        print("  SECTOR AGGREGATION SERVICE")
        print("=" * 55)
        print("  1. Aggregate 1 ngày cụ thể")
        print("  2. Aggregate theo khoảng ngày")
        print("  3. Bảo trì (chỉ ngày còn thiếu)")
        print("  4. Full rebuild từ ngày cụ thể")
        print("  0. Thoát")

        choice = input("\nLựa chọn: ").strip()

        if choice == "1":
            d = input("Ngày (YYYY-MM-DD): ").strip()
            n = svc.run_date(d)
            print(f"→ {n} sector rows written")

        elif choice == "2":
            f = input("Từ ngày (YYYY-MM-DD): ").strip()
            t = input("Đến ngày (YYYY-MM-DD): ").strip()
            n = svc.run_range(f, t)
            print(f"→ {n} sector-date rows written")

        elif choice == "3":
            n = svc.run_maintenance()
            print(f"→ {n} sector-date rows written")

        elif choice == "4":
            f = input("Từ ngày (YYYY-MM-DD, mặc định 2021-01-01): ").strip() or "2021-01-01"
            n = svc.run_all(from_date=f)
            print(f"→ {n} sector-date rows written")

        elif choice == "0":
            print("Thoát.")
            break
        else:
            print("Nhập 0-4.")
