"""
src/indicators/mf_indicators.py
════════════════════════════════════════════════════════════════════════════
Stock-level Money Flow Indicators for the Sector Rotation Engine.

Contract
────────
Input  : DataFrame with price-adjusted columns (after _adjust_prices):
           trading_date      DATE / datetime
           close_price       adj_close   (= close_price_adjusted)
           highest_price     adj_high    (= highest_price  × adj_factor)
           lowest_price      adj_low     (= lowest_price   × adj_factor)
           open_price        adj_open    (= open_price     × adj_factor)
           total_match_vol   integer
           total_match_val   float  (VND)
           foreign_buy_vol_total   integer
           foreign_sell_vol_total  integer

Output : Same DataFrame with additional columns:
           mfi, cmf, rvol, nmf, nmf_zscore, nmf_accel, nff_zscore

Design Notes
────────────
• Pure functional — no DB access, no side effects.
• Every function follows the pattern used by the existing indicator modules:
      df = calc_xxx(df, ...)  →  returns df with new column(s) added
• Warmup rows are left as NaN — the caller decides how to slice them.
• All calculations are fully vectorised (pandas / numpy), no Python loops.
• adj_factor already applied upstream by IndicatorService._adjust_prices()
  so this module only sees clean adjusted prices.

Indicator Definitions
─────────────────────
MFI  (14)  — Money Flow Index          [0, 100]
CMF  (20)  — Chaikin Money Flow        [-1, +1]
RVOL (20)  — Relative Volume           [0, ∞)
NMF        — Net Money Flow            signed VND
NMF_zscore (20) — Abnormal flow z-score
NMF_accel  (5/20) — Flow persistence   EMA5(NMF) / EMA20(|NMF|)
NFF_zscore (20) — Net Foreign Flow z-score  [institutional signal]
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════
# 1. MFI — Money Flow Index
# ══════════════════════════════════════════════════════════════

def calc_mfi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Money Flow Index (MFI)
    ───────────────────────
    Typical Price (TP) = (adj_high + adj_low + adj_close) / 3
    Raw Money Flow     = TP × total_match_vol
    Positive MF / Negative MF based on TP direction vs previous bar.
    MFI = 100 - 100 / (1 + sum(+MF, n) / sum(-MF, n))

    Range : [0, 100]
    """
    df = df.copy()

    tp = (df["highest_price"] + df["lowest_price"] + df["close_price"]) / 3
    rmf = tp * df["total_match_vol"]

    tp_prev = tp.shift(1)

    # Separate positive and negative money flow
    pos_mf = rmf.where(tp > tp_prev, 0.0)
    neg_mf = rmf.where(tp < tp_prev, 0.0)

    sum_pos = pos_mf.rolling(period, min_periods=period).sum()
    sum_neg = neg_mf.rolling(period, min_periods=period).sum()

    # Avoid division by zero on flat days (sum_neg == 0 means no negative flow)
    money_ratio = sum_pos / sum_neg.replace(0, np.nan)
    df["mfi"] = (100 - (100 / (1 + money_ratio))).round(4)

    return df


# ══════════════════════════════════════════════════════════════
# 2. CMF — Chaikin Money Flow
# ══════════════════════════════════════════════════════════════

def calc_cmf(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Chaikin Money Flow (CMF)
    ─────────────────────────
    Money Flow Multiplier (MFM) = ((close - low) - (high - close))
                                  / (high - low)
    Money Flow Volume (MFV)     = MFM × volume
    CMF                         = sum(MFV, n) / sum(volume, n)

    Range : approximately [-1, +1]
    Note  : When high == low (rare gap/halt days), MFM defaults to 0.
    """
    df = df.copy()

    hl_range = df["highest_price"] - df["lowest_price"]

    # Safe division: if high == low, multiplier = 0
    mfm = ((df["close_price"] - df["lowest_price"]) -
           (df["highest_price"] - df["close_price"])) / hl_range.replace(0, np.nan)
    mfm = mfm.fillna(0.0)

    mfv = mfm * df["total_match_vol"]

    sum_mfv = mfv.rolling(period, min_periods=period).sum()
    sum_vol = df["total_match_vol"].rolling(period, min_periods=period).sum()

    df["cmf"] = (sum_mfv / sum_vol.replace(0, np.nan)).round(6)

    return df


# ══════════════════════════════════════════════════════════════
# 3. RVOL — Relative Volume
# ══════════════════════════════════════════════════════════════

def calc_rvol(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Relative Volume (RVOL)
    ───────────────────────
    RVOL = volume / MA(volume, n)

    Range : [0, ∞)
    Usage : participation expansion, breakout confirmation.
    Note  : Uses total_match_vol (matched volume only, excludes put-through deals)
            to isolate organic market participation.
    """
    df = df.copy()

    vol_ma = df["total_match_vol"].rolling(period, min_periods=period).mean()
    df["rvol"] = (df["total_match_vol"] / vol_ma.replace(0, np.nan)).round(4)

    return df


# ══════════════════════════════════════════════════════════════
# 4. NMF — Net Money Flow
# ══════════════════════════════════════════════════════════════

def calc_nmf(df: pd.DataFrame) -> pd.DataFrame:
    """
    Net Money Flow (NMF)
    ─────────────────────
    NMF = +close × volume   if close_t  > close_(t-1)
          -close × volume   if close_t  < close_(t-1)
           0                if close_t == close_(t-1)

    Units  : VND (signed traded value)
    Purpose: Signed capital flow estimate — positive = net inflow.
    """
    df = df.copy()

    close_prev = df["close_price"].shift(1)
    direction  = np.sign(df["close_price"] - close_prev)   # +1, 0, -1

    df["nmf"] = (direction * df["close_price"] * df["total_match_vol"]).round(2)

    return df


# ══════════════════════════════════════════════════════════════
# 5. NMF Z-Score — Abnormal Money Flow
# ══════════════════════════════════════════════════════════════

def calc_nmf_zscore(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    NMF Z-Score
    ────────────
    Z_NMF = (NMF - MA(NMF, n)) / STD(NMF, n)

    Requires : 'nmf' column (run calc_nmf first)
    Purpose  : Detect abnormal institutional surges above the rolling baseline.
    Caution  : STD can be near-zero for illiquid stocks → replace with NaN.
    """
    if "nmf" not in df.columns:
        raise ValueError("calc_nmf_zscore requires 'nmf' column. Run calc_nmf() first.")

    df = df.copy()

    nmf_ma  = df["nmf"].rolling(period, min_periods=period).mean()
    nmf_std = df["nmf"].rolling(period, min_periods=period).std()

    # Guard: std < 1 VND (effectively zero for large-cap stocks) → NaN
    nmf_std_safe = nmf_std.where(nmf_std > 1.0, np.nan)

    df["nmf_zscore"] = ((df["nmf"] - nmf_ma) / nmf_std_safe).round(4)

    return df


# ══════════════════════════════════════════════════════════════
# 6. NMF Acceleration — Flow Persistence
# ══════════════════════════════════════════════════════════════

def calc_nmf_accel(
    df: pd.DataFrame,
    fast_span: int = 5,
    slow_span: int = 20,
) -> pd.DataFrame:
    """
    NMF Acceleration
    ─────────────────
    Accel = EMA(NMF, fast) / EMA(|NMF|, slow)

    Requires : 'nmf' column (run calc_nmf first)
    Range    : approximately (-∞, +∞); > 1 signals sustained inflow,
               < -1 signals sustained outflow.
    Purpose  : Detect early sector rotation via flow persistence —
               small but consistently positive NMF gives Accel > 1
               even before a large volume spike appears.
    Caution  : EMA(|NMF|) can be zero for newly listed / halted stocks.
    """
    if "nmf" not in df.columns:
        raise ValueError("calc_nmf_accel requires 'nmf' column. Run calc_nmf() first.")

    df = df.copy()

    ema_fast = df["nmf"].ewm(span=fast_span, adjust=False, min_periods=fast_span).mean()
    ema_slow = df["nmf"].abs().ewm(span=slow_span, adjust=False, min_periods=slow_span).mean()

    df["nmf_accel"] = (ema_fast / ema_slow.replace(0, np.nan)).round(6)

    return df


# ══════════════════════════════════════════════════════════════
# 7. NFF Z-Score — Net Foreign Flow (Vietnamese market signal)
# ══════════════════════════════════════════════════════════════

def calc_nff_zscore(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Net Foreign Flow Z-Score (NFF_zscore)
    ───────────────────────────────────────
    NFF      = (foreign_buy_vol - foreign_sell_vol) × adj_close
    Z_NFF    = (NFF - MA(NFF, n)) / STD(NFF, n)

    Requires : 'foreign_buy_vol_total', 'foreign_sell_vol_total' columns
    Purpose  : Detect abnormal institutional foreign capital flow.
               Key alpha on Vietnamese market where foreign fund
               positioning drives sector rotation leadership.
    Note     : Stocks with no foreign room (room = 0) will produce
               NFF = 0 consistently → zscore = NaN.  This is correct
               behaviour — they should not influence sector aggregation.
    """
    df = df.copy()

    net_foreign_vol = df["foreign_buy_vol_total"] - df["foreign_sell_vol_total"]
    nff = net_foreign_vol * df["close_price"]

    nff_ma  = nff.rolling(period, min_periods=period).mean()
    nff_std = nff.rolling(period, min_periods=period).std()

    nff_std_safe = nff_std.where(nff_std > 1.0, np.nan)

    df["nff_zscore"] = ((nff - nff_ma) / nff_std_safe).round(4)

    return df


# ══════════════════════════════════════════════════════════════
# 8. Pipeline entry point — compute all MF indicators
# ══════════════════════════════════════════════════════════════

def calc_mf_indicators(
    df: pd.DataFrame,
    mfi_period:       int = 14,
    cmf_period:       int = 20,
    rvol_period:      int = 20,
    nmf_zscore_period: int = 20,
    nmf_accel_fast:   int = 5,
    nmf_accel_slow:   int = 20,
    nff_zscore_period: int = 20,
) -> pd.DataFrame:
    """
    Full money-flow indicator pipeline for one stock.

    Applies:
        calc_mfi  → mfi
        calc_cmf  → cmf
        calc_rvol → rvol
        calc_nmf  → nmf              (prerequisite for zscore + accel)
        calc_nmf_zscore → nmf_zscore
        calc_nmf_accel  → nmf_accel
        calc_nff_zscore → nff_zscore

    Input requirements
    ──────────────────
    df must already have prices adjusted (via IndicatorService._adjust_prices
    or the equivalent) so that:
        close_price    = adj_close
        highest_price  = adj_high
        lowest_price   = adj_low
        open_price     = adj_open

    Volume columns must be raw (not adjusted):
        total_match_vol
        total_match_val
        foreign_buy_vol_total
        foreign_sell_vol_total

    Returns
    ───────
    DataFrame with all original columns + 7 new indicator columns.
    Warmup rows contain NaN — slice them in the calling service.
    """
    # Validate required columns
    required = {
        "trading_date", "close_price", "highest_price", "lowest_price",
        "open_price", "total_match_vol", "total_match_val",
        "foreign_buy_vol_total", "foreign_sell_vol_total",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"calc_mf_indicators: missing columns {missing}")

    # Ensure sorted ascending (critical for shift-based calculations)
    df = df.sort_values("trading_date").reset_index(drop=True)

    # Run pipeline
    df = calc_mfi(df, period=mfi_period)
    df = calc_cmf(df, period=cmf_period)
    df = calc_rvol(df, period=rvol_period)
    df = calc_nmf(df)
    df = calc_nmf_zscore(df, period=nmf_zscore_period)
    df = calc_nmf_accel(df, fast_span=nmf_accel_fast, slow_span=nmf_accel_slow)
    df = calc_nff_zscore(df, period=nff_zscore_period)

    return df
