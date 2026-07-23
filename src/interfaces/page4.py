# src/interfaces/page4.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 4 — Sector Rotation Dashboard
#
# OOP Structure  (mirrors page1.py pattern)
# ─────────────────────────────────────────────────────────────────────────────
# Page4                              ← top-level orchestrator
#   ├── _SectorDataLoader            ← all @st.cache_data DB fetches
#   ├── _SectorChartBuilder          ← pure data → TradingView series converters
#   ├── _SummaryMetricsSection       ← 6 KPI cards from daily ranking
#   ├── _RankingSection              ← daily ranking table
#   ├── _HeatmapSection              ← regime score heatmap
#   ├── _TrendChartSection           ← multi-sector score trend line chart
#   ├── _WeeklyRankingSection        ← weekly ranking table
#   └── _DrilldownSection            ← per-sector 6-tab deep-dive
#         ├── _CandleChartTab        ← synthetic OHLC candlestick
#         ├── _SymbolMatrixTab       ← single-date symbol matrix
#         ├── _MultiDateMatrixTab    ← pivot rows=symbol, cols=date
#         ├── _IndicatorsTab         ← sector-level factor time-series
#         ├── _BreadthTab            ← breadth participation charts
#         └── _MoneyFlowTab          ← 6-panel money flow analysis
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

from src.datapipe.handler import DatabaseHandler
from src.interfaces.helpers import (
    cell_style,
    delta_style,
    pct_change_style,
    regime_style,
    score_color_style,
)

logger = logging.getLogger(__name__)

# ── Shared chart theme ─────────────────────────────────────────────────────────
_BG   = {"type": "solid", "color": "#ffffff"}
_GRID = {"vertLines": {"color": "#f0f0f0"}, "horzLines": {"color": "#f0f0f0"}}


# ══════════════════════════════════════════════════════════════════════════════
# Data Loader  (all @st.cache_data calls in one place)
# ══════════════════════════════════════════════════════════════════════════════

class _SectorDataLoader:
    """
    Centralised, cached DB access for all sector-rotation data.
    Encapsulates the six @st.cache_data functions that were previously
    module-level, hard to test, and scattered through page4.
    """

    def __init__(self, db) -> None:
        self._db = db

    # Public methods intentionally thin — just delegate to cached statics
    # so the cache key includes the db identity (_db is an unhashable arg,
    # so we use the leading-underscore convention from the original code).

    @staticmethod
    @st.cache_data(ttl=300)
    def _fetch_score_history(
        _db, from_date: str, to_date: str, sectors: tuple[str, ...]
    ) -> pd.DataFrame:
        return DatabaseHandler.fetch_sector_score_history(
            _db, from_date, to_date, list(sectors)
        )

    @staticmethod
    @st.cache_data(ttl=300)
    def _fetch_heatmap(_db, from_date: str, to_date: str) -> pd.DataFrame:
        return DatabaseHandler.fetch_sector_heatmap(_db, from_date, to_date)

    @staticmethod
    @st.cache_data(ttl=300)
    def _fetch_sector_detail(
        _db, sector: str, from_date: str, to_date: str
    ) -> pd.DataFrame:
        return DatabaseHandler.fetch_sector_detail(_db, sector, from_date, to_date)

    @staticmethod
    @st.cache_data(ttl=300)
    def _fetch_symbol_matrix(_db, sector: str, date_str: str) -> pd.DataFrame:
        return DatabaseHandler.fetch_symbol_matrix(_db, sector, date_str)

    @staticmethod
    @st.cache_data(ttl=300)
    def _fetch_symbol_history(
        _db, sector: str, from_date: str, to_date: str
    ) -> pd.DataFrame:
        return DatabaseHandler.fetch_symbol_history(_db, sector, from_date, to_date)

    @staticmethod
    @st.cache_data(ttl=300)
    def _fetch_sector_ohlc(
        _db, sector: str, from_date: str, to_date: str
    ) -> pd.DataFrame:
        return DatabaseHandler.load_sector_ohlc(_db, sector, from_date, to_date)

    # ── Public accessors ──────────────────────────────────────────────────────
    def score_history(self, from_date, to_date, sectors: tuple) -> pd.DataFrame:
        return self._fetch_score_history(self._db, from_date, to_date, sectors)

    def heatmap(self, from_date, to_date) -> pd.DataFrame:
        return self._fetch_heatmap(self._db, from_date, to_date)

    def sector_detail(self, sector, from_date, to_date) -> pd.DataFrame:
        return self._fetch_sector_detail(self._db, sector, from_date, to_date)

    def symbol_matrix(self, sector, date_str) -> pd.DataFrame:
        return self._fetch_symbol_matrix(self._db, sector, date_str)

    def symbol_history(self, sector, from_date, to_date) -> pd.DataFrame:
        return self._fetch_symbol_history(self._db, sector, from_date, to_date)

    def sector_ohlc(self, sector, from_date, to_date) -> pd.DataFrame:
        return self._fetch_sector_ohlc(self._db, sector, from_date, to_date)

    def daily_ranking(self, scoring_svc, date_str, min_stocks) -> pd.DataFrame:
        return scoring_svc.get_latest_ranking(
            date=date_str, min_coverage=0.0, min_stocks=min_stocks
        )


# ══════════════════════════════════════════════════════════════════════════════
# Chart Builder  (pure data → TradingView series, no Streamlit calls)
# ══════════════════════════════════════════════════════════════════════════════

class _SectorChartBuilder:
    """
    Converts DataFrames into TradingView-compatible list[dict] series.
    All methods are static — no state, easy to test.
    """

    @staticmethod
    def base100(df: pd.DataFrame) -> pd.DataFrame:
        """Normalise OHLC so the first close == 100."""
        df = df.copy()
        if df.empty:
            return df
        base = df["close"].dropna().iloc[0]
        if base == 0:
            return df
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = (df[col] / base * 100).round(4)
        return df

    @staticmethod
    def candle_series(df: pd.DataFrame) -> list[dict]:
        return [
            {
                "time":  row["trading_date"].strftime("%Y-%m-%d"),
                "open":  round(float(row["open"]),  4),
                "high":  round(float(row["high"]),  4),
                "low":   round(float(row["low"]),   4),
                "close": round(float(row["close"]), 4),
            }
            for _, row in df.iterrows()
            if not any(pd.isna([row["open"], row["high"], row["low"], row["close"]]))
        ]

    @staticmethod
    def volume_series(df: pd.DataFrame) -> list[dict]:
        out, prev = [], None
        for _, row in df.iterrows():
            close = row.get("close")
            color = (
                "rgba(38,166,154,0.5)"
                if prev is None or (not pd.isna(close) and close >= prev)
                else "rgba(239,83,80,0.5)"
            )
            if not pd.isna(row.get("volume", float("nan"))):
                out.append({
                    "time":  row["trading_date"].strftime("%Y-%m-%d"),
                    "value": float(row["volume"]),
                    "color": color,
                })
            prev = close if not pd.isna(close) else prev
        return out

    @staticmethod
    def score_series(score_df: pd.DataFrame) -> list[dict]:
        return [
            {
                "time":  row["date"].strftime("%Y-%m-%d"),
                "value": round(float(row["total_score"]), 4),
            }
            for _, row in score_df.iterrows()
            if not pd.isna(row["total_score"])
        ]

    @staticmethod
    def bar_series(
        detail_df: pd.DataFrame,
        col: str,
        pos_color: str = "#16a34a",
        neg_color: str = "#dc2626",
    ) -> list[dict]:
        out = []
        for _, r in detail_df.iterrows():
            v = r.get(col)
            if pd.isna(v):
                continue
            out.append({
                "time":  r["date"].strftime("%Y-%m-%d"),
                "value": round(float(v), 4),
                "color": pos_color if float(v) >= 0 else neg_color,
            })
        return out

    @staticmethod
    def line_series(detail_df: pd.DataFrame, col: str) -> list[dict]:
        return [
            {"time": r["date"].strftime("%Y-%m-%d"), "value": round(float(r[col]), 4)}
            for _, r in detail_df.iterrows()
            if not pd.isna(r.get(col))
        ]

    @staticmethod
    def cumulative_line(detail_df: pd.DataFrame, col: str) -> list[dict]:
        vals = detail_df[col].fillna(0)
        cum  = vals.cumsum().round(4)
        return [
            {
                "time":  detail_df["date"].iloc[i].strftime("%Y-%m-%d"),
                "value": float(cum.iloc[i]),
            }
            for i in range(len(detail_df))
        ]

    @staticmethod
    def lw_chart(
        series_cfg: list[dict], height: int = 200, key: str = ""
    ) -> None:
        """Render a single-pane lightweight chart."""
        renderLightweightCharts([{
            "chart": {
                "height": height,
                "layout": {"background": _BG, "textColor": "#374151"},
                "grid":   _GRID,
                "crosshair": {"mode": 1},
                "timeScale":      {"borderColor": "#e5e7eb", "rightOffset": 4},
                "rightPriceScale": {"borderColor": "#e5e7eb"},
            },
            "series": series_cfg,
        }], key=key)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Summary Metrics
# ══════════════════════════════════════════════════════════════════════════════

class _SummaryMetricsSection:
    """Six KPI cards derived from the latest daily ranking DataFrame."""

    def render(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        n_expansion   = int((df["regime"] == "Expansion").sum())
        n_rotation    = int((df["regime"] == "EarlyRotation").sum())
        n_contraction = int((df["regime"] == "Contraction").sum())
        avg_score     = df["total_score"].mean()
        top_row       = df.loc[df["total_score"].idxmax()]
        bot_row       = df.loc[df["total_score"].idxmin()]

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("🟢 Expansion",      n_expansion)
        c2.metric("🔵 Early Rotation", n_rotation)
        c3.metric("🔴 Contraction",    n_contraction)
        c4.metric("TB Score",          f"{avg_score:+.3f}")
        c5.metric("🏆 Mạnh nhất",
                  top_row["sector_name"], f"{top_row['total_score']:+.3f}")
        c6.metric("⚠️ Yếu nhất",
                  bot_row["sector_name"], f"{bot_row['total_score']:+.3f}")


# ══════════════════════════════════════════════════════════════════════════════
# Section: Ranking Table
# ══════════════════════════════════════════════════════════════════════════════

class _RankingSection:
    """Daily sector ranking styled table."""

    _COLUMN_MAP = {
        "rank": "Hạng", "sector_name": "Ngành", "total_score": "Score",
        "inst_score": "Inst", "breadth_score": "Breadth", "regime": "Chế độ",
        "score_delta_1d": "Δ1D", "score_delta_5d": "Δ5D",
        "n_stocks": "CP", "coverage_pct": "Coverage",
    }

    def render(self, df: pd.DataFrame) -> None:
        if df.empty:
            st.info("Không có dữ liệu ranking.")
            return
        avail = [c for c in self._COLUMN_MAP if c in df.columns]
        disp  = df[avail].copy().rename(columns=self._COLUMN_MAP)

        fmt = {k: v for k, v in {
            "Score":    "{:+.3f}", "Inst": "{:+.3f}", "Breadth": "{:+.3f}",
            "Δ1D":      "{:+.3f}", "Δ5D":  "{:+.3f}", "Coverage": "{:.0%}",
        }.items() if k in disp.columns}

        styled = disp.style.format(fmt, na_rep="—")
        for col, fn in [
            ("Score",   score_color_style),
            ("Chế độ",  regime_style),
            ("Δ1D",     delta_style),
            ("Δ5D",     delta_style),
        ]:
            if col in disp.columns:
                styled = styled.map(fn, subset=[col])

        st.dataframe(styled, width="stretch", height=440)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Regime Heatmap
# ══════════════════════════════════════════════════════════════════════════════

class _HeatmapSection:
    """Sector × date score heatmap."""

    def render(self, df: pd.DataFrame, n_days: int = 20) -> None:
        if df.empty:
            st.info("Không có dữ liệu heatmap.")
            return
        last_dates = sorted(df["date"].unique())[-n_days:]
        heat = df[df["date"].isin(last_dates)].copy()
        pivot = heat.pivot_table(
            index="sector_name", columns="date",
            values="total_score", aggfunc="first",
        )
        pivot.columns = [str(c) for c in pivot.columns]
        styled = pivot.style.map(score_color_style).format("{:+.2f}", na_rep="—")
        st.dataframe(styled, width="stretch", height=400)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Score trend Chart
# ══════════════════════════════════════════════════════════════════════════════

class _TrendChartSection:
    """Multi-sector score trend line chart."""

    def render(
        self, history_df: pd.DataFrame, selected_sectors: list[str]
    ) -> None:
        if history_df.empty:
            st.info("Không có dữ liệu lịch sử score.")
            return
        pivot = history_df.pivot_table(
            index="date", columns="sector_name",
            values="total_score", aggfunc="first",
        )
        if selected_sectors:
            cols = [c for c in selected_sectors if c in pivot.columns]
            pivot = pivot[cols]
        st.line_chart(pivot, width="stretch", height=280)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Weekly Ranking
# ══════════════════════════════════════════════════════════════════════════════

class _WeeklyRankingSection:
    """Weekly sector ranking styled table."""

    _COLUMN_MAP = {
        "year_week": "Tuần", "date_from": "Từ", "date_to": "Đến",
        "sector_name": "Ngành", "rank": "Hạng", "total_score": "Score",
        "regime": "Chế độ", "score_delta_1w": "Δ1W",
        "n_trading_days": "Ngày GD",
    }

    def render(self, scoring_svc, year_week) -> None:
        df = scoring_svc.get_weekly_ranking(year_week=year_week)
        if df.empty:
            st.info("Không có dữ liệu weekly ranking.")
            return

        avail = [c for c in self._COLUMN_MAP if c in df.columns]
        disp  = df[avail].copy().rename(columns=self._COLUMN_MAP)

        fmt = {k: v for k, v in {"Score": "{:+.3f}", "Δ1W": "{:+.3f}"}.items()
               if k in disp.columns}
        styled = disp.style.format(fmt, na_rep="—")
        for col, fn in [
            ("Score",  score_color_style),
            ("Chế độ", regime_style),
            ("Δ1W",    delta_style),
        ]:
            if col in disp.columns:
                styled = styled.map(fn, subset=[col])

        st.dataframe(styled, width="stretch", height=380)


# ══════════════════════════════════════════════════════════════════════════════
# Drill-down Tabs
# ══════════════════════════════════════════════════════════════════════════════

class _CandleChartTab:
    """Tab 1: synthetic sector OHLC candlestick + optional MF score pane."""

    def __init__(self, loader: _SectorDataLoader, builder: _SectorChartBuilder) -> None:
        self._loader  = loader
        self._builder = builder

    def render(self, sector: str, from_date: str, to_date: str) -> None:
        st.caption(
            "OHLC tổng hợp = bình quân **gia quyền giá trị giao dịch** "
            "của tất cả cổ phiếu trong ngành · Giá đã điều chỉnh."
        )

        cc1, cc2, _ = st.columns([1, 1, 1])
        with cc1:
            normalise = st.checkbox(
                "Base-100 (so sánh tương đối)", value=True,
                key=f"sc_norm_{sector}",
            )
        with cc2:
            show_score = st.checkbox(
                "Hiện MF Score pane", value=True,
                key=f"sc_score_{sector}",
            )

        price_df = self._loader.sector_ohlc(sector, from_date, to_date)
        score_df = self._loader.sector_detail(sector, from_date, to_date)

        self._render_metric_cards(price_df, score_df, normalise)
        self._render_chart(price_df, score_df, normalise, show_score, sector, from_date)
        self._render_stats(price_df)

    def _render_metric_cards(
        self, price_df: pd.DataFrame, score_df: pd.DataFrame, normalise: bool
    ) -> None:
        if price_df.empty:
            return
        disp_df = _SectorChartBuilder.base100(price_df) if normalise else price_df
        last    = disp_df.iloc[-1]
        prev    = disp_df.iloc[-2] if len(disp_df) > 1 else last
        close   = float(last["close"])
        pclose  = float(prev["close"])
        chg     = close - pclose
        chg_pct = chg / pclose * 100 if pclose else 0

        regime_val = score_val = "—"
        if not score_df.empty and "regime" in score_df.columns:
            ls = score_df.iloc[-1]
            regime_val = ls.get("regime") or "—"
            sv = ls.get("total_score")
            score_val = f"{sv:+.3f}" if sv is not None and not pd.isna(sv) else "—"

        lbl = "Base-100" if normalise else "Giá adj"
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric(f"Đóng ({lbl})",  f"{close:,.2f}", f"{chg:+.2f} ({chg_pct:+.1f}%)")
        m2.metric("Cao nhất",       f"{float(last['high']):,.2f}")
        m3.metric("Thấp nhất",      f"{float(last['low']):,.2f}")
        m4.metric("KL (cổ phiếu)", f"{float(last['volume'])/1e6:.2f}M")
        m5.metric("MF Score",      score_val)
        m6.metric("Chế độ",        regime_val)

    def _render_chart(
        self,
        price_df: pd.DataFrame,
        score_df: pd.DataFrame,
        normalise: bool,
        show_score: bool,
        sector: str,
        from_date: str,
    ) -> None:
        if price_df.empty:
            st.info("Không có dữ liệu giá tổng hợp cho ngành này.")
            return

        df = _SectorChartBuilder.base100(price_df) if normalise else price_df.copy()

        charts = [
            {
                "chart": {
                    "height": 360,
                    "layout": {"background": _BG, "textColor": "#333"},
                    "grid": _GRID, "crosshair": {"mode": 1},
                    "timeScale": {"borderColor": "#d1d5db", "rightOffset": 8},
                    "rightPriceScale": {"borderColor": "#d1d5db"},
                },
                "series": [{
                    "type": "Candlestick",
                    "data": _SectorChartBuilder.candle_series(df),
                    "options": {
                        "upColor":         "#26a69a", "downColor":       "#ef5350",
                        "borderUpColor":   "#26a69a", "borderDownColor": "#ef5350",
                        "wickUpColor":     "#26a69a", "wickDownColor":   "#ef5350",
                        "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
                    },
                }],
            },
            {
                "chart": {
                    "height": 80,
                    "layout": {"background": _BG, "textColor": "#333"},
                    "grid": _GRID,
                    "timeScale": {"borderColor": "#d1d5db", "visible": False},
                    "rightPriceScale": {
                        "borderColor": "#d1d5db",
                        "scaleMargins": {"top": 0.05, "bottom": 0},
                    },
                },
                "series": [{
                    "type": "Histogram",
                    "data": _SectorChartBuilder.volume_series(df),
                    "options": {"priceFormat": {"type": "volume"}, "priceScaleId": ""},
                }],
            },
        ]

        if show_score and not score_df.empty:
            sd = _SectorChartBuilder.score_series(
                score_df[["date", "total_score"]].dropna(subset=["total_score"])
            )
            if sd:
                charts.append({
                    "chart": {
                        "height": 70,
                        "layout": {"background": _BG, "textColor": "#333"},
                        "grid": _GRID,
                        "timeScale": {"borderColor": "#d1d5db", "visible": False},
                        "rightPriceScale": {
                            "borderColor": "#d1d5db",
                            "scaleMargins": {"top": 0.1, "bottom": 0.1},
                        },
                    },
                    "series": [{
                        "type": "Line",
                        "data": sd,
                        "options": {
                            "color":            "#8b5cf6",
                            "lineWidth":        2,
                            "priceLineVisible": False,
                            "lastValueVisible": True,
                            "title":            "MF Score",
                            "priceFormat": {"type": "price", "precision": 3, "minMove": 0.001},
                        },
                    }],
                })

        renderLightweightCharts(
            charts,
            key=f"sc_candle_{sector}_{from_date}_{normalise}_{show_score}",
        )

    def _render_stats(self, price_df: pd.DataFrame) -> None:
        if price_df.empty:
            return
        close_s = price_df["close"]
        pct_chg = close_s.pct_change().dropna()
        n       = len(price_df)
        with st.expander("📊 Thống kê hiệu suất"):
            sa, sb, sc, sd = st.columns(4)
            sa.metric("Số phiên",           n)
            sb.metric("Biến động ngày (TB)", f"{pct_chg.mean()*100:+.3f}%")
            sc.metric("Ngày tăng mạnh nhất", f"{pct_chg.max()*100:+.2f}%")
            sd.metric("Ngày giảm mạnh nhất", f"{pct_chg.min()*100:+.2f}%")

            r_cols = st.columns(4)
            for i, (label, periods) in enumerate([
                ("5 phiên", 5), ("20 phiên", 20), ("60 phiên", 60), ("120 phiên", 120)
            ]):
                if n > periods:
                    r = (close_s.iloc[-1] / close_s.iloc[-periods] - 1) * 100
                    r_cols[i].metric(f"Return {label}", f"{r:+.2f}%")


class _SymbolMatrixTab:
    """Tab 2: single-date symbol matrix — all stocks in the sector."""

    _COLUMN_MAP = {
        "symbol": "Mã", "stock_name": "Tên", "per_price_change": "% Today",
        "mfi": "MFI", "cmf": "CMF", "rvol": "RVOL",
        "nmf_zscore": "NMF_z", "nmf_accel": "Accel", "nff_zscore": "NFF_z",
        "trading_value": "GT (tỷ)",
    }
    _FMT = {
        "% Today": "{:+.2f}%", "MFI": "{:.1f}", "CMF": "{:+.3f}",
        "RVOL": "{:.2f}", "NMF_z": "{:+.3f}", "Accel": "{:+.3f}",
        "NFF_z": "{:+.3f}", "GT (tỷ)": "{:.1f}",
    }

    def __init__(self, loader: _SectorDataLoader) -> None:
        self._loader = loader

    def render(self, sector: str, date_str: str) -> None:
        st.caption(
            "Tất cả cổ phiếu trong ngành. Màu = cường độ tín hiệu. "
            "Sắp xếp theo giá trị giao dịch giảm dần."
        )
        df = self._loader.symbol_matrix(sector, date_str)
        if df.empty:
            st.info(f"Không có dữ liệu cổ phiếu cho {sector} ngày {date_str}.")
            return
        self._render_table(df)

    def _render_table(self, df: pd.DataFrame) -> None:
        disp = df.rename(columns=self._COLUMN_MAP).copy()
        if "GT (tỷ)" in disp.columns:
            disp["GT (tỷ)"] = (disp["GT (tỷ)"] / 1e9).round(2)

        col_order = [c for c in self._COLUMN_MAP.values() if c in disp.columns]
        disp      = disp[col_order].reset_index(drop=True)
        fmt       = {k: v for k, v in self._FMT.items() if k in disp.columns}

        styled = disp.style.format(fmt, na_rep="—")
        if "% Today" in disp.columns:
            styled = styled.map(pct_change_style, subset=["% Today"])
        for col in ["CMF", "MFI", "RVOL", "NMF_z", "Accel", "NFF_z"]:
            if col in disp.columns:
                styled = styled.map(cell_style(col), subset=[col])
        if "GT (tỷ)" in disp.columns:
            styled = styled.background_gradient(subset=["GT (tỷ)"], cmap="Blues", low=0.2, high=0.8)

        st.dataframe(styled, width="stretch", height=min(40 * len(disp) + 40, 800))
        st.caption(
            f"**{len(disp)} cổ phiếu** | "
            "**% ngày**: tăng/giảm so với phiên trước (±7% = trần/sàn) | "
            "🟢 Tín hiệu dương 🔴 Tín hiệu âm ⬜ Trung tính"
        )


class _MultiDateMatrixTab:
    """Tab 3: pivot rows=symbol, cols=date for one indicator."""

    _METRIC_MAP = {
        "CMF": "cmf", "MFI": "mfi", "RVOL": "rvol",
        "NMF_z": "nmf_zscore", "Accel": "nmf_accel", "NFF_z": "nff_zscore",
    }

    def __init__(self, loader: _SectorDataLoader) -> None:
        self._loader = loader

    def render(self, sector: str, from_date: str, to_date: str) -> None:
        mc1, mc2 = st.columns([1, 3])
        with mc1:
            metric = st.selectbox(
                "Chỉ báo", list(self._METRIC_MAP), index=0, key="sr_multi_metric"
            )
        with mc2:
            st.caption(
                "Rows = cổ phiếu, Columns = ngày giao dịch. "
                "Phát hiện xu hướng tích lũy / phân phối liên tục."
            )

        col = self._METRIC_MAP[metric]
        df  = self._loader.symbol_history(sector, from_date, to_date)
        if df.empty:
            st.info(f"Không có dữ liệu lịch sử cho {sector}.")
            return

        sym_order = (
            df.groupby("symbol")["trading_value"]
            .mean().sort_values(ascending=False).index.tolist()
        )
        pivot = df.pivot_table(
            index="symbol", columns="date", values=col, aggfunc="first"
        )
        pivot = pivot.reindex([s for s in sym_order if s in pivot.index])
        pivot.columns = [str(c) for c in pivot.columns]

        fmt_str = "{:+.2f}" if col != "mfi" else "{:.1f}"
        styled  = (
            pivot.style
            .map(cell_style(metric))
            .format(fmt_str, na_rep="—")
        )

        n = len(pivot)
        st.dataframe(styled, width="stretch", height=min(40 * n + 40, 800))
        st.caption(
            f"**{n} cổ phiếu × {len(pivot.columns)} ngày** | "
            "Sắp xếp theo giá trị giao dịch trung bình (lớn nhất ở trên)"
        )


class _IndicatorsTab:
    """Tab 4: sector-level indicator time-series line charts."""

    def __init__(self, loader: _SectorDataLoader) -> None:
        self._loader = loader

    def render(self, sector: str, from_date: str, to_date: str) -> None:
        detail_df = self._loader.sector_detail(sector, from_date, to_date)
        if detail_df.empty:
            st.info("Không có dữ liệu sector factor.")
            return

        c_left, c_right = st.columns(2)
        with c_left:
            st.markdown("**CMF (Weighted vs Median)**")
            st.line_chart(
                detail_df.set_index("date")[["weighted_cmf", "median_cmf"]],
                height=180, width="stretch",
            )
            st.markdown("**MFI (Weighted vs Median)**")
            st.line_chart(
                detail_df.set_index("date")[["weighted_mfi", "median_mfi"]],
                height=180, width="stretch",
            )
            st.markdown("**RVOL (Weighted)**")
            st.line_chart(
                detail_df.set_index("date")[["weighted_rvol"]],
                height=160, width="stretch",
            )
        with c_right:
            st.markdown("**NMF Z-Score (Weighted)**")
            st.line_chart(
                detail_df.set_index("date")[["weighted_nmf_z"]],
                height=180, width="stretch",
            )
            st.markdown("**NMF Acceleration (Weighted)**")
            st.line_chart(
                detail_df.set_index("date")[["weighted_accel"]],
                height=180, width="stretch",
            )
            st.markdown("**NFF Z-Score (Net Foreign Flow)**")
            st.line_chart(
                detail_df.set_index("date")[["weighted_nff_z"]],
                height=160, width="stretch",
            )


class _BreadthTab:
    """Tab 5: breadth participation percentage charts."""

    def __init__(self, loader: _SectorDataLoader) -> None:
        self._loader = loader

    def render(self, sector: str, from_date: str, to_date: str) -> None:
        detail_df = self._loader.sector_detail(sector, from_date, to_date)
        if detail_df.empty:
            st.info("Không có dữ liệu.")
            return

        breadth_cols = [c for c in [
            "breadth_cmf_positive", "breadth_mfi_above_50",
            "breadth_accel_above_1", "breadth_nff_positive",
        ] if c in detail_df.columns]

        if breadth_cols:
            b_df = detail_df.set_index("date")[breadth_cols].rename(columns={
                "breadth_cmf_positive":  "CMF>0",
                "breadth_mfi_above_50":  "MFI>50",
                "breadth_accel_above_1": "Accel>1",
                "breadth_nff_positive":  "NFF>0",
            })
            st.markdown("**Breadth Participation (% stocks meeting threshold)**")
            st.line_chart(b_df, height=240, width="stretch")

        if "n_stocks" in detail_df.columns and "coverage_pct" in detail_df.columns:
            st.markdown("**Số cổ phiếu & Coverage**")
            st.line_chart(
                detail_df.set_index("date")[["n_stocks", "coverage_pct"]],
                height=160, width="stretch",
            )


class _MoneyFlowTab:
    """Tab 6: 6-panel money flow analysis (CMF, NMF-z, CumCMF, NFF-z, MFI, Accel)."""

    def __init__(self, loader: _SectorDataLoader, builder: _SectorChartBuilder) -> None:
        self._loader  = loader
        self._builder = builder

    def render(self, sector: str, from_date: str, to_date: str) -> None:
        detail_df = self._loader.sector_detail(sector, from_date, to_date)
        if detail_df.empty:
            st.info("Không có dữ liệu money flow cho ngành này.")
            return

        detail_df = detail_df.copy()
        detail_df["date"] = pd.to_datetime(detail_df["date"])
        detail_df = detail_df.sort_values("date").reset_index(drop=True)

        self._render_snapshot_cards(detail_df)
        st.markdown("---")
        self._render_cmf_nmfz_row(detail_df, sector)
        self._render_cum_nff_row(detail_df, sector)
        self._render_mfi_accel_row(detail_df, sector)
        self._render_stock_breakdown(sector, to_date)

    def _snapshot_card(
        self, detail_df: pd.DataFrame, label: str, col: str,
        fmt: str = "{:+.3f}"
    ) -> None:
        last = detail_df.iloc[-1]
        prev = detail_df.iloc[-2] if len(detail_df) > 1 else last
        v = last.get(col)
        p = prev.get(col)
        if pd.isna(v):
            st.metric(label, "—")
            return
        delta_str = f"{float(v) - float(p):+.3f}" if not pd.isna(p) else None
        st.metric(label, fmt.format(float(v)), delta_str)

    def _render_snapshot_cards(self, detail_df: pd.DataFrame) -> None:
        st.markdown(
            "<div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;"
            "padding:12px 16px;margin-bottom:12px'>"
            "<span style='font-size:12px;font-weight:700;color:#64748b;letter-spacing:.05em'>"
            "MONEY FLOW SNAPSHOT — LATEST SESSION</span></div>",
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        with c1: self._snapshot_card(detail_df, "CMF (Weighted)", "weighted_cmf")
        with c2: self._snapshot_card(detail_df, "NMF Z-Score",    "weighted_nmf_z")
        with c3: self._snapshot_card(detail_df, "NFF Z-Score",    "weighted_nff_z")
        with c4: self._snapshot_card(detail_df, "MFI (Weighted)", "weighted_mfi",  fmt="{:.1f}")
        with c5: self._snapshot_card(detail_df, "RVOL",           "weighted_rvol", fmt="{:.2f}")
        with c6: self._snapshot_card(detail_df, "Accel",          "weighted_accel")

        # Flow direction badge
        cmf_val  = float(detail_df.iloc[-1].get("weighted_cmf", 0) or 0)
        nmf_val  = float(detail_df.iloc[-1].get("weighted_nmf_z", 0) or 0)
        flow     = (cmf_val + nmf_val / 3) / 2
        if flow > 0.05:
            clr, bg, txt = "#16a34a", "#dcfce7", "▲ NET INFLOW"
        elif flow < -0.05:
            clr, bg, txt = "#dc2626", "#fee2e2", "▼ NET OUTFLOW"
        else:
            clr, bg, txt = "#6b7280", "#f3f4f6", "◆ NEUTRAL"
        st.markdown(
            f"<div style='display:inline-block;background:{bg};color:{clr};"
            f"border:1.5px solid {clr};border-radius:6px;padding:4px 14px;"
            f"font-size:13px;font-weight:700;letter-spacing:.06em;margin-bottom:8px'>"
            f"{txt}</div>",
            unsafe_allow_html=True,
        )

    def _render_cmf_nmfz_row(self, detail_df: pd.DataFrame, sector: str) -> None:
        col_l, col_r = st.columns(2)
        b = self._builder
        with col_l:
            st.markdown("**Chaikin Money Flow (CMF)**")
            cmf_data = b.bar_series(detail_df, "weighted_cmf", "#16a34a", "#dc2626")
            if cmf_data:
                b.lw_chart([
                    {"type": "Histogram", "data": cmf_data,
                     "options": {"priceFormat": {"type": "price", "precision": 3, "minMove": 0.001},
                                 "priceScaleId": "right", "base": 0}},
                    {"type": "Line",
                     "data": [{"time": cmf_data[0]["time"], "value": 0},
                               {"time": cmf_data[-1]["time"], "value": 0}],
                     "options": {"color": "#94a3b8", "lineWidth": 1,
                                 "priceLineVisible": False, "lastValueVisible": False,
                                 "lineStyle": 2}},
                ], height=220, key=f"mf_cmf_{sector}")
        with col_r:
            st.markdown("**NMF Z-Score (Institutional Net Money Flow)**")
            nmf_data = b.bar_series(detail_df, "weighted_nmf_z", "#2563eb", "#f59e0b")
            if nmf_data:
                b.lw_chart([
                    {"type": "Histogram", "data": nmf_data,
                     "options": {"priceFormat": {"type": "price", "precision": 3, "minMove": 0.001},
                                 "priceScaleId": "right", "base": 0}},
                ], height=220, key=f"mf_nmfz_{sector}")

    def _render_cum_nff_row(self, detail_df: pd.DataFrame, sector: str) -> None:
        col_l, col_r = st.columns(2)
        b = self._builder
        with col_l:
            st.markdown("**Cumulative CMF**")
            cum_data = b.cumulative_line(detail_df, "weighted_cmf")
            if cum_data:
                last_cum   = cum_data[-1]["value"] if cum_data else 0
                line_color = "#16a34a" if last_cum >= 0 else "#dc2626"
                b.lw_chart([
                    {"type": "Area", "data": cum_data,
                     "options": {"topColor": f"{line_color}33",
                                 "bottomColor": f"{line_color}05",
                                 "lineColor": line_color, "lineWidth": 2,
                                 "priceLineVisible": False, "lastValueVisible": True,
                                 "title": "Cum CMF",
                                 "priceFormat": {"type": "price", "precision": 3, "minMove": 0.001}}},
                ], height=220, key=f"mf_cumcmf_{sector}")
        with col_r:
            st.markdown("**NFF Z-Score (Net Foreign Flow)**")
            nff_data = b.bar_series(detail_df, "weighted_nff_z", "#0891b2", "#be185d")
            if nff_data:
                b.lw_chart([
                    {"type": "Histogram", "data": nff_data,
                     "options": {"priceFormat": {"type": "price", "precision": 3, "minMove": 0.001},
                                 "priceScaleId": "right", "base": 0}},
                ], height=220, key=f"mf_nffz_{sector}")

    def _render_mfi_accel_row(self, detail_df: pd.DataFrame, sector: str) -> None:
        col_l, col_r = st.columns(2)
        b = self._builder
        with col_l:
            st.markdown("**Money Flow Index (MFI)**")
            mfi_data = b.line_series(detail_df, "weighted_mfi")
            if mfi_data:
                b.lw_chart([
                    {"type": "Line", "data": mfi_data,
                     "options": {"color": "#7c3aed", "lineWidth": 2,
                                 "priceLineVisible": False, "lastValueVisible": True,
                                 "title": "MFI",
                                 "priceFormat": {"type": "price", "precision": 1, "minMove": 0.1}}},
                    {"type": "Line",
                     "data": [{"time": mfi_data[0]["time"], "value": 50},
                               {"time": mfi_data[-1]["time"], "value": 50}],
                     "options": {"color": "#94a3b8", "lineWidth": 1,
                                 "priceLineVisible": False, "lastValueVisible": False,
                                 "lineStyle": 2}},
                ], height=200, key=f"mf_mfi_{sector}")
        with col_r:
            st.markdown("**NMF Acceleration**")
            accel_data = b.bar_series(detail_df, "weighted_accel", "#10b981", "#f97316")
            if accel_data:
                b.lw_chart([
                    {"type": "Histogram", "data": accel_data,
                     "options": {"priceFormat": {"type": "price", "precision": 3, "minMove": 0.001},
                                 "priceScaleId": "right", "base": 0}},
                ], height=200, key=f"mf_accel_{sector}")

    def _render_stock_breakdown(self, sector: str, to_date: str) -> None:
        st.markdown("---")
        st.markdown("**📊 Phân tích cổ phiếu trong ngành — phiên gần nhất**")
        sym_df = self._loader.symbol_matrix(sector, to_date)
        if sym_df.empty:
            st.info(f"Không có dữ liệu cổ phiếu cho {sector} ngày {to_date}.")
            return

        sym_df = sym_df.copy()

        def _norm(s: pd.Series) -> pd.Series:
            mn, mx = s.min(), s.max()
            if mx == mn:
                return pd.Series(0.0, index=s.index)
            return (s - mn) / (mx - mn) * 2 - 1

        score_cols = [c for c in ["cmf", "nmf_zscore", "nff_zscore"] if c in sym_df.columns]
        sym_df["flow_score"] = (
            sum(_norm(sym_df[c].fillna(0)) for c in score_cols) / len(score_cols)
            if score_cols else 0.0
        )
        sym_df = sym_df.sort_values("flow_score", ascending=False).reset_index(drop=True)

        top_n      = 10
        inflow_df  = sym_df.head(top_n)
        outflow_df = sym_df.tail(top_n).sort_values("flow_score")

        col_in, col_out = st.columns(2)
        display_cols_map = {
            "symbol": "Mã", "stock_name": "Tên",
            "cmf": "CMF", "nmf_zscore": "NMF_z", "nff_zscore": "NFF_z",
            "mfi": "MFI", "flow_score": "Flow Score", "trading_value": "GT (tỷ)",
        }
        fmt = {
            "CMF": "{:+.3f}", "NMF_z": "{:+.3f}", "NFF_z": "{:+.3f}",
            "MFI": "{:.1f}",  "Flow Score": "{:+.3f}", "GT (tỷ)": "{:.1f}",
        }

        def _flow_table(df_: pd.DataFrame, label: str, color: str) -> None:
            avail = {k: v for k, v in display_cols_map.items() if k in df_.columns}
            disp  = df_[list(avail.keys())].rename(columns=avail).copy()
            if "GT (tỷ)" in disp.columns:
                disp["GT (tỷ)"] = (disp["GT (tỷ)"] / 1e9).round(2)
            _fmt = {k: v for k, v in fmt.items() if k in disp.columns}
            st.markdown(
                f"<div style='font-size:13px;font-weight:700;color:{color};"
                f"margin-bottom:4px'>{label}</div>",
                unsafe_allow_html=True,
            )
            st.dataframe(
                disp.style.format(_fmt, na_rep="—"),
                width="stretch", height=min(40 * len(disp) + 40, 420),
            )

        with col_in:
            _flow_table(inflow_df,  f"🟢 Top {top_n} — Dòng tiền VÀO mạnh nhất", "#16a34a")
        with col_out:
            _flow_table(outflow_df, f"🔴 Top {top_n} — Dòng tiền RA mạnh nhất",  "#dc2626")


# ══════════════════════════════════════════════════════════════════════════════
# Section: Drilldown (aggregates the 6 tabs)
# ══════════════════════════════════════════════════════════════════════════════

class _DrilldownSection:
    """Sector drill-down: 6-tab deep-dive wrapper."""

    def __init__(self, loader: _SectorDataLoader, builder: _SectorChartBuilder) -> None:
        self._candle   = _CandleChartTab(loader, builder)
        self._matrix   = _SymbolMatrixTab(loader)
        self._multi    = _MultiDateMatrixTab(loader)
        self._ind      = _IndicatorsTab(loader)
        self._breadth  = _BreadthTab(loader)
        self._mf       = _MoneyFlowTab(loader, builder)

    def render(self, sector: str, from_date: str, to_date: str) -> None:
        tab_candle, tab_matrix, tab_multi, tab_ind, tab_breadth, tab_mf = st.tabs([
            "🕯️ Candle Chart",
            "📋 Symbol Matrix",
            "📆 Multi-date Matrix",
            "📈 Indicators",
            "🧩 Breadth",
            "💰 Money Flow",
        ])

        with tab_candle:
            self._candle.render(sector, from_date, to_date)
        with tab_matrix:
            self._matrix.render(sector, to_date)
        with tab_multi:
            self._multi.render(sector, from_date, to_date)
        with tab_ind:
            self._ind.render(sector, from_date, to_date)
        with tab_breadth:
            self._breadth.render(sector, from_date, to_date)
        with tab_mf:
            self._mf.render(sector, from_date, to_date)


# ══════════════════════════════════════════════════════════════════════════════
# Page4 — Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class Page4:
    """
    Orchestrates Tab 4 — Sector Rotation Dashboard.

    Usage (from app entry-point):
        page = Page4(db, scoring_svc)
        page.render()
    """

    def __init__(self, db, scoring_svc) -> None:
        self._scoring_svc = scoring_svc

        self._loader   = _SectorDataLoader(db)
        self._builder  = _SectorChartBuilder()

        self._summary  = _SummaryMetricsSection()
        self._ranking  = _RankingSection()
        self._heatmap  = _HeatmapSection()
        self._trend    = _TrendChartSection()
        self._weekly   = _WeeklyRankingSection()
        self._drill    = _DrilldownSection(self._loader, self._builder)

    def render(self) -> None:
        st.subheader("🔄 Sector Rotation — Money Flow Dashboard")

        # ── Controls ──────────────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns([1, 1, 1.5, 1])
        with c1:
            view_date = st.date_input(
                "Ngày xem (trống = mới nhất)", value=None, key="sr_date"
            )
        with c2:
            min_stocks = st.number_input(
                "Số CP tối thiểu", min_value=1, max_value=20, value=3,
                step=1, key="sr_min_stocks",
            )
        with c3:
            trend_period = st.selectbox(
                "Kỳ trend chart",
                ["1 tháng", "3 tháng", "6 tháng", "1 năm"],
                index=1, key="sr_trend_period",
            )
        with c4:
            heatmap_days = st.number_input(
                "Số ngày heatmap", min_value=5, max_value=60, value=20,
                step=5, key="sr_heatmap_days",
            )

        # ── Load daily ranking ─────────────────────────────────────────────────
        date_str = view_date.strftime("%Y-%m-%d") if view_date else None
        daily_df = self._loader.daily_ranking(self._scoring_svc, date_str, min_stocks)

        if daily_df.empty:
            st.warning(
                "Chưa có dữ liệu sector ranking. "
                "Hãy chạy MFService → SectorAggregationService → SectorScoringService trước."
            )
            return

        display_date = (
            str(daily_df["date"].iloc[0]) if "date" in daily_df.columns else "N/A"
        )
        st.caption(f"📅 Dữ liệu tính đến: **{display_date}**")

        # ── Section 1: Summary KPIs ────────────────────────────────────────────
        self._summary.render(daily_df)
        st.markdown("---")

        # ── Section 2: Ranking table + Heatmap ────────────────────────────────
        col_rank, col_heat = st.columns([1, 1.4])
        with col_rank:
            st.markdown("#### 📊 Ranking ngành hôm nay")
            self._ranking.render(daily_df)
        with col_heat:
            st.markdown(f"#### 🗓️ Score heatmap ({int(heatmap_days)} ngày gần nhất)")
            today_str = datetime.now().date().strftime("%Y-%m-%d")
            heat_from = (
                datetime.now().date() - timedelta(days=int(heatmap_days) * 2)
            ).strftime("%Y-%m-%d")
            heatmap_df = self._loader.heatmap(heat_from, today_str)
            self._heatmap.render(heatmap_df, n_days=int(heatmap_days))

        st.markdown("---")

        # ── Section 3: Score trend chart ──────────────────────────────────────
        st.markdown("#### 📈 Score trend theo ngành")
        all_sectors = sorted(daily_df["sector_name"].unique().tolist())
        default_sectors = (
            daily_df.nsmallest(5, "rank")["sector_name"].tolist()
            if len(all_sectors) >= 5 else all_sectors
        )
        selected_sectors = st.multiselect(
            "Chọn ngành để so sánh", all_sectors,
            default=default_sectors, key="sr_trend_sectors",
        )

        _period_map = {"1 tháng": 30, "3 tháng": 90, "6 tháng": 180, "1 năm": 365}
        trend_days  = _period_map[trend_period]
        trend_from  = (datetime.now().date() - timedelta(days=trend_days)).strftime("%Y-%m-%d")
        trend_to    = datetime.now().date().strftime("%Y-%m-%d")

        if selected_sectors:
            history_df = self._loader.score_history(
                trend_from, trend_to, tuple(selected_sectors)
            )
            self._trend.render(history_df, selected_sectors)
        else:
            st.info("Chọn ít nhất 1 ngành.")

        st.markdown("---")

        # ── Section 4: Weekly ranking ──────────────────────────────────────────
        st.markdown("#### 📅 Weekly ranking")
        wc1, _ = st.columns([1, 3])
        with wc1:
            week_input = st.text_input(
                "Tuần YYYYWW (trống = mới nhất)", value="",
                key="sr_week", placeholder="vd: 202518",
            )
        year_week_int = int(week_input) if week_input.strip().isdigit() else None
        self._weekly.render(self._scoring_svc, year_week_int)

        st.markdown("---")

        # ── Section 5: Drill-down ──────────────────────────────────────────────
        st.markdown("#### 🔬 Drill-down theo ngành")
        d1, d2 = st.columns([1, 1])
        with d1:
            drill_sector = st.selectbox(
                "Chọn ngành", all_sectors, index=0, key="sr_drill_sector"
            )
        with d2:
            drill_period = st.selectbox(
                "Kỳ", ["1 tháng", "3 tháng", "6 tháng"],
                index=1, key="sr_drill_period",
            )

        drill_days = _period_map.get(drill_period, 90)
        drill_from = (
            datetime.now().date() - timedelta(days=drill_days)
        ).strftime("%Y-%m-%d")

        self._drill.render(drill_sector, drill_from, display_date)


# ── Backward-compatible module-level entry point ──────────────────────────────

def render(db, scoring_svc) -> None:
    """Backward-compatible shim — delegates to Page4."""
    Page4(db, scoring_svc).render()