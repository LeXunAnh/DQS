# src/interfaces/page5.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab — Sector Rotation Dashboard
# ─────────────────────────────────────────────────────────────────────────────
# Layout
# ──────
# Section 1 │ Controls + Summary metrics
# Section 2 │ Ranking table  │  Regime heatmap (sectors × dates)
# Section 3 │ Score trend chart (multi-sector line)
# Section 4 │ Weekly ranking table
# Section 5 │ Drill-down: symbol matrix + indicator charts + breadth
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import text
from src.database.handler import DatabaseHandler

logger = logging.getLogger(__name__)

# ── Regime palette ─────────────────────────────────────────────────────────────
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

# ── Per-column color rules for symbol matrix ───────────────────────────────────
# Each entry: (low_threshold, high_threshold) for red/green shading
# Values between thresholds → neutral background
_MATRIX_THRESHOLDS: dict[str, tuple] = {
    "CMF":    (-0.05,  0.05),
    "MFI":    (40.0,   60.0),
    "RVOL":   (0.8,    1.3),
    "NMF_z":  (-0.5,   0.5),
    "Accel":  (-0.3,   0.3),
    "NFF_z":  (-0.5,   0.5),
}

# ── CSS helpers ────────────────────────────────────────────────────────────────

def _score_color(val: float) -> str:
    if pd.isna(val):
        return "background-color:#f3f4f6"
    if val >= 0.45:  return "background-color:#16a34a;color:#fff"
    if val >= 0.15:  return "background-color:#86efac;color:#14532d"
    if val >= -0.15: return "background-color:#f3f4f6;color:#374151"
    if val >= -0.45: return "background-color:#fca5a5;color:#7f1d1d"
    return "background-color:#dc2626;color:#fff"


def _regime_style(val: str) -> str:
    bg  = REGIME_BG.get(val,  "#f3f4f6")
    clr = REGIME_COLOR.get(val, "#374151")
    return f"background-color:{bg};color:{clr};font-weight:600"


def _delta_style(val: float) -> str:
    if pd.isna(val): return ""
    return ("color:#16a34a;font-weight:600" if val > 0
            else "color:#dc2626;font-weight:600" if val < 0 else "")


def _cell_style(col: str):
    """
    Return a per-value styler function for a given indicator column.
    Green  = strong positive signal (above high threshold)
    Red    = strong negative signal (below low threshold)
    Neutral = in between
    """
    lo, hi = _MATRIX_THRESHOLDS.get(col, (-0.5, 0.5))

    def _style(val: float) -> str:
        if pd.isna(val):
            return "background-color:#f3f4f6;color:#9ca3af"
        if val > hi:
            # Intensity scales with distance above threshold (cap at 3×)
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


# ── Data loaders ───────────────────────────────────────────────────────────────

def _load_daily_ranking(scoring_svc, date_str, min_stocks: int) -> pd.DataFrame:
    return scoring_svc.get_latest_ranking(
        date=date_str, min_coverage=0.0, min_stocks=min_stocks,
    )

@st.cache_data(ttl=300)
def _load_score_history(_db, from_date: str, to_date: str, sectors: tuple[str, ...]) -> pd.DataFrame:
    return DatabaseHandler.fetch_sector_score_history(_db, from_date, to_date, list(sectors))

@st.cache_data(ttl=300)
def _load_heatmap_data(_db, from_date: str, to_date: str) -> pd.DataFrame:
    return DatabaseHandler.fetch_sector_heatmap(_db, from_date, to_date)

@st.cache_data(ttl=300)
def _load_sector_detail(_db, sector: str, from_date: str, to_date: str) -> pd.DataFrame:
    return DatabaseHandler.fetch_sector_detail(_db, sector, from_date, to_date)

@st.cache_data(ttl=300)
def _load_symbol_matrix(_db, sector: str, date_str: str) -> pd.DataFrame:
    return DatabaseHandler.fetch_symbol_matrix(_db, sector, date_str)

@st.cache_data(ttl=300)
def _load_symbol_history(_db, sector: str, from_date: str, to_date: str) -> pd.DataFrame:
    return DatabaseHandler.fetch_symbol_history(_db, sector, from_date, to_date)

@st.cache_data(ttl=300)
def _load_sector_ohlc(_db, sector: str, from_date: str, to_date: str) -> pd.DataFrame:
    return DatabaseHandler.load_sector_ohlc(_db, sector, from_date, to_date)


# ── Sector candle chart helpers ─────────────────────────────────────────────────

def _base100(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise OHLC so the first close = 100."""
    df = df.copy()
    if df.empty:
        return df
    base = df["close"].dropna().iloc[0]
    if base == 0:
        return df
    for c in ["open", "high", "low", "close"]:
        if c in df.columns:
            df[c] = (df[c] / base * 100).round(4)
    return df


def _candle_series_data(df: pd.DataFrame) -> list[dict]:
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


def _vol_series_data(df: pd.DataFrame) -> list[dict]:
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


def _score_series_data(score_df: pd.DataFrame) -> list[dict]:
    return [
        {"time": row["date"].strftime("%Y-%m-%d"), "value": round(float(row["total_score"]), 4)}
        for _, row in score_df.iterrows()
        if not pd.isna(row["total_score"])
    ]


def _render_sector_candle_chart(
    price_df: pd.DataFrame,
    score_df: pd.DataFrame,
    normalise: bool,
    show_score: bool,
    key: str,
) -> None:
    """Render 2-pane (+ optional score pane) lightweight candle chart for a sector."""
    from streamlit_lightweight_charts import renderLightweightCharts

    if price_df.empty:
        st.info("Không có dữ liệu giá tổng hợp cho ngành này.")
        return

    df = _base100(price_df) if normalise else price_df.copy()
    bg   = {"type": "solid", "color": "#ffffff"}
    grid = {"vertLines": {"color": "#f0f0f0"}, "horzLines": {"color": "#f0f0f0"}}

    charts = [
        {
            "chart": {
                "height": 360,
                "layout": {"background": bg, "textColor": "#333"},
                "grid": grid,
                "crosshair": {"mode": 1},
                "timeScale": {"borderColor": "#d1d5db", "rightOffset": 8},
                "rightPriceScale": {"borderColor": "#d1d5db"},
            },
            "series": [{
                "type": "Candlestick",
                "data": _candle_series_data(df),
                "options": {
                    "upColor":         "#26a69a",
                    "downColor":       "#ef5350",
                    "borderUpColor":   "#26a69a",
                    "borderDownColor": "#ef5350",
                    "wickUpColor":     "#26a69a",
                    "wickDownColor":   "#ef5350",
                    "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
                },
            }],
        },
        {
            "chart": {
                "height": 80,
                "layout": {"background": bg, "textColor": "#333"},
                "grid": grid,
                "timeScale": {"borderColor": "#d1d5db", "visible": False},
                "rightPriceScale": {
                    "borderColor": "#d1d5db",
                    "scaleMargins": {"top": 0.05, "bottom": 0},
                },
            },
            "series": [{
                "type": "Histogram",
                "data": _vol_series_data(df),
                "options": {"priceFormat": {"type": "volume"}, "priceScaleId": ""},
            }],
        },
    ]

    if show_score and not score_df.empty:
        sd = _score_series_data(score_df)
        if sd:
            charts.append({
                "chart": {
                    "height": 70,
                    "layout": {"background": bg, "textColor": "#333"},
                    "grid": grid,
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
                        "color": "#8b5cf6",
                        "lineWidth": 2,
                        "priceLineVisible": False,
                        "lastValueVisible": True,
                        "title": "MF Score",
                        "priceFormat": {"type": "price", "precision": 3, "minMove": 0.001},
                    },
                }],
            })

    renderLightweightCharts(charts, key=key)



# ── Sub-renderers ──────────────────────────────────────────────────────────────

def _render_summary_metrics(df: pd.DataFrame) -> None:
    if df.empty:
        return
    n_expansion   = int((df["regime"] == "Expansion").sum())
    n_rotation    = int((df["regime"] == "EarlyRotation").sum())
    n_contraction = int((df["regime"] == "Contraction").sum())
    avg_score     = df["total_score"].mean()
    top_row       = df.loc[df["total_score"].idxmax()]
    bot_row       = df.loc[df["total_score"].idxmin()]

    c1,c2,c3,c4,c5,c6 = st.columns(6)
    c1.metric("🟢 Expansion",      n_expansion)
    c2.metric("🔵 Early Rotation", n_rotation)
    c3.metric("🔴 Contraction",    n_contraction)
    c4.metric("TB Score",          f"{avg_score:+.3f}")
    c5.metric("🏆 Mạnh nhất",
              top_row["sector_name"], f"{top_row['total_score']:+.3f}")
    c6.metric("⚠️ Yếu nhất",
              bot_row["sector_name"], f"{bot_row['total_score']:+.3f}")


def _render_ranking_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Không có dữ liệu ranking.")
        return
    want  = ["rank","sector_name","total_score","inst_score","breadth_score",
             "regime","score_delta_1d","score_delta_5d","n_stocks","coverage_pct"]
    avail = [c for c in want if c in df.columns]
    disp  = df[avail].copy().rename(columns={
        "rank": "Hạng", "sector_name": "Ngành", "total_score": "Score",
        "inst_score": "Inst", "breadth_score": "Breadth", "regime": "Chế độ",
        "score_delta_1d": "Δ1D", "score_delta_5d": "Δ5D",
        "n_stocks": "CP", "coverage_pct": "Coverage",
    })
    fmt = {k: v for k, v in {
        "Score":"    {:+.3f}", "Inst":"{:+.3f}", "Breadth":"{:+.3f}",
        "Δ1D":"{:+.3f}", "Δ5D":"{:+.3f}", "Coverage":"{:.0%}",
    }.items() if k in disp.columns}

    styled = disp.style.format(fmt, na_rep="—")
    for col, fn in [("Score",_score_color),("Chế độ",_regime_style),
                    ("Δ1D",_delta_style),("Δ5D",_delta_style)]:
        if col in disp.columns:
            styled = styled.map(fn, subset=[col])
    st.dataframe(styled, width='stretch', height=440)


def _render_regime_heatmap(df: pd.DataFrame, n_days: int = 20) -> None:
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
    styled = pivot.style.map(_score_color).format("{:+.2f}", na_rep="—")
    st.dataframe(styled, width='stretch', height=400)


def _render_trend_chart(history_df: pd.DataFrame,
                        selected_sectors: list[str]) -> None:
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
    st.line_chart(pivot, width='stretch', height=280)


def _render_weekly_table(scoring_svc, year_week) -> None:
    df = scoring_svc.get_weekly_ranking(year_week=year_week)
    if df.empty:
        st.info("Không có dữ liệu weekly ranking.")
        return
    want  = ["year_week","date_from","date_to","sector_name","rank",
             "total_score","regime","score_delta_1w","n_trading_days"]
    avail = [c for c in want if c in df.columns]
    disp  = df[avail].copy().rename(columns={
        "year_week":"Tuần","date_from":"Từ","date_to":"Đến",
        "sector_name":"Ngành","rank":"Hạng","total_score":"Score",
        "regime":"Chế độ","score_delta_1w":"Δ1W","n_trading_days":"Ngày GD",
    })
    fmt = {k: v for k, v in {"Score":"{:+.3f}","Δ1W":"{:+.3f}"}.items()
           if k in disp.columns}
    styled = disp.style.format(fmt, na_rep="—")
    for col, fn in [("Score",_score_color),("Chế độ",_regime_style),
                    ("Δ1W",_delta_style)]:
        if col in disp.columns:
            styled = styled.map(fn, subset=[col])
    st.dataframe(styled, width='stretch', height=380)

def _pct_change_style(val: float) -> str:
    """Color for per_price_change column: green=up, red=down, neutral=flat."""
    if pd.isna(val):
        return "background-color:#f3f4f6;color:#9ca3af"
    if val > 0:
        ratio = min(val / 7.0, 1.0)          # 7% = ceiling price → full green
        r = int(22  + ratio * (22  - 22))
        g = int(163 + ratio * (239 - 163))
        b = int(74  + ratio * (172 - 74))
        luma = 0.299*r + 0.587*g + 0.114*b
        fg   = "#14532d" if luma > 160 else "#fff"
        return f"background-color:rgb({r},{g},{b});color:{fg};font-weight:600"
    if val < 0:
        ratio = min(abs(val) / 7.0, 1.0)     # -7% = floor price → full red
        r = int(252 + ratio * (220 - 252))
        g = int(165 + ratio * (38  - 165))
        b = int(165 + ratio * (38  - 165))
        luma = 0.299*r + 0.587*g + 0.114*b
        fg   = "#7f1d1d" if luma > 160 else "#fff"
        return f"background-color:rgb({r},{g},{b});color:{fg};font-weight:600"
    return "background-color:#f3f4f6;color:#374151"


def _render_symbol_matrix_single(db, sector: str, date_str: str) -> None:
    """
    Symbol matrix for ONE date.
    Rows = symbols (sorted by trading value DESC).
    Columns = Mã | Tên | %D | Giá | MFI | CMF | RVOL | NMF_z | Accel | NFF_z | GT(tỷ)
    %D  = per_price_change (% vs previous close) — color-coded green/red.
    Giá = adjusted close price of the day.
    Each indicator column is independently color-coded using _cell_style().
    """
    df = _load_symbol_matrix(db, sector, date_str)
    if df.empty:
        st.info(f"Không có dữ liệu cổ phiếu cho {sector} ngày {date_str}.")
        return

    disp = df.rename(columns={
        "symbol": "Mã",
        "stock_name": "Tên",
        "per_price_change":"% Today",
        "mfi": "MFI",
        "cmf": "CMF",
        "rvol": "RVOL",
        "nmf_zscore": "NMF_z",
        "nmf_accel": "Accel",
        "nff_zscore": "NFF_z",
        "trading_value": "GT (tỷ)",
    }).copy()

    # Convert trading value to billions
    if "GT (tỷ)" in disp.columns:
        disp["GT (tỷ)"] = (disp["GT (tỷ)"] / 1e9).round(2)

    # Column order: price info first, then indicators, then value
    col_order = [c for c in [
        "Mã", "Tên", "% Today",
        "MFI", "CMF", "RVOL", "NMF_z", "Accel", "NFF_z",
        "GT (tỷ)",
    ] if c in disp.columns]
    disp = disp[col_order].reset_index(drop=True)

    fmt = {
        "% Today": "{:+.2f}%",
        "MFI": "{:.1f}",
        "CMF": "{:+.3f}",
        "RVOL": "{:.2f}",
        "NMF_z": "{:+.3f}",
        "Accel": "{:+.3f}",
        "NFF_z": "{:+.3f}",
        "GT (tỷ)": "{:.1f}",
    }
    fmt = {k: v for k, v in fmt.items() if k in disp.columns}

    styled = disp.style.format(fmt, na_rep="—")

    # % ngày: green/red intensity scaled to ±7% (ceiling/floor)
    if "% Today" in disp.columns:
        styled = styled.map(_pct_change_style, subset=["% Today"])

    # Indicator columns: per-column threshold coloring
    for col in ["CMF", "MFI", "RVOL", "NMF_z", "Accel", "NFF_z"]:
        if col in disp.columns:
            styled = styled.map(_cell_style(col), subset=[col])

    # GT column: blue gradient (higher = darker)
    if "GT (tỷ)" in disp.columns:
        styled = styled.background_gradient(
            subset=["GT (tỷ)"], cmap="Blues", low=0.2, high=0.8
        )

    n = len(disp)
    height = min(40 * n + 40, 800)
    st.dataframe(styled, width='stretch', height=height)

    st.caption(
        f"**{n} cổ phiếu** &nbsp;|&nbsp; "
        "**% ngày**: tăng/giảm so với phiên trước (±7% = trần/sàn) &nbsp;|&nbsp; "
        "🟢 Tín hiệu dương &nbsp; 🔴 Tín hiệu âm &nbsp; ⬜ Trung tính &nbsp;|&nbsp; "
        "Sắp xếp theo giá trị giao dịch"
    )


def _render_symbol_matrix_multi(db, sector: str,
                                from_date: str, to_date: str,
                                metric: str = "CMF") -> None:
    """
    Cross-date symbol matrix for ONE indicator.
    Rows = symbols, columns = trading dates (last N days).
    Useful for spotting persistent accumulation / distribution.
    """
    METRIC_MAP = {
        "CMF":   "cmf",
        "MFI":   "mfi",
        "RVOL":  "rvol",
        "NMF_z": "nmf_zscore",
        "Accel": "nmf_accel",
        "NFF_z": "nff_zscore",
    }
    col = METRIC_MAP.get(metric, "cmf")

    df = _load_symbol_history(db, sector, from_date, to_date)
    if df.empty:
        st.info(f"Không có dữ liệu lịch sử cho {sector}.")
        return

    # Order symbols by mean trading_value (most liquid first)
    sym_order = (
        df.groupby("symbol")["trading_value"]
        .mean().sort_values(ascending=False).index.tolist()
    )

    pivot = df.pivot_table(
        index="symbol", columns="date",
        values=col, aggfunc="first",
    )
    # Reorder rows by liquidity
    pivot = pivot.reindex([s for s in sym_order if s in pivot.index])
    pivot.columns = [str(c) for c in pivot.columns]

    # Format values
    fmt_str = "{:+.2f}" if col != "mfi" else "{:.1f}"
    styled  = (
        pivot.style
        .map(_cell_style(metric))
        .format(fmt_str, na_rep="—")
    )

    n = len(pivot)
    height = min(40 * n + 40, 800)
    st.dataframe(styled, width='stretch', height=height)
    st.caption(
        f"**{n} cổ phiếu × {len(pivot.columns)} ngày** | "
        "Sắp xếp theo giá trị giao dịch trung bình (lớn nhất ở trên)"
    )


def _render_sector_drilldown(db, sector: str,
                             from_date: str, to_date: str) -> None:
    """
    Drill-down for one sector — 5 tabs:
      1. 🕯️ Candle Chart  — synthetic OHLC candlestick (liquidity-weighted)
      2. 📋 Symbol Matrix  — latest date, all stocks
      3. 📆 Multi-date Matrix — pivot rows=symbol, cols=date
      4. 📈 Indicators    — sector-level factor time-series
      5. 🧩 Breadth       — breadth participation %
    """
    tab_candle, tab_matrix, tab_multi, tab_ind, tab_breadth = st.tabs([
        "🕯️ Candle Chart",
        "📋 Symbol Matrix",
        "📆 Multi-date Matrix",
        "📈 Indicators",
        "🧩 Breadth",
    ])

    # ── Tab 0: Candle Chart — synthetic OHLC ─────────────────────────────────
    with tab_candle:
        st.caption(
            "OHLC tổng hợp = bình quân **gia quyền giá trị giao dịch** (total_match_val) "
            "của tất cả cổ phiếu trong ngành · Giá đã điều chỉnh (adjusted)."
        )

        cc1, cc2, cc3 = st.columns([1, 1, 1])
        with cc1:
            normalise = st.checkbox(
                "Base-100 (so sánh tương đối)",
                value=True,
                key=f"sc_norm_{sector}",
                help="Chia tất cả giá theo giá đóng cửa đầu tiên × 100",
            )
        with cc2:
            show_score = st.checkbox(
                "Hiện MF Score pane",
                value=True,
                key=f"sc_score_{sector}",
            )
        with cc3:
            pass  # reserved

        # Load data
        price_df = _load_sector_ohlc(db, sector, from_date, to_date)
        score_df = _load_sector_detail(db, sector, from_date, to_date)

        # Metric cards
        if not price_df.empty:
            disp_df = _base100(price_df) if normalise else price_df
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
            m1.metric(f"Đóng ({lbl})", f"{close:,.2f}", f"{chg:+.2f} ({chg_pct:+.1f}%)")
            m2.metric("Cao nhất",      f"{float(last['high']):,.2f}")
            m3.metric("Thấp nhất",     f"{float(last['low']):,.2f}")
            m4.metric("KL (cổ phiếu)", f"{float(last['volume'])/1e6:.2f}M")
            m5.metric("MF Score",      score_val)
            m6.metric("Chế độ",        regime_val)

        # Build score overlay DataFrame (date + total_score columns)
        score_overlay = pd.DataFrame()
        if show_score and not score_df.empty and "total_score" in score_df.columns:
            score_overlay = score_df[["date", "total_score"]].dropna(subset=["total_score"]).copy()

        _render_sector_candle_chart(
            price_df   = price_df,
            score_df   = score_overlay,
            normalise  = normalise,
            show_score = show_score,
            key        = f"sc_candle_{sector}_{from_date}_{normalise}_{show_score}",
        )

        # Performance stats
        if not price_df.empty:
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

    # ── Tab 1: Symbol Matrix — single date ────────────────────────────────────
    with tab_matrix:
        st.caption(
            "Tất cả cổ phiếu trong ngành. "
            "Mỗi hàng = 1 mã. Màu = cường độ tín hiệu so với ngưỡng trung tính. "
            "Sắp xếp theo giá trị giao dịch (vốn hóa thanh khoản) giảm dần."
        )
        _render_symbol_matrix_single(db, sector, to_date)

    # ── Tab 2: Symbol Matrix — multi-date (one indicator) ─────────────────────
    with tab_multi:
        mc1, mc2 = st.columns([1, 3])
        with mc1:
            metric = st.selectbox(
                "Chỉ báo",
                ["CMF","MFI","RVOL","NMF_z","Accel","NFF_z"],
                index=0,
                key="sr_multi_metric",
            )
        with mc2:
            st.caption(
                "Rows = cổ phiếu, Columns = ngày giao dịch. "
                "Phát hiện xu hướng tích lũy / phân phối liên tục."
            )
        _render_symbol_matrix_multi(db, sector, from_date, to_date, metric)

    # ── Tab 3: Sector-level indicator time-series ──────────────────────────────
    with tab_ind:
        detail_df = _load_sector_detail(db, sector, from_date, to_date)
        if detail_df.empty:
            st.info("Không có dữ liệu sector factor.")
        else:
            c_left, c_right = st.columns(2)
            with c_left:
                st.markdown("**CMF (Weighted vs Median)**")
                st.line_chart(detail_df.set_index("date")[["weighted_cmf","median_cmf"]],
                              height=180, width='stretch')
                st.markdown("**MFI (Weighted vs Median)**")
                st.line_chart(detail_df.set_index("date")[["weighted_mfi","median_mfi"]],
                              height=180, width='stretch')
                st.markdown("**RVOL (Weighted)**")
                st.line_chart(detail_df.set_index("date")[["weighted_rvol"]],
                              height=160, width='stretch')
            with c_right:
                st.markdown("**NMF Z-Score (Weighted)**")
                st.line_chart(detail_df.set_index("date")[["weighted_nmf_z"]],
                              height=180, width='stretch')
                st.markdown("**NMF Acceleration (Weighted)**")
                st.line_chart(detail_df.set_index("date")[["weighted_accel"]],
                              height=180, width='stretch')
                st.markdown("**NFF Z-Score (Net Foreign Flow)**")
                st.line_chart(detail_df.set_index("date")[["weighted_nff_z"]],
                              height=160, width='stretch')

    # ── Tab 4: Breadth participation ───────────────────────────────────────────
    with tab_breadth:
        detail_df = _load_sector_detail(db, sector, from_date, to_date)
        if detail_df.empty:
            st.info("Không có dữ liệu.")
        else:
            breadth_cols = [c for c in [
                "breadth_cmf_positive","breadth_mfi_above_50",
                "breadth_accel_above_1","breadth_nff_positive",
            ] if c in detail_df.columns]
            if breadth_cols:
                b_df = detail_df.set_index("date")[breadth_cols].rename(columns={
                    "breadth_cmf_positive":  "CMF>0",
                    "breadth_mfi_above_50":  "MFI>50",
                    "breadth_accel_above_1": "Accel>1",
                    "breadth_nff_positive":  "NFF>0",
                })
                st.markdown("**Breadth Participation (% stocks meeting threshold)**")
                st.line_chart(b_df, height=240, width='stretch')
            if "n_stocks" in detail_df.columns and "coverage_pct" in detail_df.columns:
                st.markdown("**Số cổ phiếu & Coverage**")
                st.line_chart(detail_df.set_index("date")[["n_stocks","coverage_pct"]],
                              height=160, width='stretch')


# ── Main render ────────────────────────────────────────────────────────────────

def render(db, scoring_svc) -> None:
    st.subheader("🔄 Sector Rotation — Money Flow Dashboard")

    # ── Controls ───────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([1, 1, 1.5, 1])
    with c1:
        view_date = st.date_input("Ngày xem (trống = mới nhất)",
                                  value=None, key="sr_date")
    with c2:
        min_stocks = st.number_input("Số CP tối thiểu",
                                     min_value=1, max_value=20, value=3,
                                     step=1, key="sr_min_stocks")
    with c3:
        trend_period = st.selectbox("Kỳ trend chart",
                                    ["1 tháng","3 tháng","6 tháng","1 năm"],
                                    index=1, key="sr_trend_period")
    with c4:
        heatmap_days = st.number_input("Số ngày heatmap",
                                       min_value=5, max_value=60,
                                       value=20, step=5, key="sr_heatmap_days")

    # ── Load ranking ───────────────────────────────────────────────────────────
    date_str = view_date.strftime("%Y-%m-%d") if view_date else None
    daily_df = _load_daily_ranking(scoring_svc, date_str, min_stocks)

    if daily_df.empty:
        st.warning(
            "Chưa có dữ liệu sector ranking. "
            "Hãy chạy MFService → SectorAggregationService → SectorScoringService trước."
        )
        return

    display_date = str(daily_df["date"].iloc[0]) if "date" in daily_df.columns else "N/A"
    st.caption(f"📅 Dữ liệu tính đến: **{display_date}**")

    # ── Section 1: Summary ─────────────────────────────────────────────────────
    _render_summary_metrics(daily_df)
    st.markdown("---")

    # ── Section 2: Ranking + Heatmap ───────────────────────────────────────────
    col_rank, col_heat = st.columns([1, 1.4])
    with col_rank:
        st.markdown("#### 📊 Ranking ngành hôm nay")
        _render_ranking_table(daily_df)
    with col_heat:
        st.markdown(f"#### 🗓️ Score heatmap ({int(heatmap_days)} ngày gần nhất)")
        today_str = datetime.now().date().strftime("%Y-%m-%d")
        heat_from = (datetime.now().date()
                     - timedelta(days=int(heatmap_days) * 2)).strftime("%Y-%m-%d")
        heatmap_df = _load_heatmap_data(db, heat_from, today_str)
        _render_regime_heatmap(heatmap_df, n_days=int(heatmap_days))

    st.markdown("---")

    # ── Section 3: Score trend ─────────────────────────────────────────────────
    st.markdown("#### 📈 Score trend theo ngành")
    all_sectors = sorted(daily_df["sector_name"].unique().tolist())
    default_sectors = (daily_df.nsmallest(5, "rank")["sector_name"].tolist()
                       if len(all_sectors) >= 5 else all_sectors)
    selected_sectors = st.multiselect(
        "Chọn ngành để so sánh", all_sectors,
        default=default_sectors, key="sr_trend_sectors",
    )

    _period_days = {"1 tháng": 30, "3 tháng": 90, "6 tháng": 180, "1 năm": 365}
    trend_days   = _period_days[trend_period]
    trend_from   = (datetime.now().date() - timedelta(days=trend_days)).strftime("%Y-%m-%d")
    trend_to     = datetime.now().date().strftime("%Y-%m-%d")

    if selected_sectors:
        history_df = _load_score_history(
            db, trend_from, trend_to, tuple(selected_sectors)
        )
        _render_trend_chart(history_df, selected_sectors)
    else:
        st.info("Chọn ít nhất 1 ngành.")

    st.markdown("---")

    # ── Section 4: Weekly ranking ──────────────────────────────────────────────
    st.markdown("#### 📅 Weekly ranking")
    wc1, _ = st.columns([1, 3])
    with wc1:
        week_input = st.text_input("Tuần YYYYWW (trống = mới nhất)",
                                   value="", key="sr_week",
                                   placeholder="vd: 202518")
    year_week_int = int(week_input) if week_input.strip().isdigit() else None
    _render_weekly_table(scoring_svc, year_week_int)

    st.markdown("---")

    # ── Section 5: Drill-down ──────────────────────────────────────────────────
    st.markdown("#### 🔬 Drill-down theo ngành")

    d1, d2 = st.columns([1, 1])
    with d1:
        drill_sector = st.selectbox("Chọn ngành", all_sectors,
                                    index=0, key="sr_drill_sector")
    with d2:
        drill_period = st.selectbox("Kỳ", ["1 tháng","3 tháng","6 tháng"],
                                    index=1, key="sr_drill_period")

    drill_days = _period_days.get(drill_period, 90)
    drill_from = (datetime.now().date()
                  - timedelta(days=drill_days)).strftime("%Y-%m-%d")

    # Use display_date as to_date so matrix aligns with ranking date shown
    _render_sector_drilldown(db, drill_sector, drill_from, display_date)