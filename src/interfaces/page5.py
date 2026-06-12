# src/interfaces/page5.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 5 — Market Index Dashboard
# ─────────────────────────────────────────────────────────────────────────────
# Layout
# ──────
# Section 1 │ Controls (market, period, columns-per-row)
# Section 2 │ Summary snapshot cards (latest value, change, breadth)
# Section 3 │ Candlestick matrix — N charts per row, TradingView lightweight
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

from src.services.index_service import IndexService

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_PERIOD_DAYS: dict[str, int] = {
    "1 tháng":  30,
    "3 tháng":  90,
    "6 tháng":  180,
    "1 năm":    365,
    "2 năm":    730,
    "Toàn bộ":  3650,
}

_CHART_HEIGHT = 280   # px per candlestick panel


# ── Helpers ────────────────────────────────────────────────────────────────────

def _change_color(val: float) -> str:
    if val > 0:
        return "#16a34a"
    if val < 0:
        return "#dc2626"
    return "#6b7280"


def _metric_html(label: str, value: str, delta: str, color: str) -> str:
    return (
        f"<div style='background:#f8f9fa;border-radius:8px;padding:10px 14px;"
        f"border:1px solid #e9ecef;min-width:130px'>"
        f"<div style='font-size:11px;color:#6b7280;margin-bottom:2px'>{label}</div>"
        f"<div style='font-size:18px;font-weight:700;color:#111'>{value}</div>"
        f"<div style='font-size:12px;font-weight:600;color:{color}'>{delta}</div>"
        f"</div>"
    )


def _build_candle_series(df: pd.DataFrame) -> list[dict]:
    """Convert OHLCV DataFrame → TradingView candlestick data list."""
    series = []
    for _, r in df.iterrows():
        series.append({
            "time":  r["trading_date"].strftime("%Y-%m-%d"),
            "open":  float(r["open"]),
            "high":  float(r["high"]),
            "low":   float(r["low"]),
            "close": float(r["close"]),
        })
    return series


def _build_volume_series(df: pd.DataFrame) -> list[dict]:
    """Convert OHLCV DataFrame → TradingView histogram data list."""
    series = []
    for _, r in df.iterrows():
        up = float(r["close"]) >= float(r["open"])
        series.append({
            "time":  r["trading_date"].strftime("%Y-%m-%d"),
            "value": float(r["volume"]),
            "color": "rgba(38,166,154,0.5)" if up else "rgba(239,83,80,0.5)",
        })
    return series


def _chart_config(title: str, candle_data: list, vol_data: list) -> list[dict]:
    """
    Build the renderLightweightCharts config for ONE index:
    - Top panel: candlestick chart  (height _CHART_HEIGHT)
    - Bottom panel: volume histogram (height 70px)
    """
    bg   = {"type": "solid", "color": "#ffffff"}
    grid = {"vertLines": {"color": "#f0f0f0"}, "horzLines": {"color": "#f0f0f0"}}

    return [
        {
            "chart": {
                "height": _CHART_HEIGHT,
                "layout": {"background": bg, "textColor": "#333"},
                "grid": grid,
                "crosshair": {"mode": 1},
                "timeScale": {
                    "borderColor": "#d1d5db",
                    "rightOffset": 4,
                    "timeVisible": False,
                },
                "rightPriceScale": {"borderColor": "#d1d5db"},
            },
            "series": [
                {
                    "type": "Candlestick",
                    "data": candle_data,
                    "options": {
                        "upColor":        "#26a69a",
                        "downColor":      "#ef5350",
                        "borderUpColor":  "#26a69a",
                        "borderDownColor":"#ef5350",
                        "wickUpColor":    "#26a69a",
                        "wickDownColor":  "#ef5350",
                        "priceFormat":    {"type": "price", "precision": 2, "minMove": 0.01},
                        "title":          title,
                    },
                }
            ],
        },
        {
            "chart": {
                "height": 70,
                "layout": {"background": bg, "textColor": "#333"},
                "grid": grid,
                "timeScale": {"borderColor": "#d1d5db", "visible": False},
                "rightPriceScale": {
                    "borderColor": "#d1d5db",
                    "scaleMargins": {"top": 0.1, "bottom": 0},
                },
            },
            "series": [
                {
                    "type": "Histogram",
                    "data": vol_data,
                    "options": {
                        "priceFormat": {"type": "volume"},
                        "priceScaleId": "",
                    },
                }
            ],
        },
    ]


# ── Summary snapshot section ───────────────────────────────────────────────────

def _render_snapshot(snapshot_df: pd.DataFrame) -> None:
    """Render a row of metric cards — one per index."""
    if snapshot_df.empty:
        return

    st.markdown("#### 📊 Snapshot mới nhất")

    # Render up to 8 metrics per row, then wrap
    cols_per_row = 8
    chunks = [
        snapshot_df.iloc[i : i + cols_per_row]
        for i in range(0, len(snapshot_df), cols_per_row)
    ]

    for chunk in chunks:
        cols = st.columns(len(chunk))
        for col, (_, row) in zip(cols, chunk.iterrows()):
            change_val = float(row.get("change", 0) or 0)
            ratio_val  = float(row.get("ratio_change", 0) or 0)
            close_val  = float(row.get("close", 0) or 0)
            color      = _change_color(change_val)

            with col:
                st.markdown(
                    _metric_html(
                        label=str(row.get("index_name", row["index_code"]))[:20],
                        value=f"{close_val:,.2f}",
                        delta=f"{change_val:+.2f} ({ratio_val:+.2f}%)",
                        color=color,
                    ),
                    unsafe_allow_html=True,
                )
        st.markdown("")  # spacing


# ── Breadth bar section ────────────────────────────────────────────────────────

def _render_breadth(snapshot_df: pd.DataFrame) -> None:
    """
    Render a compact breadth table:
    Index | Tăng | Không đổi | Giảm | Trần | Sàn
    """
    if snapshot_df.empty:
        return

    want = ["index_code", "advances", "no_changes", "declines", "ceilings", "floors"]
    avail = [c for c in want if c in snapshot_df.columns]
    if len(avail) < 3:
        return

    disp = snapshot_df[avail].copy().rename(columns={
        "index_code": "Chỉ số",
        "advances":   "🟢 Tăng",
        "no_changes": "⬜ Không đổi",
        "declines":   "🔴 Giảm",
        "ceilings":   "🔵 Trần",
        "floors":     "🟣 Sàn",
    })

    def _breadth_style(col: pd.Series) -> list[str]:
        name = col.name
        styles = []
        for v in col:
            if not isinstance(v, (int, float)) or pd.isna(v):
                styles.append("")
                continue
            if "Tăng" in str(name) or "Trần" in str(name):
                styles.append("color:#16a34a;font-weight:600")
            elif "Giảm" in str(name) or "Sàn" in str(name):
                styles.append("color:#dc2626;font-weight:600")
            else:
                styles.append("color:#6b7280")
        return styles

    styled = disp.style
    for col in ["🟢 Tăng", "🔵 Trần"]:
        if col in disp.columns:
            styled = styled.apply(_breadth_style, subset=[col])
    for col in ["🔴 Giảm", "🟣 Sàn"]:
        if col in disp.columns:
            styled = styled.apply(_breadth_style, subset=[col])

    st.dataframe(styled, use_container_width=True, height=min(40 * len(disp) + 40, 400))


# ── Candlestick matrix section ─────────────────────────────────────────────────

def _render_chart_matrix(
    all_ohlcv: dict[str, pd.DataFrame],
    metadata_df: pd.DataFrame,
    cols_per_row: int,
    snapshot_df: pd.DataFrame,
) -> None:
    """
    Render all indices as a matrix of candlestick charts.
    Each cell = one index candlestick + volume panel.
    """
    if not all_ohlcv:
        st.warning("Không có dữ liệu OHLCV để hiển thị.")
        return

    # Build name lookup
    name_map: dict[str, str] = {}
    if not metadata_df.empty and "index_code" in metadata_df.columns:
        for _, r in metadata_df.iterrows():
            name_map[r["index_code"]] = r.get("index_name", r["index_code"])

    # Build latest-value lookup for subtitle
    latest_map: dict[str, tuple] = {}   # code → (close, change, ratio)
    if not snapshot_df.empty:
        for _, r in snapshot_df.iterrows():
            latest_map[r["index_code"]] = (
                float(r.get("close", 0) or 0),
                float(r.get("change", 0) or 0),
                float(r.get("ratio_change", 0) or 0),
            )

    codes = sorted(all_ohlcv.keys())
    total = len(codes)

    st.markdown(f"#### 📈 Biểu đồ nến — {total} chỉ số")

    # Render in rows of cols_per_row
    for row_start in range(0, total, cols_per_row):
        row_codes = codes[row_start : row_start + cols_per_row]
        cols = st.columns(len(row_codes))

        for col_widget, code in zip(cols, row_codes):
            df = all_ohlcv[code]
            if df.empty:
                with col_widget:
                    st.caption(f"{code} — không có dữ liệu")
                continue

            candle_data = _build_candle_series(df)
            vol_data    = _build_volume_series(df)
            chart_cfg   = _chart_config(code, candle_data, vol_data)

            # Header above the chart
            name   = name_map.get(code, code)
            latest = latest_map.get(code)

            with col_widget:
                # Title row
                if latest:
                    close, chg, ratio = latest
                    color = _change_color(chg)
                    st.markdown(
                        f"<div style='margin-bottom:2px'>"
                        f"<span style='font-weight:700;font-size:13px'>{code}</span> "
                        f"<span style='font-size:11px;color:#6b7280'>{name[:25]}</span><br>"
                        f"<span style='font-size:13px;font-weight:600'>{close:,.2f}</span> "
                        f"<span style='font-size:12px;color:{color}'>"
                        f"{chg:+.2f} ({ratio:+.2f}%)</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"**{code}** <span style='font-size:11px;color:#6b7280'>{name[:25]}</span>",
                        unsafe_allow_html=True,
                    )

                # Chart
                renderLightweightCharts(
                    chart_cfg,
                    key=f"idx_chart_{code}",
                )

        st.markdown("")  # row spacing


# ── Main render ────────────────────────────────────────────────────────────────

def render(db) -> None:
    st.subheader("📊 Market Index Dashboard")

    # Init service
    svc = IndexService(db)

    # ── Controls ───────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])

    with c1:
        market = st.selectbox(
            "Sàn",
            ["HOSE", "HNX", "UPCOM", "Tất cả"],
            index=0,
            key="idx_market",
        )
    with c2:
        period = st.selectbox(
            "Kỳ",
            list(_PERIOD_DAYS.keys()),
            index=2,
            key="idx_period",
        )
    with c3:
        cols_per_row = st.selectbox(
            "Số chart / hàng",
            [3, 4, 5],
            index=1,
            key="idx_cols",
        )
    with c4:
        show_breadth = st.checkbox("Hiện bảng breadth", value=True, key="idx_breadth")

    n_days  = _PERIOD_DAYS[period]
    mkt_arg = None if market == "Tất cả" else market

    # ── Load data ──────────────────────────────────────────────────────────────
    with st.spinner("Đang tải dữ liệu chỉ số..."):
        metadata_df  = svc.get_index_metadata(mkt_arg)
        snapshot_df  = svc.get_latest_snapshot(mkt_arg)
        all_ohlcv    = svc.get_all_indices_ohlcv(mkt_arg, n_days=n_days)

    if not all_ohlcv:
        st.warning(
            "Chưa có dữ liệu index. "
            "Hãy chạy **Sync Daily Index Data** ở Tab Data trước."
        )
        return

    # Display date
    if not snapshot_df.empty and "trading_date" in snapshot_df.columns:
        display_date = str(snapshot_df["trading_date"].max())[:10]
        st.caption(f"📅 Dữ liệu tính đến: **{display_date}**  |  **{len(all_ohlcv)}** chỉ số")

    st.markdown("---")

    # ── Section 1: Snapshot metrics ───────────────────────────────────────────
    _render_snapshot(snapshot_df)

    # ── Section 2: Breadth table ──────────────────────────────────────────────
    if show_breadth:
        with st.expander("📋 Breadth thị trường (Tăng / Giảm / Trần / Sàn)", expanded=False):
            _render_breadth(snapshot_df)

    st.markdown("---")

    # ── Section 3: Candlestick matrix ─────────────────────────────────────────
    _render_chart_matrix(
        all_ohlcv    = all_ohlcv,
        metadata_df  = metadata_df,
        cols_per_row = int(cols_per_row),
        snapshot_df  = snapshot_df,
    )