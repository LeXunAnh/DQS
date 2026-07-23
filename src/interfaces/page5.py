# src/interfaces/page5.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 5 — Market Index Dashboard
#
# OOP Structure  (mirrors page1.py pattern)
# ─────────────────────────────────────────────────────────────────────────────
# Page5                          ← top-level orchestrator
#   ├── _IndexDataLoader         ← encapsulates IndexService calls
#   ├── _ControlBar              ← period / cols-per-row / breadth toggle
#   ├── _SnapshotSection         ← metric cards (latest value, change)
#   ├── _BreadthSection          ← advances/declines/ceiling/floor table
#   └── _CandleMatrixSection     ← N-per-row candlestick chart grid
#         └── _IndexChartBuilder ← pure data → TradingView series (static)
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

from src.services.index_service import IndexService
from src.interfaces.helpers import PERIOD_DAYS

logger = logging.getLogger(__name__)

# ── Layout constants ──────────────────────────────────────────────────────────

_CHART_HEIGHT = 280   # px per candlestick panel
_VOLUME_HEIGHT = 70   # px for the volume histogram sub-panel

# Indices always shown first regardless of sort order
_PRIORITY_CODES = ["VNINDEX", "HNXIndex", "VN30", "HNX30"]


# ══════════════════════════════════════════════════════════════════════════════
# Chart Builder  (pure data → TradingView series, no Streamlit calls)
# ══════════════════════════════════════════════════════════════════════════════

class _IndexChartBuilder:
    """
    Stateless converter: DataFrame → TradingView-compatible list[dict].
    All methods are static — no state, easy to unit-test.
    """

    @staticmethod
    def candle_series(df: pd.DataFrame) -> list[dict]:
        return [
            {
                "time":  r["trading_date"].strftime("%Y-%m-%d"),
                "open":  float(r["open"]),
                "high":  float(r["high"]),
                "low":   float(r["low"]),
                "close": float(r["close"]),
            }
            for _, r in df.iterrows()
        ]

    @staticmethod
    def volume_series(df: pd.DataFrame) -> list[dict]:
        series = []
        for _, r in df.iterrows():
            up = float(r["close"]) >= float(r["open"])
            series.append({
                "time":  r["trading_date"].strftime("%Y-%m-%d"),
                "value": float(r["volume"]),
                "color": (
                    "rgba(38,166,154,0.5)" if up
                    else "rgba(239,83,80,0.5)"
                ),
            })
        return series

    @staticmethod
    def build_chart_config(
        title: str,
        candle_data: list[dict],
        vol_data: list[dict],
    ) -> list[dict]:
        """
        Two-pane TradingView config for one index:
        top = candlestick (_CHART_HEIGHT px),
        bottom = volume histogram (_VOLUME_HEIGHT px).
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
                "series": [{
                    "type": "Candlestick",
                    "data": candle_data,
                    "options": {
                        "upColor":         "#26a69a",
                        "downColor":       "#ef5350",
                        "borderUpColor":   "#26a69a",
                        "borderDownColor": "#ef5350",
                        "wickUpColor":     "#26a69a",
                        "wickDownColor":   "#ef5350",
                        "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
                        "title": title,
                    },
                }],
            },
            {
                "chart": {
                    "height": _VOLUME_HEIGHT,
                    "layout": {"background": bg, "textColor": "#333"},
                    "grid": grid,
                    "timeScale": {"borderColor": "#d1d5db", "visible": False},
                    "rightPriceScale": {
                        "borderColor": "#d1d5db",
                        "scaleMargins": {"top": 0.1, "bottom": 0},
                    },
                },
                "series": [{
                    "type": "Histogram",
                    "data": vol_data,
                    "options": {
                        "priceFormat": {"type": "volume"},
                        "priceScaleId": "",
                    },
                }],
            },
        ]


# ══════════════════════════════════════════════════════════════════════════════
# Data Loader
# ══════════════════════════════════════════════════════════════════════════════

class _IndexDataLoader:
    """
    Wraps IndexService calls. Keeping all service interactions
    here means the UI sections stay free of service dependencies.
    """

    def __init__(self, index_svc: IndexService) -> None:
        self._svc = index_svc

    def load_all(self, n_days: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        """
        Fetch metadata, snapshot, and OHLCV for all indices.
        Returns:
            (metadata_df, snapshot_df, all_ohlcv)
        """

        metadata_df = self._svc.get_index_metadata(None)
        snapshot_df = self._svc.get_latest_snapshot(None)
        all_ohlcv   = self._svc.get_all_indices_ohlcv(None, n_days=n_days)
        return metadata_df, snapshot_df, all_ohlcv


# ══════════════════════════════════════════════════════════════════════════════
# Section: Controls
# ══════════════════════════════════════════════════════════════════════════════

class _ControlBar:
    """Period selector, cols-per-row picker, and breadth toggle."""

    def render(self) -> dict:
        """Return {'period', 'cols_per_row', 'show_breadth'}."""
        c1, c2, c3 = st.columns([1, 1, 1])

        with c1:
            period = st.selectbox(
                "Kỳ",
                list(PERIOD_DAYS.keys()),
                index=2,
                key="idx_period",
            )
        with c2:
            cols_per_row = st.selectbox(
                "Số chart / hàng", [3, 4, 5], index=1, key="idx_cols"
            )
        with c3:
            show_breadth = st.checkbox(
                "Hiện bảng breadth", value=True, key="idx_breadth"
            )

        return {
            "period":       period,
            "cols_per_row": int(cols_per_row),
            "show_breadth": show_breadth,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Section: Snapshot Metric Cards
# ══════════════════════════════════════════════════════════════════════════════

class _SnapshotSection:
    """
    Render a row of metric cards — one per index — showing the latest
    close, absolute change, and percentage change.
    """

    _CARDS_PER_ROW = 8

    def render(self, snapshot_df: pd.DataFrame) -> None:
        if snapshot_df.empty:
            return

        st.markdown("#### 📊 Snapshot mới nhất")
        chunks = [
            snapshot_df.iloc[i : i + self._CARDS_PER_ROW]
            for i in range(0, len(snapshot_df), self._CARDS_PER_ROW)
        ]

        for chunk in chunks:
            cols = st.columns(len(chunk))
            for col, (_, row) in zip(cols, chunk.iterrows()):
                change_val = float(row.get("change",       0) or 0)
                ratio_val  = float(row.get("ratio_change", 0) or 0)
                close_val  = float(row.get("close",        0) or 0)
                color      = self._change_color(change_val)

                with col:
                    st.markdown(
                        self._metric_html(
                            label=str(row.get("index_name", row["index_code"]))[:20],
                            value=f"{close_val:,.2f}",
                            delta=f"{change_val:+.2f} ({ratio_val:+.2f}%)",
                            color=color,
                        ),
                        unsafe_allow_html=True,
                    )
            st.markdown("")   # visual spacing between rows

    @staticmethod
    def _change_color(val: float) -> str:
        if val > 0:  return "#16a34a"
        if val < 0:  return "#dc2626"
        return "#6b7280"

    @staticmethod
    def _metric_html(label: str, value: str, delta: str, color: str) -> str:
        return (
            f"<div style='background:#f8f9fa;border-radius:8px;padding:10px 14px;"
            f"border:1px solid #e9ecef;min-width:130px'>"
            f"<div style='font-size:11px;color:#6b7280;margin-bottom:2px'>{label}</div>"
            f"<div style='font-size:18px;font-weight:700;color:#111'>{value}</div>"
            f"<div style='font-size:12px;font-weight:600;color:{color}'>{delta}</div>"
            f"</div>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section: Breadth Table
# ══════════════════════════════════════════════════════════════════════════════

# class _BreadthSection:
#     """Advances / declines / ceilings / floors breadth statistics table."""
#
#     _COLUMN_MAP = {
#         "index_code": "Chỉ số",
#         "advances":   "🟢 Tăng",
#         "no_changes": "⬜ Không đổi",
#         "declines":   "🔴 Giảm",
#         "ceilings":   "🔵 Trần",
#         "floors":     "🟣 Sàn",
#     }
#
#     def render(self, snapshot_df: pd.DataFrame) -> None:
#         if snapshot_df.empty:
#             return
#
#         avail = [c for c in self._COLUMN_MAP if c in snapshot_df.columns]
#         if len(avail) < 3:
#             return
#
#         with st.expander(
#             "📋 Breadth thị trường (Tăng / Giảm / Trần / Sàn)", expanded=False
#         ):
#             disp    = snapshot_df[avail].copy().rename(columns=self._COLUMN_MAP)
#             styled  = disp.style
#             for col in ["🟢 Tăng", "🔵 Trần"]:
#                 if col in disp.columns:
#                     styled = styled.apply(self._breadth_style, subset=[col])
#             for col in ["🔴 Giảm", "🟣 Sàn"]:
#                 if col in disp.columns:
#                     styled = styled.apply(self._breadth_style, subset=[col])
#             st.dataframe(styled, width="stretch", height=min(40 * len(disp) + 40, 400))
#
#     @staticmethod
#     def _breadth_style(col: pd.Series) -> list[str]:
#         name = col.name
#         styles = []
#         for v in col:
#             if not isinstance(v, (int, float)) or pd.isna(v):
#                 styles.append("")
#                 continue
#             if "Tăng" in str(name) or "Trần" in str(name):
#                 styles.append("color:#16a34a;font-weight:600")
#             elif "Giảm" in str(name) or "Sàn" in str(name):
#                 styles.append("color:#dc2626;font-weight:600")
#             else:
#                 styles.append("color:#6b7280")
#         return styles


class _BreadthSection:
    """Độ rộng thị trường hiển thị dạng ma trận ngang cho 4 chỉ số chính."""

    _TARGET_INDICES = ["VNINDEX", "VN30", "HNXINDEX", "HNX30"]

    def render(self, snapshot_df: pd.DataFrame) -> None:
        if snapshot_df.empty:
            return

        # Đồng bộ hóa chữ hoa/thường để đối chiếu chính xác
        df_match = snapshot_df.copy()
        if "index_code" in df_match.columns:
            df_match["match_code"] = df_match["index_code"].str.upper()
        else:
            return

        # Lọc và sắp xếp chuẩn theo thứ tự yêu cầu
        filtered_df = df_match[df_match["match_code"].isin(self._TARGET_INDICES)]
        if filtered_df.empty:
            return

        filtered_df = filtered_df.set_index("match_code").reindex(self._TARGET_INDICES).reset_index()

        st.markdown("#### 📋 Độ rộng thị trường")

        # Tạo cấu trúc 4 cột tương ứng với 4 chỉ số chính
        cols = st.columns(len(filtered_df))

        for col, (_, row) in zip(cols, filtered_df.iterrows()):
            # Trường hợp chỉ số thiếu dữ liệu trong snapshot
            if pd.isna(row.get("index_code")):
                code_name = row["match_code"]
                with col:
                    st.markdown(
                        f"<div style='background:#f8f9fa;border-radius:8px;padding:12px;border:1px solid #e9ecef;text-align:center'>"
                        f"<div style='font-weight:700;font-size:14px;color:#333;margin-bottom:6px'>{code_name}</div>"
                        f"<div style='font-size:12px;color:#9ca3af;font-style:italic'>Không có dữ liệu</div>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                continue

            code = str(row["index_code"]).upper()
            floor = int(row.get("floors", 0) or 0)
            dec = int(row.get("declines", 0) or 0)
            flat = int(row.get("no_changes", 0) or 0)
            adv = int(row.get("advances", 0) or 0)
            ceil = int(row.get("ceilings", 0) or 0)

            with col:
                st.markdown(
                    f"<div style='background:#f8f9fa;border-radius:8px;padding:10px 12px;border:1px solid #e9ecef;text-align:center'>"
                    f"  <div style='font-weight:700;font-size:14px;color:#111;margin-bottom:8px;border-bottom:1px solid #e5e7eb;padding-bottom:4px'>{code}</div>"
                    f"  <div style='display:flex;justify-content:space-between;align-items:center;gap:2px;font-size:12px;font-weight:600'>"
                    f"    <div style='color:#06b6d4' title='Sàn'><span style='display:block;font-size:10px;color:#6b7280;font-weight:normal'>SÀN</span>{floor}</div>"
                    f"    <div style='color:#dc2626' title='Giảm'><span style='display:block;font-size:10px;color:#6b7280;font-weight:normal'>GIẢM</span>{dec}</div>"
                    f"    <div style='color:#4b5563' title='Không đổi'><span style='display:block;font-size:10px;color:#6b7280;font-weight:normal'>TC</span>{flat}</div>"
                    f"    <div style='color:#16a34a' title='Tăng'><span style='display:block;font-size:10px;color:#6b7280;font-weight:normal'>TĂNG</span>{adv}</div>"
                    f"    <div style='color:#a855f7' title='Trần'><span style='display:block;font-size:10px;color:#6b7280;font-weight:normal'>TRẦN</span>{ceil}</div>"
                    f"  </div>"
                    f"</div>",
                    unsafe_allow_html=True
                )

# ══════════════════════════════════════════════════════════════════════════════
# Section: Candlestick Matrix
# ══════════════════════════════════════════════════════════════════════════════

class _CandleMatrixSection:
    """
    Renders all index candlestick charts in a responsive N-per-row grid.

    Priority codes (VNINDEX, HNXIndex, VN30, HNX30) are always shown
    first; remaining codes are sorted alphabetically.
    """

    def __init__(self) -> None:
        self._builder = _IndexChartBuilder()

    def render(
        self,
        all_ohlcv: dict[str, pd.DataFrame],
        metadata_df: pd.DataFrame,
        snapshot_df: pd.DataFrame,
        cols_per_row: int,
    ) -> None:
        if not all_ohlcv:
            st.warning("Không có dữ liệu OHLCV để hiển thị.")
            return

        name_map   = self._build_name_map(metadata_df)
        latest_map = self._build_latest_map(snapshot_df)
        codes      = self._sort_codes(list(all_ohlcv.keys()))
        total      = len(codes)

        st.markdown(f"#### 📈 Biểu đồ nến — {total} chỉ số")

        for row_start in range(0, total, cols_per_row):
            row_codes = codes[row_start : row_start + cols_per_row]
            col_widgets = st.columns(len(row_codes))

            for col_widget, code in zip(col_widgets, row_codes):
                df = all_ohlcv[code]
                if df.empty:
                    with col_widget:
                        st.caption(f"{code} — không có dữ liệu")
                    continue

                with col_widget:
                    self._render_one_cell(code, df, name_map, latest_map)

            st.markdown("")   # row spacing

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _sort_codes(available: list[str]) -> list[str]:
        priority = [c for c in _PRIORITY_CODES if c in available]
        others   = sorted(c for c in available if c not in _PRIORITY_CODES)
        return priority + others

    @staticmethod
    def _build_name_map(metadata_df: pd.DataFrame) -> dict[str, str]:
        if metadata_df.empty or "index_code" not in metadata_df.columns:
            return {}
        return {
            r["index_code"]: r.get("index_name", r["index_code"])
            for _, r in metadata_df.iterrows()
        }

    @staticmethod
    def _build_latest_map(
        snapshot_df: pd.DataFrame,
    ) -> dict[str, tuple[float, float, float]]:
        """Return {code: (close, change, ratio_change)}."""
        if snapshot_df.empty:
            return {}
        return {
            r["index_code"]: (
                float(r.get("close",        0) or 0),
                float(r.get("change",       0) or 0),
                float(r.get("ratio_change", 0) or 0),
            )
            for _, r in snapshot_df.iterrows()
        }

    def _render_one_cell(
        self,
        code: str,
        df: pd.DataFrame,
        name_map: dict,
        latest_map: dict,
    ) -> None:
        candle_data = self._builder.candle_series(df)
        vol_data    = self._builder.volume_series(df)
        chart_cfg   = self._builder.build_chart_config(code, candle_data, vol_data)

        name   = name_map.get(code, code)
        latest = latest_map.get(code)

        # Header above the chart
        if latest:
            close, chg, ratio = latest
            color = "#16a34a" if chg > 0 else "#dc2626" if chg < 0 else "#6b7280"
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
                f"**{code}** "
                f"<span style='font-size:11px;color:#6b7280'>{name[:25]}</span>",
                unsafe_allow_html=True,
            )

        renderLightweightCharts(chart_cfg, key=f"idx_chart_{code}")


# ══════════════════════════════════════════════════════════════════════════════
# Page5 — Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class Page5:
    """
    Orchestrates Tab 5 — Market Index Dashboard.

    Usage (from app entry-point):
        page = Page5(db)
        page.render()
    """

    def __init__(self, db, index_svc) -> None:
        self._loader  = _IndexDataLoader(index_svc)

        self._controls  = _ControlBar()
        self._snapshot  = _SnapshotSection()
        self._breadth   = _BreadthSection()
        self._matrix    = _CandleMatrixSection()

    def render(self) -> None:
        st.subheader("📊 Market Index Dashboard")

        # ── Controls ──────────────────────────────────────────────────────────
        sel          = self._controls.render()
        n_days       = PERIOD_DAYS[sel["period"]]
        cols_per_row = sel["cols_per_row"]
        show_breadth = sel["show_breadth"]

        # ── Load data ─────────────────────────────────────────────────────────
        with st.spinner("Đang tải dữ liệu chỉ số..."):
            metadata_df, snapshot_df, all_ohlcv = self._loader.load_all(n_days)

        if not all_ohlcv:
            st.warning(
                "Chưa có dữ liệu index. "
                "Hãy chạy **Sync Daily Index Data** ở Tab Data trước."
            )
            return

        # Caption with data date and count
        if not snapshot_df.empty and "trading_date" in snapshot_df.columns:
            display_date = str(snapshot_df["trading_date"].max())[:10]
            st.caption(
                f"📅 Dữ liệu tính đến: **{display_date}** "
                f"| **{len(all_ohlcv)}** chỉ số"
            )

        st.markdown("---")

        # ── Section 1: Snapshot metric cards ──────────────────────────────────
        self._snapshot.render(snapshot_df)

        # ── Section 2: Breadth table (optional) ───────────────────────────────
        if show_breadth:
            self._breadth.render(snapshot_df)

        st.markdown("---")

        # ── Section 3: Candlestick matrix ─────────────────────────────────────
        self._matrix.render(
            all_ohlcv    = all_ohlcv,
            metadata_df  = metadata_df,
            snapshot_df  = snapshot_df,
            cols_per_row = cols_per_row,
        )


# ── Backward-compatible module-level entry point ──────────────────────────────

def render(db, index_svc) -> None:
    """Backward-compatible shim — delegates to Page5."""
    Page5(db, index_svc).render()