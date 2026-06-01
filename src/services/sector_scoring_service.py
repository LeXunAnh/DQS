"""
src/services/sector_scoring_service.py
════════════════════════════════════════════════════════════════════════════
SectorScoringService — Sector Scoring, Ranking, Regime & Weekly Rollup

Responsibilities
────────────────
1. Read sector_factor_daily
2. Normalize each raw metric onto [-1, +1]
3. Compute inst_score, breadth_score, total_score
4. Rank sectors per date (1 = strongest)
5. Classify regime per (date, sector)
6. Compute score_delta_1d, score_delta_5d
7. Upsert → sector_score_daily
8. Roll up daily scores → sector_rank_weekly

Normalization
─────────────
Raw metrics span incompatible scales:
    MFI          [0, 100]   — centered at 50
    CMF          [-1, +1]   — already normalized
    RVOL         [0, ∞)     — centered at 1.0 (1 = average participation)
    nmf_zscore   unbounded  — clip ±3σ
    nmf_accel    unbounded  — clip ±2
    nff_zscore   unbounded  — clip ±3σ
    breadth      [0, 1]     — centered at 0.5

All are mapped to [-1, +1] before weighting so no single metric
dominates the score by scale alone.

Scoring Weights
───────────────
inst_score   = 0.30×n_cmf  + 0.20×n_mfi  + 0.20×n_rvol
             + 0.15×n_nmf_z + 0.15×n_accel

breadth_score = 0.40×n_med_cmf + 0.30×n_med_mfi
              + 0.30×n_breadth_cmf

total_score   = 0.60×inst_score + 0.40×breadth_score

All scores in [-1, +1].  Positive = net inflow / accumulation.

Regime Rules  (evaluated in priority order)
────────────────────────────────────────────
score  5 — Expansion     : total ≥ 0.45  AND breadth_cmf ≥ 0.60
                           AND w_cmf > 0  AND w_accel ≥ 0.15
score  4 — EarlyRotation : total ≥ 0.15  AND breadth_cmf ≥ 0.45
                           AND delta_1d > 0  (rising)
score  3 — Neutral       : -0.15 < total < 0.45 (catch-all)
score  2 — Distribution  : total ≤ -0.15 AND breadth_cmf < 0.55
                           AND delta_1d < 0  (deteriorating)
score  1 — Contraction   : total ≤ -0.45 AND breadth_cmf ≤ 0.40
                           AND w_cmf < 0

NaN-safe: if total_score is NaN, regime = 'Neutral' (score 3).

Weekly Rollup
─────────────
year_week  = year × 100 + ISO week number  e.g. 202518
WeeklyScore = mean(daily total_score in week)
regime      = modal (most-frequent) daily regime in the week
score_delta_1w = WeeklyScore_this - WeeklyScore_prev_week

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
# Constants
# ══════════════════════════════════════════════════════════════

# Regime label ↔ numeric score mapping (used for sorting / diff)
REGIME_SCORE: dict[str, float] = {
    "Expansion":     5.0,
    "EarlyRotation": 4.0,
    "Neutral":       3.0,
    "Distribution":  2.0,
    "Contraction":   1.0,
}

# Inverse mapping
SCORE_REGIME: dict[float, str] = {v: k for k, v in REGIME_SCORE.items()}

# Scoring weights
INST_WEIGHTS = {
    "weighted_cmf":   0.30,
    "weighted_mfi":   0.20,
    "weighted_rvol":  0.20,
    "weighted_nmf_z": 0.15,
    "weighted_accel": 0.15,
}

BREADTH_WEIGHTS = {
    "median_cmf":            0.40,
    "median_mfi":            0.30,
    "breadth_cmf_positive":  0.30,
}

TOTAL_WEIGHTS = {"inst_score": 0.60, "breadth_score": 0.40}


# ══════════════════════════════════════════════════════════════
# Normalization helpers  (pure functions)
# ══════════════════════════════════════════════════════════════

def _norm_mfi(x: pd.Series) -> pd.Series:
    """MFI [0,100] → [-1, +1], center = 50."""
    return ((x - 50.0) / 50.0).clip(-1.0, 1.0)


def _norm_cmf(x: pd.Series) -> pd.Series:
    """CMF [-1, +1] → [-1, +1], already normalized; clip for safety."""
    return x.clip(-1.0, 1.0)


def _norm_rvol(x: pd.Series) -> pd.Series:
    """RVOL [0,∞) → [-1, +1], center = 1.0 (average participation).
    RVOL of 2.0 (double average) maps to +0.5; 3.0 maps to +1.0 (clipped).
    RVOL of 0   maps to -0.5.
    """
    return ((x - 1.0) / 2.0).clip(-1.0, 1.0)


def _norm_zscore(x: pd.Series, clip_val: float = 3.0) -> pd.Series:
    """Z-score → [-1, +1] by clipping at ±clip_val."""
    return (x / clip_val).clip(-1.0, 1.0)


def _norm_accel(x: pd.Series, clip_val: float = 2.0) -> pd.Series:
    """NMF acceleration → [-1, +1] by clipping at ±clip_val."""
    return (x / clip_val).clip(-1.0, 1.0)


def _norm_breadth(x: pd.Series) -> pd.Series:
    """Breadth [0, 1] → [-1, +1], center = 0.5."""
    return ((x - 0.5) * 2.0).clip(-1.0, 1.0)


# ══════════════════════════════════════════════════════════════
# Score computation  (pure function — operates on full DataFrame)
# ══════════════════════════════════════════════════════════════

def compute_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add normalized component columns + inst/breadth/total scores to df.

    Input  : sector_factor_daily rows (any date range, any sectors)
    Output : same df with added columns:
               n_cmf, n_mfi, n_rvol, n_nmf_z, n_accel, n_nff_z (normalized)
               n_med_cmf, n_med_mfi, n_breadth_cmf
               inst_score, breadth_score, total_score
    All scores in [-1, +1].
    """
    df = df.copy()

    # ── Normalize institutional components ──────────────────
    df["n_cmf"]   = _norm_cmf(df["weighted_cmf"])
    df["n_mfi"]   = _norm_mfi(df["weighted_mfi"])
    df["n_rvol"]  = _norm_rvol(df["weighted_rvol"])
    df["n_nmf_z"] = _norm_zscore(df["weighted_nmf_z"])
    df["n_accel"] = _norm_accel(df["weighted_accel"])

    # ── Normalize breadth components ────────────────────────
    df["n_med_cmf"]     = _norm_cmf(df["median_cmf"])
    df["n_med_mfi"]     = _norm_mfi(df["median_mfi"])
    df["n_breadth_cmf"] = _norm_breadth(df["breadth_cmf_positive"])

    # ── Composite scores ─────────────────────────────────────
    df["inst_score"] = (
        INST_WEIGHTS["weighted_cmf"]   * df["n_cmf"]   +
        INST_WEIGHTS["weighted_mfi"]   * df["n_mfi"]   +
        INST_WEIGHTS["weighted_rvol"]  * df["n_rvol"]  +
        INST_WEIGHTS["weighted_nmf_z"] * df["n_nmf_z"] +
        INST_WEIGHTS["weighted_accel"] * df["n_accel"]
    ).round(4)

    df["breadth_score"] = (
        BREADTH_WEIGHTS["median_cmf"]           * df["n_med_cmf"]     +
        BREADTH_WEIGHTS["median_mfi"]           * df["n_med_mfi"]     +
        BREADTH_WEIGHTS["breadth_cmf_positive"] * df["n_breadth_cmf"]
    ).round(4)

    df["total_score"] = (
        TOTAL_WEIGHTS["inst_score"]    * df["inst_score"]    +
        TOTAL_WEIGHTS["breadth_score"] * df["breadth_score"]
    ).round(4)

    return df


# ══════════════════════════════════════════════════════════════
# Ranking  (pure function — per-date rank)
# ══════════════════════════════════════════════════════════════

def compute_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add 'rank' column: 1 = strongest sector per date.
    Sectors with NaN total_score receive NaN rank.
    Ties broken by inst_score descending.
    """
    df = df.copy()
    df["rank"] = (
        df.groupby("date")["total_score"]
        .rank(method="min", ascending=False, na_option="keep")
        .astype("Int64")
    )
    return df


# ══════════════════════════════════════════════════════════════
# Regime classification  (pure function — row-wise)
# ══════════════════════════════════════════════════════════════

def _classify_regime(row: pd.Series) -> tuple[str, float]:
    """
    Classify a single sector-date row into a regime.
    Returns (regime_label, regime_score).

    Evaluated in strict priority order:
        Expansion → EarlyRotation → Contraction → Distribution → Neutral
    """
    ts   = row.get("total_score")
    bcmf = row.get("breadth_cmf_positive", np.nan)
    wcmf = row.get("weighted_cmf",         np.nan)
    wacc = row.get("n_accel",              np.nan)
    d1d  = row.get("score_delta_1d",       0.0)

    # Guard: NaN total_score → Neutral
    if pd.isna(ts):
        return "Neutral", 3.0

    # ── Expansion ────────────────────────────────────────────
    if (
        ts   >= 0.45
        and (pd.isna(bcmf) or bcmf >= 0.60)
        and (pd.isna(wcmf) or wcmf >  0.0)
        and (pd.isna(wacc) or wacc >= 0.15)
    ):
        return "Expansion", 5.0

    # ── EarlyRotation ────────────────────────────────────────
    if (
        ts   >= 0.15
        and (pd.isna(bcmf) or bcmf >= 0.45)
        and (pd.isna(d1d)  or d1d  >  0.0)
    ):
        return "EarlyRotation", 4.0

    # ── Contraction ──────────────────────────────────────────
    if (
        ts   <= -0.45
        and (pd.isna(bcmf) or bcmf <= 0.40)
        and (pd.isna(wcmf) or wcmf <  0.0)
    ):
        return "Contraction", 1.0

    # ── Distribution ─────────────────────────────────────────
    if (
        ts   <= -0.15
        and (pd.isna(bcmf) or bcmf <  0.55)
        and (pd.isna(d1d)  or d1d  <  0.0)
    ):
        return "Distribution", 2.0

    # ── Neutral (catch-all) ───────────────────────────────────
    return "Neutral", 3.0


def compute_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'regime' and 'regime_score' columns from score + breadth columns."""
    df = df.copy()
    regimes = df.apply(_classify_regime, axis=1)
    df["regime"]       = regimes.apply(lambda t: t[0])
    df["regime_score"] = regimes.apply(lambda t: t[1])
    return df


# ══════════════════════════════════════════════════════════════
# Score deltas  (pure function — requires history)
# ══════════════════════════════════════════════════════════════

def compute_deltas(
    current: pd.DataFrame,
    history: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute score_delta_1d and score_delta_5d.

    current : sector_score rows being computed (new dates)
    history : existing sector_score_daily rows (lookback ≥ 5 trading days)

    Uses trading days as lag units — not calendar days — so weekends
    and holidays don't create false delta spikes.

    Note: dates are normalised to string (YYYY-MM-DD) internally so that
    mix of date / Timestamp / str types from different DB drivers don't
    cause MultiIndex lookup mismatches.
    """
    current = current.copy()

    def _to_str(s: pd.Series) -> pd.Series:
        return pd.to_datetime(s).dt.strftime("%Y-%m-%d")

    # Normalise date columns to plain string for consistent joining
    cur_work = current[["date","sector_name","total_score"]].copy()
    cur_work["date"] = _to_str(cur_work["date"])

    hist_work = pd.DataFrame()
    if not history.empty:
        hist_work = history[["date","sector_name","total_score"]].copy()
        hist_work["date"] = _to_str(hist_work["date"])

    # Combine, deduplicate, sort (sector, date) → stable positional shift
    combined = pd.concat(
        [hist_work, cur_work], ignore_index=True
    ).drop_duplicates(subset=["date","sector_name"]) \
     .sort_values(["sector_name","date"]) \
     .reset_index(drop=True)

    # Positional lag — correct regardless of calendar gaps
    combined["lag1"] = combined.groupby("sector_name")["total_score"].shift(1)
    combined["lag5"] = combined.groupby("sector_name")["total_score"].shift(5)

    # Lookup by (date_str, sector_name)
    lookup = combined.set_index(["date","sector_name"])

    date_strs = _to_str(current["date"])
    current_idx = pd.MultiIndex.from_arrays(
        [date_strs, current["sector_name"]]
    )

    lag1_vals = lookup.reindex(current_idx)["lag1"].values
    lag5_vals = lookup.reindex(current_idx)["lag5"].values

    current["score_delta_1d"] = (current["total_score"] - lag1_vals).round(4)
    current["score_delta_5d"] = (current["total_score"] - lag5_vals).round(4)

    return current


# ══════════════════════════════════════════════════════════════
# Weekly rollup  (pure function)
# ══════════════════════════════════════════════════════════════

def _iso_year_week(dt) -> int:
    """Convert date → YYYYWW integer using ISO week numbering."""
    if isinstance(dt, str):
        dt = pd.Timestamp(dt)
    iso = dt.isocalendar()
    return int(iso.year) * 100 + int(iso.week)


def _modal_regime(regimes: pd.Series) -> str:
    """Most frequent regime in a week; tie → higher regime_score wins."""
    if regimes.empty:
        return "Neutral"
    counts = regimes.value_counts()
    # If tie: prefer the stronger regime (higher score)
    max_count = counts.max()
    candidates = counts[counts == max_count].index.tolist()
    return max(candidates, key=lambda r: REGIME_SCORE.get(r, 3.0))


def compute_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate sector_score_daily → sector_rank_weekly.

    Input  : sector_score_daily rows (one or more weeks)
    Output : sector_rank_weekly rows

    year_week : YYYYWW integer
    date_from : Monday of the ISO week
    date_to   : Friday of the ISO week
    """
    df = daily_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["year_week"] = df["date"].apply(_iso_year_week)

    records = []
    for (yw, sector), grp in df.groupby(["year_week","sector_name"]):
        total_scores = grp["total_score"].dropna()
        if total_scores.empty:
            continue

        week_start = grp["date"].min()
        # ISO Monday / Friday of that week
        date_from = (week_start - timedelta(days=week_start.weekday())).date()
        date_to   = (date_from + timedelta(days=4))

        records.append({
            "year_week":      int(yw),
            "date_from":      date_from,
            "date_to":        date_to,
            "sector_name":    sector,
            "inst_score":     round(grp["inst_score"].mean(),    4),
            "breadth_score":  round(grp["breadth_score"].mean(), 4),
            "total_score":    round(total_scores.mean(),         4),
            "rank":           None,                   # filled after group
            "regime":         _modal_regime(grp["regime"]),
            "regime_score":   float(REGIME_SCORE.get(
                                _modal_regime(grp["regime"]), 3.0)),
            "score_delta_1w": None,                   # filled after merge
            "n_trading_days": int(grp["date"].nunique()),
        })

    if not records:
        return pd.DataFrame()

    weekly = pd.DataFrame(records)

    # ── Weekly rank (per year_week) ──────────────────────────
    weekly["rank"] = (
        weekly.groupby("year_week")["total_score"]
        .rank(method="min", ascending=False, na_option="keep")
        .astype("Int64")
    )

    # ── score_delta_1w (vs same sector previous week) ────────
    weekly = weekly.sort_values(["sector_name","year_week"])
    weekly["score_delta_1w"] = (
        weekly.groupby("sector_name")["total_score"].diff(1).round(4)
    )

    return weekly.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# SectorScoringService
# ══════════════════════════════════════════════════════════════

class SectorScoringService:
    """
    Compute sector scores, rankings, regimes and weekly roll-ups.

    Usage
    ─────
        db  = DatabaseHandler()
        svc = SectorScoringService(db)

        svc.run_date("2025-05-28")
        svc.run_range("2025-01-01", "2025-05-28")
        svc.run_maintenance()          # daily cron
        svc.run_all(from_date="2024-01-01")
    """

    # How many extra history days to load for delta computation
    _DELTA_LOOKBACK_DAYS: int = 14   # calendar days; covers 5 trading days safely

    # Output columns for each table
    _SCORE_COLS = [
        "date", "sector_name",
        "inst_score", "breadth_score", "total_score",
        "rank", "regime", "regime_score",
        "score_delta_1d", "score_delta_5d",
    ]

    _WEEKLY_COLS = [
        "year_week", "date_from", "date_to", "sector_name",
        "inst_score", "breadth_score", "total_score",
        "rank", "regime", "regime_score",
        "score_delta_1w", "n_trading_days",
    ]

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    # ──────────────────────────────────────────────────────────
    # DB Helpers
    # ──────────────────────────────────────────────────────────

    def _fetch_factors(
        self,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """Fetch sector_factor_daily for scoring."""
        query = text("""
            SELECT
                date, sector_name,
                weighted_mfi, weighted_cmf, weighted_rvol,
                weighted_nmf_z, weighted_accel, weighted_nff_z,
                median_mfi, median_cmf, median_rvol,
                median_nmf_z, median_accel, median_nff_z,
                breadth_cmf_positive, breadth_mfi_above_50,
                breadth_accel_above_1, breadth_nff_positive,
                n_stocks, coverage_pct
            FROM sector_factor_daily
            WHERE date BETWEEN :from_date AND :to_date
            ORDER BY date ASC, sector_name
        """)
        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query, conn,
                                 params={"from_date": from_date,
                                         "to_date":   to_date})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch sector_factor_daily: {e}")
            return pd.DataFrame()

    def _fetch_score_history(
        self,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """Fetch existing sector_score_daily rows for delta lookback."""
        query = text("""
            SELECT date, sector_name, total_score
            FROM sector_score_daily
            WHERE date BETWEEN :from_date AND :to_date
            ORDER BY date ASC, sector_name
        """)
        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query, conn,
                                 params={"from_date": from_date,
                                         "to_date":   to_date})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch score history: {e}")
            return pd.DataFrame()

    def _fetch_weekly_history(self, year_weeks: list[int]) -> pd.DataFrame:
        """Fetch sector_rank_weekly rows for prior-week delta."""
        if not year_weeks:
            return pd.DataFrame()
        query = text("""
            SELECT year_week, sector_name, total_score
            FROM sector_rank_weekly
            WHERE year_week = ANY(:yws)
        """)
        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query, conn, params={"yws": year_weeks})
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch weekly history: {e}")
            return pd.DataFrame()

    def _get_latest_score_date(self) -> Optional[date_type]:
        query = text("SELECT MAX(date) FROM sector_score_daily")
        try:
            with self.db.engine.connect() as conn:
                return conn.execute(query).scalar()
        except Exception as e:
            logger.error(f"❌ {e}")
            return None

    def _save_scores(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        out = df[self._SCORE_COLS].copy()
        self.db.save_data(out, "sector_score_daily", ["date", "sector_name"])

    def _save_weekly(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        out = df[self._WEEKLY_COLS].copy()
        self.db.save_data(out, "sector_rank_weekly", ["year_week", "sector_name"])

    # ──────────────────────────────────────────────────────────
    # Core Pipeline
    # ──────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        from_date: str,
        to_date: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Full scoring pipeline for a date range.

        Returns: (sector_score_daily df, sector_rank_weekly df)
        """
        # ── 1. Fetch factors ────────────────────────────────
        factors = self._fetch_factors(from_date, to_date)
        if factors.empty:
            logger.warning(
                f"⚠️  Không có sector_factor_daily data: "
                f"{from_date} → {to_date}"
            )
            return pd.DataFrame(), pd.DataFrame()

        # ── 2. Fetch score history for delta computation ─────
        history_from = (
            datetime.strptime(from_date, "%Y-%m-%d")
            - timedelta(days=self._DELTA_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")

        history = self._fetch_score_history(history_from, from_date)

        # ── 3. Compute scores ────────────────────────────────
        scored = compute_scores(factors)

        # ── 4. Compute score deltas ──────────────────────────
        scored = compute_deltas(scored, history)

        # ── 5. Compute regimes ───────────────────────────────
        scored = compute_regimes(scored)

        # ── 6. Compute per-date rank ─────────────────────────
        scored = compute_ranks(scored)

        # ── 7. Build weekly rollup ───────────────────────────
        weekly = compute_weekly(scored)

        return scored, weekly

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    def run_date(self, date: str) -> int:
        """
        Score all sectors for a SINGLE date.
        Returns: number of sector rows written.
        """
        daily, weekly = self._run_pipeline(date, date)
        self._save_scores(daily)
        self._save_weekly(weekly)

        n = len(daily)
        logger.info(
            f"✅ Scored {n} sectors @ {date} | "
            f"weekly rows: {len(weekly)}"
        )
        return n

    def run_range(
        self,
        from_date: str,
        to_date: str,
        batch_days: int = 90,
    ) -> int:
        """
        Score all sectors for a DATE RANGE in batches.
        batch_days=90 is safe — score computation is lightweight.

        Returns: total sector-date rows written.
        """
        # Discover available factor dates in range
        query = text("""
            SELECT DISTINCT date FROM sector_factor_daily
            WHERE date BETWEEN :from_date AND :to_date
            ORDER BY date
        """)
        try:
            with self.db.engine.connect() as conn:
                rows = conn.execute(
                    query,
                    {"from_date": from_date, "to_date": to_date}
                ).fetchall()
            available = [row[0] for row in rows]
        except Exception as e:
            logger.error(f"❌ Lỗi lấy available factor dates: {e}")
            return 0

        if not available:
            logger.warning(
                f"⚠️  Không có sector_factor_daily: {from_date} → {to_date}"
            )
            return 0

        logger.info(
            f"🚀 Scoring {len(available)} ngày "
            f"({from_date} → {to_date}) | batch={batch_days}d"
        )

        total = 0
        pbar  = tqdm(
            range(0, len(available), batch_days),
            desc="Scoring sectors",
            unit="batch",
        )

        for i in pbar:
            batch = available[i : i + batch_days]
            b_from = str(batch[0])
            b_to   = str(batch[-1])
            pbar.set_postfix({"range": f"{b_from}→{b_to}"})

            daily, weekly = self._run_pipeline(b_from, b_to)
            self._save_scores(daily)
            self._save_weekly(weekly)
            total += len(daily)

        logger.info(f"✅ Hoàn tất scoring: {total} sector-date rows")
        return total

    def run_maintenance(self) -> int:
        """
        Maintenance mode — only score dates not yet in sector_score_daily.
        Run daily after SectorAggregationService.run_maintenance().

        Returns: total sector-date rows written.
        """
        last  = self._get_latest_score_date()
        today = datetime.now().date()

        if last and last >= today:
            logger.info("✅ sector_score_daily đã cập nhật đến hôm nay")
            return 0

        from_date = (
            (last + timedelta(days=1)).strftime("%Y-%m-%d")
            if last else "2021-01-01"
        )
        to_date = today.strftime("%Y-%m-%d")

        logger.info(f"🔄 Bảo trì scoring: {from_date} → {to_date}")
        return self.run_range(from_date, to_date)

    def run_all(self, from_date: str = "2021-01-01") -> int:
        """
        Full rebuild from from_date to today.
        Run after SectorAggregationService.run_all().

        Returns: total sector-date rows written.
        """
        to_date = datetime.now().date().strftime("%Y-%m-%d")
        logger.info(
            f"🚀 Full rebuild sector_score_daily: {from_date} → {to_date}"
        )
        return self.run_range(from_date, to_date, batch_days=90)

    def get_latest_ranking(
        self,
        date: Optional[str] = None,
        min_coverage: float = 0.3,
        min_stocks: int = 3,
    ) -> pd.DataFrame:
        """
        Query latest sector ranking for dashboard / screener.

        Args:
            date         : 'YYYY-MM-DD', default = most recent date in DB
            min_coverage : exclude thin sectors below this coverage_pct
            min_stocks   : exclude sectors with fewer stocks than this

        Returns: DataFrame sorted by rank ascending.
        """
        date_clause = (
            "ssd.date = :date"
            if date else
            "ssd.date = (SELECT MAX(date) FROM sector_score_daily)"
        )
        params: dict = {"min_coverage": min_coverage, "min_stocks": min_stocks}
        if date:
            params["date"] = date

        query = text(f"""
            SELECT
                ssd.date,
                ssd.sector_name,
                ssd.rank,
                ssd.total_score,
                ssd.inst_score,
                ssd.breadth_score,
                ssd.regime,
                ssd.regime_score,
                ssd.score_delta_1d,
                ssd.score_delta_5d,
                sfd.n_stocks,
                sfd.coverage_pct,
                sfd.weighted_cmf,
                sfd.breadth_cmf_positive
            FROM sector_score_daily ssd
            JOIN sector_factor_daily sfd
              ON sfd.date        = ssd.date
             AND sfd.sector_name = ssd.sector_name
            WHERE {date_clause}
              AND sfd.coverage_pct >= :min_coverage
              AND sfd.n_stocks     >= :min_stocks
            ORDER BY ssd.rank ASC NULLS LAST
        """)
        try:
            with self.db.engine.connect() as conn:
                return pd.read_sql(query, conn, params=params)
        except Exception as e:
            logger.error(f"❌ Lỗi get_latest_ranking: {e}")
            return pd.DataFrame()

    def get_weekly_ranking(
        self,
        year_week: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Query weekly sector ranking.

        Args:
            year_week : YYYYWW integer, default = latest week in DB

        Returns: DataFrame sorted by rank ascending.
        """
        yw_clause = (
            "year_week = :yw"
            if year_week else
            "year_week = (SELECT MAX(year_week) FROM sector_rank_weekly)"
        )
        params = {"yw": year_week} if year_week else {}

        query = text(f"""
            SELECT
                year_week, date_from, date_to, sector_name,
                rank, total_score, inst_score, breadth_score,
                regime, regime_score, score_delta_1w, n_trading_days
            FROM sector_rank_weekly
            WHERE {yw_clause}
            ORDER BY rank ASC NULLS LAST
        """)
        try:
            with self.db.engine.connect() as conn:
                return pd.read_sql(query, conn, params=params)
        except Exception as e:
            logger.error(f"❌ Lỗi get_weekly_ranking: {e}")
            return pd.DataFrame()


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
    svc = SectorScoringService(db)

    while True:
        print("\n" + "=" * 55)
        print("  SECTOR SCORING SERVICE")
        print("=" * 55)
        print("  1. Score 1 ngày cụ thể")
        print("  2. Score theo khoảng ngày")
        print("  3. Bảo trì (chỉ ngày còn thiếu)")
        print("  4. Full rebuild từ ngày cụ thể")
        print("  5. Xem ranking mới nhất")
        print("  6. Xem weekly ranking")
        print("  0. Thoát")

        choice = input("\nLựa chọn: ").strip()

        if choice == "1":
            d = input("Ngày (YYYY-MM-DD): ").strip()
            svc.run_date(d)

        elif choice == "2":
            f = input("Từ ngày (YYYY-MM-DD): ").strip()
            t = input("Đến ngày (YYYY-MM-DD): ").strip()
            svc.run_range(f, t)

        elif choice == "3":
            svc.run_maintenance()

        elif choice == "4":
            f = input("Từ ngày (mặc định 2021-01-01): ").strip() or "2021-01-01"
            svc.run_all(from_date=f)

        elif choice == "5":
            d = input("Ngày (Enter = mới nhất): ").strip() or None
            result = svc.get_latest_ranking(date=d)
            if result.empty:
                print("Không có dữ liệu.")
            else:
                cols = ["rank","sector_name","total_score","inst_score",
                        "breadth_score","regime","score_delta_1d"]
                print(result[cols].to_string(index=False))

        elif choice == "6":
            yw = input("Tuần YYYYWW (Enter = mới nhất): ").strip()
            yw = int(yw) if yw else None
            result = svc.get_weekly_ranking(year_week=yw)
            if result.empty:
                print("Không có dữ liệu.")
            else:
                print(result.to_string(index=False))

        elif choice == "0":
            print("Thoát.")
            break
        else:
            print("Nhập 0-6.")
