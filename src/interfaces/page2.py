# src/interfaces/page2.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Price Chart + Performance Metrics
#
# OOP Structure  (mirrors page1.py pattern)
# ─────────────────────────────────────────────────────────────────────────────
# Page2                          ← top-level orchestrator
#   ├── _SymbolSelector          ← symbol + chart-type + period + signal controls
#   ├── _PriceDataLoader         ← all data fetching (prices, indicators, MF)
#   ├── _MetricsCalculator       ← pure metric computation (no UI)
#   ├── _PriceChartSection       ← price chart + MA labels
#   ├── _MetricCardsSection      ← 4×3 metric card grid
#   └── _CmfChartSection         ← standalone CMF histogram panel
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

from src.interfaces.helpers import (
    ALL_SIGNAL_TYPES,
    MA_COLORS,
    MA_PERIODS,
    PERIOD_DAYS,
    build_ma_series,
    build_markers,
    compute_adj_prices,
    fetch_cmf_history,
    fetch_latest_mfi_cmf,
    render_price_chart,
)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Symbol & View Controls
# ══════════════════════════════════════════════════════════════════════════════

class _SymbolSelector:
    """Renders the top control row and exposes the user's selections."""

    def __init__(self, symbols_df: pd.DataFrame) -> None:
        self._symbols_df = symbols_df

    def render(self) -> dict:
        """
        Render the control row and return a dict of user selections:
            symbol, chart_type, period, sig_filter
        """
        c1, c2, c3, c4 = st.columns([1.0, 1.2, 1.0, 1.8])

        sym_list   = self._symbols_df["symbol"].tolist()
        default_ix = sym_list.index("SSI") if "SSI" in sym_list else 0

        with c1:
            symbol = st.selectbox(
                "Mã chứng khoán", sym_list, index=default_ix, key="t2_sym"
            )
        with c2:
            chart_type = st.selectbox(
                "Loại biểu đồ",
                ["Nến (Candlestick)", "Đường (Close)"],
                key="t2_chart_type",
            )
        with c3:
            period = st.selectbox(
                "Chu kỳ",
                list(PERIOD_DAYS.keys()),
                index=3,
                key="t2_period",
            )
        with c4:
            sig_filter = st.multiselect(
                "Hiển thị tín hiệu",
                ALL_SIGNAL_TYPES,
                default=[],
                key="t2_sig_filter",
            )

        return {
            "symbol":     symbol,
            "chart_type": chart_type,
            "period":     period,
            "sig_filter": sig_filter,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Section: Data Loading  (all DB access in one place — no inline SQL in UI)
# ══════════════════════════════════════════════════════════════════════════════

class _PriceDataLoader:
    """
    Encapsulates every DB fetch for the price-chart page.
    No Streamlit calls — pure data layer.
    """

    def __init__(self, db) -> None:
        self._db = db

    def load_prices(
        self, symbol: str, start_date, today
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns (raw_df, adj_df).
        raw_df  — full history including warmup rows
        adj_df  — adjusted price columns + 'time' string
        """
        raw_df = self._db.fetch_price_with_warmup(symbol, start_date, today)
        if raw_df.empty:
            return pd.DataFrame(), pd.DataFrame()
        adj_df = compute_adj_prices(raw_df)
        return raw_df, adj_df

    def load_indicators(
        self, symbol: str, start_date, today
    ) -> pd.DataFrame:
        """Fetch vol_ma20 from technical_indicators."""
        return self._db.fetch_indicator_data(symbol, start_date, today)

    def load_signals(
        self, symbol: str, start_date, today, sig_filter: list[str]
    ) -> pd.DataFrame:
        """Fetch and filter trading signals for the chart markers."""
        if not sig_filter:
            return pd.DataFrame()
        df = self._db.fetch_signals_for_chart(symbol, start_date, today)
        if df.empty:
            return df
        return df[df["signal_type"].isin(sig_filter)]

    def load_mf_latest(
        self, symbol: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Fetch latest MFI and CMF from stock_mf_daily."""
        return fetch_latest_mfi_cmf(self._db, symbol)

    def load_cmf_history(self, symbol: str, start_date) -> pd.DataFrame:
        """Fetch CMF time-series for the standalone histogram panel."""
        return fetch_cmf_history(self._db, symbol, start_date)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Metric Computation  (pure logic, no Streamlit)
# ══════════════════════════════════════════════════════════════════════════════

class _MetricsCalculator:
    """
    Pure metric computation over an adjusted price DataFrame.
    All results exposed as named attributes after calling compute().
    """

    def __init__(self, price_df: pd.DataFrame) -> None:
        self._df = price_df

    def compute(self) -> dict:
        """Return dict of all computed metrics."""
        df  = self._df
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last

        chg     = float(last["adj_close"]) - float(prev["adj_close"])
        chg_pct = chg / float(prev["adj_close"]) * 100 if prev["adj_close"] else 0

        df = df.copy()
        df["ma5_close"]   = df["adj_close"].rolling(window=5, min_periods=1).mean()
        df["ma5_vol"]     = df["total_match_vol"].rolling(window=5, min_periods=1).mean()
        df["net_foreign"] = df["foreign_buy_vol_total"] - df["foreign_sell_vol_total"]
        df["ma5_foreign"] = df["net_foreign"].rolling(window=5, min_periods=1).mean()
        df["val_ma20"]    = df["total_match_val"].rolling(window=20, min_periods=1).mean()

        price_avg_chg = vol_avg_chg = fgn_avg_chg = 0.0
        if len(df) >= 6:
            price_avg_chg = _pct_change(df["ma5_close"].iloc[-1],  df["ma5_close"].iloc[-6])
            vol_avg_chg   = _pct_change(df["ma5_vol"].iloc[-1],    df["ma5_vol"].iloc[-6])
            fgn_avg_chg   = _pct_change(
                df["ma5_foreign"].iloc[-1], df["ma5_foreign"].iloc[-6], use_abs=True
            )

        val_ma20_last = df["val_ma20"].iloc[-1] if not df.empty else np.nan
        liquidity_ratio = (
            float(last["total_match_val"]) / val_ma20_last
            if pd.notna(val_ma20_last) and val_ma20_last > 0
            else np.nan
        )

        return {
            "last":            last,
            "chg":             chg,
            "chg_pct":         chg_pct,
            "price_avg_chg":   price_avg_chg,
            "vol_avg_chg":     vol_avg_chg,
            "fgn_avg_chg":     fgn_avg_chg,
            "liquidity_ratio": liquidity_ratio,
        }


def _pct_change(curr: float, prev: float, use_abs: bool = False) -> float:
    """Safe percentage change; use_abs=True uses |prev| in denominator."""
    denom = abs(prev) if use_abs else prev
    if not denom:
        return 0.0
    return (curr - prev) / denom * 100


# ══════════════════════════════════════════════════════════════════════════════
# Section: Price Chart
# ══════════════════════════════════════════════════════════════════════════════

class _PriceChartSection:
    """Renders the price chart (candlestick or line) + MA label strip."""

    # MA overlays always shown (MA5 and MA10 excluded as per original)
    _DEFAULT_MAS: list[str] = ["MA20", "MA50", "MA200"]

    def render(
        self,
        price_df: pd.DataFrame,
        raw_adj: pd.DataFrame,
        ma_series: list[dict],
        markers: list[dict],
        chart_type: str,
        chart_key: str,
    ) -> None:
        render_price_chart(price_df, ma_series, markers, chart_type, chart_key)
        self._render_ma_labels(raw_adj)

    def _render_ma_labels(self, raw_adj: pd.DataFrame) -> None:
        """Render the MA value chips below the chart."""
        parts = []
        for ma in self._DEFAULT_MAS:
            n    = MA_PERIODS[ma]
            vals = raw_adj["adj_close"].rolling(n, min_periods=n).mean().dropna()
            v    = f"{vals.iloc[-1]:,.2f}" if not vals.empty else "—"
            parts.append(
                f"<span style='background:{MA_COLORS[ma]};color:#fff;"
                f"padding:2px 9px;border-radius:10px;"
                f"font-size:12px;margin:2px'>{ma}: {v}</span>"
            )
        st.markdown(" ".join(parts), unsafe_allow_html=True)

    @classmethod
    def build_ma_overlays(cls, raw_adj: pd.DataFrame, start_date) -> list[dict]:
        return build_ma_series(raw_adj, cls._DEFAULT_MAS, start_date)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Metric Cards Grid
# ══════════════════════════════════════════════════════════════════════════════

class _MetricCardsSection:
    """Renders the 4-row × 3-column metric card grid on the right column."""

    def render(
        self,
        metrics: dict,
        mfi_val: Optional[float],
        cmf_val: Optional[float],
    ) -> None:
        st.markdown("<div style='padding-top: 10px;'></div>", unsafe_allow_html=True)
        last = metrics["last"]

        r1c1, r1c2, r1c3 = st.columns(3)
        r1c1.metric(
            "Đóng cửa",
            f"{last['adj_close']:,.0f}",
            f"{metrics['chg']:+.2f} ({metrics['chg_pct']:+.2f}%)",
        )
        r1c2.metric("Cao nhất",  f"{last['adj_high']:,.0f}")
        r1c3.metric("Thấp nhất", f"{last['adj_low']:,.0f}")

        r2c1, r2c2, r2c3 = st.columns(3)
        r2c1.metric("Khối lượng",     f"{last['total_match_vol'] / 1e6:.2f}M")
        r2c2.metric("Khối ngoại mua", f"{last['foreign_buy_vol_total'] / 1e6:.2f}M")
        r2c3.metric("Khối ngoại bán", f"{last['foreign_sell_vol_total'] / 1e6:.2f}M")

        r3c1, r3c2, r3c3 = st.columns(3)
        r3c1.metric("% tăng giảm giá TB 1 tuần",     f"{metrics['price_avg_chg']:+.2f}%")
        r3c2.metric("% tăng giảm khối lượng TB 1 tuần", f"{metrics['vol_avg_chg']:+.2f}%")
        r3c3.metric("% tăng giảm khối ngoại TB 1 tuần", f"{metrics['fgn_avg_chg']:+.2f}%")

        r4c1, r4c2, r4c3 = st.columns(3)
        r4c1.metric("MFI", f"{mfi_val:.1f}" if mfi_val is not None else "—")
        cmf_pct = cmf_val * 100 if cmf_val is not None else None
        r4c2.metric("CMF", f"{cmf_pct:.1f}%" if cmf_pct is not None else "—")
        liq = metrics["liquidity_ratio"]
        r4c3.metric(
            "Tỷ lệ thanh khoản",
            f"{liq:.2f}x" if pd.notna(liq) else "—",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Page2 — Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class Page2:
    """
    Orchestrates Tab 2 — Price Chart + Performance Metrics.

    Usage (from app entry-point):
        page = Page2(db, symbols_df, has_data)
        page.render()
    """

    def __init__(self, db, symbols_df: pd.DataFrame, has_data: bool) -> None:
        self._db         = db
        self._symbols_df = symbols_df
        self._has_data   = has_data

        self._selector    = _SymbolSelector(symbols_df)
        self._loader      = _PriceDataLoader(db)
        self._chart_sec   = _PriceChartSection()
        self._metrics_sec = _MetricCardsSection()

    def render(self) -> None:
        if not self._has_data:
            st.warning(
                "Chưa có dữ liệu. Hãy đồng bộ danh mục securities ở Tab Đồng bộ trước."
            )
            return

        # ── Controls ──────────────────────────────────────────────────────────
        sel        = self._selector.render()
        symbol     = sel["symbol"]
        chart_type = sel["chart_type"]
        period     = sel["period"]
        sig_filter = sel["sig_filter"]

        # ── Date range ────────────────────────────────────────────────────────
        today      = datetime.now().date()
        start_date = today - timedelta(days=PERIOD_DAYS[period])

        # ── Load data ─────────────────────────────────────────────────────────
        raw_df, raw_adj = self._loader.load_prices(symbol, start_date, today)
        if raw_df.empty:
            st.warning(f"Không có dữ liệu giá cho {symbol}.")
            return

        # Slice adj to visible range; merge vol_ma20
        price_df = raw_adj[raw_adj["trading_date"] >= start_date].reset_index(drop=True)
        ind_df   = self._loader.load_indicators(symbol, start_date, today)
        if not ind_df.empty:
            price_df = price_df.merge(ind_df, on="trading_date", how="left")
        else:
            price_df["vol_ma20"] = np.nan

        if price_df.empty:
            st.warning("Không đủ dữ liệu sau khi filter theo ngày.")
            return

        # ── Compute metrics & MF values ───────────────────────────────────────
        metrics          = _MetricsCalculator(price_df).compute()
        mfi_val, cmf_val = self._loader.load_mf_latest(symbol)

        # ── Stock name header ─────────────────────────────────────────────────
        info_row   = self._symbols_df[self._symbols_df["symbol"] == symbol]
        stock_name = info_row["stock_name"].values[0] if not info_row.empty else symbol
        st.markdown(
            f"#### {symbol} &nbsp;"
            f"<span style='font-size:14px;color:#6b7280'>{stock_name}</span>",
            unsafe_allow_html=True,
        )

        # ── Build chart overlays & markers ────────────────────────────────────
        ma_series = _PriceChartSection.build_ma_overlays(raw_adj, start_date)
        sig_df    = self._loader.load_signals(symbol, start_date, today, sig_filter)
        markers   = build_markers(sig_df) if not sig_df.empty else []

        chart_key = (
            f"c_{symbol}_{start_date}_{chart_type}"
            f"_{''.join(_PriceChartSection._DEFAULT_MAS)}"
            f"_{bool(sig_filter)}"
        )

        # ── Layout: chart (left) | metric cards (right) ───────────────────────
        col_left, col_right = st.columns([5, 4])

        with col_left:
            self._chart_sec.render(
                price_df, raw_adj, ma_series, markers, chart_type, chart_key
            )

        with col_right:
            self._metrics_sec.render(metrics, mfi_val, cmf_val)



# ── Backward-compatible module-level entry point ──────────────────────────────

def render(db, symbols_df: pd.DataFrame, has_data: bool) -> None:
    """Backward-compatible shim — delegates to Page2."""
    Page2(db, symbols_df, has_data).render()