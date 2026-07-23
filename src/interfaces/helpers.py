"""
src/interfaces/helpers.py
════════════════════════════════════════════════════════════════════════════
Shared utilities for all UI pages.

Sections
────────
1. Constants          — MA colours, period maps, signal type list
2. Logging            — _SessionHandler + setup_logging()
3. Styling helpers    — direction_color_style(), score/regime/delta/cell
4. Chart builders     — compute_adj_prices, build_ma_series, build_markers,
                        render_price_chart
5. DB fetch helpers   — fetch_latest_mfi_cmf(), fetch_cmf_history()
                        (extracted from page2 inline SQL)
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import text
from streamlit_lightweight_charts import renderLightweightCharts

# ── 1. Constants ──────────────────────────────────────────────────────────────

MA_COLORS: dict[str, str] = {
    "MA5":  "#3b82f6",
    "MA10": "#8b5cf6",
    "MA20": "#f59e0b",
    "MA50": "#10b981",
    "MA200":"#f43f5e",
}

MA_PERIODS: dict[str, int] = {
    "MA5": 5, "MA10": 10, "MA20": 20, "MA50": 50, "MA200": 200,
}

# Shared period-to-days mapping used by page2, page4, page5
PERIOD_DAYS: dict[str, int] = {
    "1 tháng":  30,
    "3 tháng":  90,
    "6 tháng":  180,
    "1 năm":    365,
    "2 năm":    730,
    "Toàn bộ":  3650,
}

ALL_SIGNAL_TYPES: list[str] = [
    "MA_GOLDEN_CROSS", "MA_DEATH_CROSS",
    "RSI_OVERSOLD", "RSI_OVERBOUGHT",
    "MACD_BULLISH_CROSS", "MACD_BEARISH_CROSS",
    "BB_SQUEEZE_BREAKOUT_UP", "BB_SQUEEZE_BREAKOUT_DOWN",
    "VOLUME_SPIKE", "FOREIGN_ACCUMULATION", "FOREIGN_DISTRIBUTION",
]

# Regime palette — single source of truth (was duplicated in page4 + helpers)
REGIME_COLOR: dict[str, str] = {
    "Expansion":     "#16a34a",
    "EarlyRotation": "#2563eb",
    "Neutral":       "#6b7280",
    "Distribution":  "#d97706",
    "Contraction":   "#dc2626",
}

REGIME_BG: dict[str, str] = {
    "Expansion":     "#dcfce7",
    "EarlyRotation": "#dbeafe",
    "Neutral":       "#f3f4f6",
    "Distribution":  "#fef3c7",
    "Contraction":   "#fee2e2",
}

# Per-column colour thresholds for symbol-matrix cells (used by page4)
MATRIX_THRESHOLDS: dict[str, tuple[float, float]] = {
    "CMF":   (-0.05,  0.05),
    "MFI":   (40.0,   60.0),
    "RVOL":  (0.8,    1.3),
    "NMF_z": (-0.5,   0.5),
    "Accel": (-0.3,   0.3),
    "NFF_z": (-0.5,   0.5),
}

# ── VWAP visual configuration ─────────────────────────────────────────────────
# Central VWAP line colour per anchor
_VWAP_LINE_COLOR: dict[str, str] = {
    "W": "#f59e0b",  # amber  — weekly
    "M": "#3b82f6",  # blue   — monthly
    "Y": "#a855f7",  # purple — yearly
}

# Band fill colours — (band1_rgba, band2_rgba, band3_rgba)
# Band 3 uses warning tones (red/orange tint) as per PRD
_VWAP_BAND_COLORS: dict[str, tuple[str, str, str]] = {
    "W": (
        "rgba(245,158,11,0.10)",  # band 1 fill
        "rgba(245,158,11,0.07)",  # band 2 fill
        "rgba(239,68,68,0.12)",  # band 3 fill — warning zone
    ),
    "M": (
        "rgba(59,130,246,0.10)",
        "rgba(59,130,246,0.07)",
        "rgba(239,68,68,0.12)",
    ),
    "Y": (
        "rgba(168,85,247,0.10)",
        "rgba(168,85,247,0.07)",
        "rgba(239,68,68,0.12)",
    ),
}

_ANCHOR_LABEL: dict[str, str] = {
    "W": "Tuần (Weekly)",
    "M": "Tháng (Monthly)",
    "Y": "Năm (Yearly)",
}


# ── 2. Logging ─────────────────────────────────────────────────────────────────

class _SessionHandler(logging.Handler):
    """Write log records into st.session_state and (optionally) st.write."""

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        if "log_messages" not in st.session_state:
            st.session_state.log_messages = []
        st.session_state.log_messages.append(msg)
        try:
            st.write(msg)
        except Exception:
            pass


def setup_logging() -> None:
    """Register _SessionHandler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in root.handlers[:]:
        if isinstance(h, _SessionHandler):
            root.removeHandler(h)
    handler = _SessionHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)s  %(message)s")
    )
    root.addHandler(handler)


# ── 3. Styling helpers ─────────────────────────────────────────────────────────

def direction_color_style(val: str) -> str:
    """CSS for BUY/SELL direction cells (used in page3 signal table)."""
    if val == "BUY":
        return "background-color:#d1fae5;color:#065f46;font-weight:600"
    if val == "SELL":
        return "background-color:#fee2e2;color:#991b1b;font-weight:600"
    return ""


def score_color_style(val: float) -> str:
    """Background+foreground CSS for total_score cells in sector tables."""
    if pd.isna(val):
        return "background-color:#f3f4f6"

    if val >= 0.45:  return "background-color:#16a34a;color:#ffffff"
    if val >= 0.30:  return "background-color:#4ade80;color:#14532d"
    if val >= 0.10:  return "background-color:#bbf7d0;color:#166534"
    if val >= -0.10:  return "background-color:#f3f4f6;color:#374151"
    if val >= -0.30: return "background-color:#fee2e2;color:#991b1b"
    if val >= -0.45: return "background-color:#fca5a5;color:#7f1d1d"

    return "background-color:#dc2626;color:#fff"


def regime_style(val: str) -> str:
    """CSS for regime label cells in sector tables."""
    bg  = REGIME_BG.get(val,  "#f3f4f6")
    clr = REGIME_COLOR.get(val, "#374151")
    return f"background-color:{bg};color:{clr};font-weight:600"


def delta_style(val: float) -> str:
    """CSS for score-delta cells (positive=green, negative=red)."""
    if pd.isna(val):
        return ""
    return (
        "color:#16a34a;font-weight:600" if val > 0
        else "color:#dc2626;font-weight:600" if val < 0
        else ""
    )


def pct_change_style(val: float) -> str:
    """
    CSS for per_price_change column in symbol matrix.
    Green gradient up to +7% (ceiling); red gradient down to -7% (floor).
    """
    if pd.isna(val):
        return "background-color:#f3f4f6;color:#9ca3af"
    if val > 0:
        ratio = min(val / 7.0, 1.0)
        r = int(22  + ratio * (22  - 22))
        g = int(163 + ratio * (239 - 163))
        b = int(74  + ratio * (172 - 74))
        luma = 0.299*r + 0.587*g + 0.114*b
        fg   = "#14532d" if luma > 160 else "#fff"
        return f"background-color:rgb({r},{g},{b});color:{fg};font-weight:600"
    if val < 0:
        ratio = min(abs(val) / 7.0, 1.0)
        r = int(252 + ratio * (220 - 252))
        g = int(165 + ratio * (38  - 165))
        b = int(165 + ratio * (38  - 165))
        luma = 0.299*r + 0.587*g + 0.114*b
        fg   = "#7f1d1d" if luma > 160 else "#fff"
        return f"background-color:rgb({r},{g},{b});color:{fg};font-weight:600"
    return "background-color:#f3f4f6;color:#374151"


def cell_style(col: str):
    """
    Return a per-value styler callable for one indicator column.
    Uses MATRIX_THRESHOLDS for lo/hi bounds; intensity scales with distance.
    """
    lo, hi = MATRIX_THRESHOLDS.get(col, (-0.5, 0.5))

    def _style(val: float) -> str:
        if pd.isna(val):
            return "background-color:#f3f4f6;color:#9ca3af"
        if val > hi:
            ratio = min((val - hi) / max(abs(hi), 0.001), 3.0) / 3.0
            r = int(22  + ratio * (134 - 22))
            g = int(163 + ratio * (239 - 163))
            b = int(74  + ratio * (172 - 74))
            luma = 0.299*r + 0.587*g + 0.114*b
            fg   = "#14532d" if luma > 160 else "#fff"
            return f"background-color:rgb({r},{g},{b});color:{fg}"
        if val < lo:
            ratio = min((lo - val) / max(abs(lo), 0.001), 3.0) / 3.0
            r = int(252 + ratio * (220 - 252))
            g = int(165 + ratio * (38  - 165))
            b = int(165 + ratio * (38  - 165))
            luma = 0.299*r + 0.587*g + 0.114*b
            fg   = "#7f1d1d" if luma > 160 else "#fff"
            return f"background-color:rgb({r},{g},{b});color:{fg}"
        return "background-color:#f3f4f6;color:#374151"

    return _style


# ── 4. Chart builders ──────────────────────────────────────────────────────────

def compute_adj_prices(raw: pd.DataFrame) -> pd.DataFrame:
    """Add adj_* columns and a 'time' string column. Does not mutate raw."""
    df = raw.copy()
    factor = (df["close_price_adjusted"] / df["close_price"]).fillna(1.0)
    df["adj_open"]  = (df["open_price"]    * factor).round(2)
    df["adj_high"]  = (df["highest_price"] * factor).round(2)
    df["adj_low"]   = (df["lowest_price"]  * factor).round(2)
    df["adj_close"] = df["close_price_adjusted"].round(2)
    df["time"]      = df["trading_date"].apply(lambda d: d.strftime("%Y-%m-%d"))
    return df


def build_ma_series(
    raw_adj: pd.DataFrame,
    selected_mas: list[str],
    start: date_type,
) -> list[dict]:
    """Compute MA lines from adj_close; only emit bars on/after *start*."""
    series = []
    adj   = raw_adj["adj_close"]
    dates = raw_adj["trading_date"]
    for ma in selected_mas:
        n    = MA_PERIODS[ma]
        vals = adj.rolling(n, min_periods=n).mean().round(2)
        data = [
            {"time": dates.iloc[i].strftime("%Y-%m-%d"), "value": float(vals.iloc[i])}
            for i in range(len(raw_adj))
            if pd.notna(vals.iloc[i]) and dates.iloc[i] >= start
        ]
        if not data:
            continue
        series.append({
            "type": "Line",
            "data": data,
            "options": {
                "color":              MA_COLORS[ma],
                "lineWidth":          1,
                "priceLineVisible":   False,
                "lastValueVisible":   True,
                "title":              ma,
                "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
            },
        })
    return series


def build_markers(sig_df: pd.DataFrame) -> list[dict]:
    """Convert trading_signals rows to TradingView marker objects."""
    markers = []
    for _, r in sig_df.iterrows():
        buy = r["signal_direction"] == "BUY"
        markers.append({
            "time":     r["signal_date"].strftime("%Y-%m-%d"),
            "position": "belowBar" if buy else "aboveBar",
            "color":    "#22c55e" if buy else "#ef4444",
            "shape":    "arrowUp" if buy else "arrowDown",
            "text":     r["signal_type"].replace("_", " "),
            "size":     max(1, min(int(float(r["strength"]) * 3), 3)),
        })
    return sorted(markers, key=lambda m: m["time"])


def calc_zigzag(
    df: pd.DataFrame,
    length: int = 9,
    high_col: str = "adj_high",
    low_col:  str = "adj_low",
    time_col: str = "trading_date",
) -> list[dict]:
    """
    Compute ZigZag pivot points from a price DataFrame.

    Translates the Pine Script ZigZag logic (highest/lowest over `length` bars,
    trend-flip detection) into a Python/pandas implementation.

    Algorithm
    ─────────
    At each bar:
    • to_up   = high >= rolling_max(high, length)   → potential upswing
    • to_down = low  <= rolling_min(low,  length)   → potential downswing

    trend starts at +1 (uptrend).
    • If trend == +1 and to_down fires → flip to -1, record the HIGH pivot
      as the highest high since the last downswing.
    • If trend == -1 and to_up fires  → flip to +1, record the LOW pivot
      as the lowest low since the last upswing.

    On each flip, the pivot price and date are recorded.
    The returned list connects consecutive pivots as line-series data points,
    which TradingView lightweight-charts renders as the ZigZag line.

    Parameters
    ──────────
    df       : DataFrame with at least high_col, low_col, time_col columns.
    length   : lookback period for highest/lowest detection (default 9).
    high_col : column name for bar highs  (default "adj_high").
    low_col  : column name for bar lows   (default "adj_low").
    time_col : column name for bar date   (default "trading_date").

    Returns
    ───────
    list[dict]  — TradingView Line-series data: [{"time": "YYYY-MM-DD", "value": float}, …]
    Each dict is one confirmed ZigZag pivot; the chart library draws straight
    lines between consecutive points.
    """
    if df.empty or len(df) < length + 1:
        return []

    highs  = df[high_col].values.astype(float)
    lows   = df[low_col].values.astype(float)
    dates  = df[time_col].values   # numpy datetime64 or date objects
    n      = len(df)

    # Rolling highest / lowest (pandas does this cleanly)
    roll_high = df[high_col].rolling(length, min_periods=length).max().values
    roll_low  = df[low_col].rolling(length, min_periods=length).min().values

    # ── State ──────────────────────────────────────────────────────────────────
    trend         = 1    # +1 = uptrend (looking for next low); -1 = downtrend
    pivots: list[tuple] = []   # (date_str, price)
    last_flip_bar = 0    # bar index of the last confirmed trend flip

    for i in range(length - 1, n):
        to_up   = (roll_high[i] is not None and not np.isnan(roll_high[i])
                   and highs[i] >= roll_high[i])
        to_down = (roll_low[i] is not None and not np.isnan(roll_low[i])
                   and lows[i]  <= roll_low[i])

        if trend == 1 and to_down:
            # Flip to downtrend: record the HIGH pivot in [last_flip_bar .. i]
            seg_highs = highs[last_flip_bar:i + 1]
            hi_idx    = last_flip_bar + int(np.argmax(seg_highs))
            d         = dates[hi_idx]
            date_str  = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            pivots.append((date_str, float(highs[hi_idx])))
            trend         = -1
            last_flip_bar = i

        elif trend == -1 and to_up:
            # Flip to uptrend: record the LOW pivot in [last_flip_bar .. i]
            seg_lows = lows[last_flip_bar:i + 1]
            lo_idx   = last_flip_bar + int(np.argmin(seg_lows))
            d        = dates[lo_idx]
            date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            pivots.append((date_str, float(lows[lo_idx])))
            trend         = 1
            last_flip_bar = i

    # ── Append the "live" (unconfirmed) last pivot so the ZigZag reaches
    #    the current bar rather than stopping at the previous confirmed flip.
    if pivots:
        if trend == 1:
            # Still in uptrend → the live pivot is the highest high so far
            seg   = highs[last_flip_bar:]
            hi_off = int(np.argmax(seg))
            hi_idx = last_flip_bar + hi_off
            d = dates[hi_idx]
            date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            live_val = float(highs[hi_idx])
        else:
            # Still in downtrend → the live pivot is the lowest low so far
            seg   = lows[last_flip_bar:]
            lo_off = int(np.argmin(seg))
            lo_idx = last_flip_bar + lo_off
            d = dates[lo_idx]
            date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            live_val = float(lows[lo_idx])

        # Only append live pivot if it differs from last confirmed pivot
        if not pivots or pivots[-1][0] != date_str:
            pivots.append((date_str, live_val))

    # ── Convert to TradingView Line-series format ──────────────────────────────
    return [{"time": t, "value": round(v, 2)} for t, v in pivots]


def build_zigzag_series(
    price_df: pd.DataFrame,
    length: int = 9,
    start_date=None,
) -> list[dict]:
    """
    Build a TradingView Line-series dict for the ZigZag overlay.

    Parameters
    ──────────
    price_df   : adjusted price DataFrame with adj_high, adj_low, trading_date.
    length     : ZigZag pivot detection length.
    start_date : only include pivots on/after this date (date or None).

    Returns
    ───────
    A single-element list containing the series config dict, ready to be
    appended to the chart's `series` list.  Returns [] if no pivots found.
    """
    pivots = calc_zigzag(price_df, length=length)
    if not pivots:
        return []

    # Filter to visible range only after computing on full data
    if start_date is not None:
        start_str = (
            start_date.strftime("%Y-%m-%d")
            if hasattr(start_date, "strftime")
            else str(start_date)[:10]
        )
        # Keep the last pivot before start_date as an anchor so the first
        # visible segment is drawn from the correct starting price.
        visible = [p for p in pivots if p["time"] >= start_str]
        # Find the last pivot strictly before start so the line doesn't float
        before  = [p for p in pivots if p["time"] < start_str]
        if before and visible:
            pivots = [before[-1]] + visible
        elif visible:
            pivots = visible
        else:
            return []

    return [
        {
            "type": "Line",
            "data": pivots,
            "options": {
                "color":            "#38bdf8",   #  stands out on candlestick chart
                "lineWidth":        2,
                "lineStyle":        0,            # solid
                "priceLineVisible": False,
                "lastValueVisible": False,
                "crosshairMarkerVisible": True,
                "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
            },
        }
    ]


def render_price_chart(
    price_df: pd.DataFrame,
    ma_series: list[dict],
    markers: list[dict],
    chart_type: str,
    key: str,
    zigzag_series: list[dict] | None = None,
) -> None:
    """Render TradingView lightweight price panel + volume panel."""
    bg   = {"type": "solid", "color": "#ffffff"}
    grid = {"vertLines": {"color": "#f0f0f0"}, "horzLines": {"color": "#f0f0f0"}}

    ma_vol_data = [
        {"time": row["time"], "value": float(row["vol_ma20"])}
        for _, row in price_df[["time", "vol_ma20"]].dropna().iterrows()
    ]

    if chart_type == "Nến (Candlestick)":
        main_data = [
            {
                "time":  r["time"],
                "open":  r["adj_open"],
                "high":  r["adj_high"],
                "low":   r["adj_low"],
                "close": r["adj_close"],
            }
            for _, r in price_df.iterrows()
        ]
        main_series = {
            "type": "Candlestick",
            "data": main_data,
            "markers": markers,
            "options": {
                "upColor":        "#26a69a", "downColor":       "#ef5350",
                "borderUpColor":  "#26a69a", "borderDownColor": "#ef5350",
                "wickUpColor":    "#26a69a", "wickDownColor":   "#ef5350",
                "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
            },
        }
    else:
        main_data = [
            {"time": r["time"], "value": r["adj_close"]}
            for _, r in price_df.iterrows()
        ]
        main_series = {
            "type": "Line",
            "data": main_data,
            "markers": markers,
            "options": {
                "color":     "#2962ff",
                "lineWidth": 2,
                "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
            },
        }

    vol_data = [
        {
            "time":  r["time"],
            "value": float(r["total_match_vol"]),
            "color": (
                "rgba(38,166,154,0.5)"
                if r["adj_close"] >= r["adj_open"]
                else "rgba(239,83,80,0.5)"
            ),
        }
        for _, r in price_df.iterrows()
    ]

    charts = [
        {
            "chart": {
                "height": 440,
                "layout": {"background": bg, "textColor": "#333"},
                "grid": grid,
                "crosshair": {"mode": 1},
                "timeScale": {"borderColor": "#d1d5db", "rightOffset": 8},
                "rightPriceScale": {"borderColor": "#d1d5db"},
            },
            "series": [main_series] + ma_series + (zigzag_series or []),
        },
        {
            "chart": {
                "height": 100,
                "layout": {"background": bg, "textColor": "#333"},
                "grid": grid,
                "timeScale": {"borderColor": "#d1d5db", "visible": False},
                "rightPriceScale": {
                    "borderColor": "#d1d5db",
                    "scaleMargins": {"top": 0.05, "bottom": 0},
                },
            },
            "series": [
                {
                    "type": "Histogram",
                    "data": vol_data,
                    "options": {"priceFormat": {"type": "volume"}, "priceScaleId": ""},
                },
                {
                    "type": "Line",
                    "data": ma_vol_data,
                    "options": {
                        "color":            "#FF6D00",
                        "lineWidth":        2,
                        "priceLineVisible": False,
                        "lastValueVisible": True,
                        "title":            "MAVol20",
                        "priceFormat":      {"type": "volume", "precision": 0},
                        "priceScaleId":     "",
                    },
                },
            ],
        },
    ]
    renderLightweightCharts(charts, key=key)


# ── 5. DB fetch helpers extracted from page2 inline SQL ───────────────────────

def fetch_latest_mfi_cmf(
    db, symbol: str
) -> tuple[Optional[float], Optional[float]]:
    """
    Fetch the most recent MFI and CMF values for one symbol from
    stock_mf_daily.

    Extracted from page2's inline SQL block so the page layer has no
    direct SQLAlchemy dependency.

    Returns:
        (mfi, cmf) — either value may be None if no data exists.
    """
    query = text("""
        SELECT mfi, cmf
        FROM stock_mf_daily
        WHERE symbol = :sym
        ORDER BY date DESC
        LIMIT 1
    """)
    try:
        with db.engine.connect() as conn:
            row = conn.execute(query, {"sym": symbol}).fetchone()
        if row:
            mfi = float(row[0]) if row[0] is not None else None
            cmf = float(row[1]) if row[1] is not None else None
            return mfi, cmf
    except Exception:
        pass
    return None, None


def fetch_cmf_history(
    db, symbol: str, from_date: date_type
) -> pd.DataFrame:
    """
    Fetch CMF time-series for one symbol from stock_mf_daily.

    Returns DataFrame with columns [date (datetime), cmf].
    Empty DataFrame on error or no data.
    """
    query = text("""
        SELECT date, cmf
        FROM stock_mf_daily
        WHERE symbol = :sym
          AND date >= :from_dt
        ORDER BY date ASC
    """)
    try:
        with db.engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"sym": symbol, "from_dt": from_date})
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return pd.DataFrame()