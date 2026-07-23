# src/interfaces/page6.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 6 — Kagi Chart
#
# OOP Structure  (mirrors page1.py pattern)
# ─────────────────────────────────────────────────────────────────────────────
# Page6                          ← top-level orchestrator
#   ├── _KagiControls            ← symbol + reversal type/value + period
#   ├── _KagiChartSection        ← builds chart via KagiService, renders it
#   └── _SegmentTableSection     ← collapsible pivot-point table
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from src.interfaces.helpers import PERIOD_DAYS


# ══════════════════════════════════════════════════════════════════════════════
# Section: Controls
# ══════════════════════════════════════════════════════════════════════════════

class _KagiControls:
    """Symbol selector + reversal type/value + lookback period."""

    def __init__(self, symbols_df: pd.DataFrame) -> None:
        self._symbols_df = symbols_df

    def render(self) -> dict:
        """Return {'symbol', 'reversal_type', 'reversal_value', 'period'}."""
        c1, c2, c3, c4 = st.columns([1.0, 1.0, 1.0, 1.0])

        sym_list   = self._symbols_df["symbol"].tolist()
        default_ix = sym_list.index("SSI") if "SSI" in sym_list else 0

        with c1:
            symbol = st.selectbox(
                "Mã chứng khoán", sym_list, index=default_ix, key="t6_sym"
            )
        with c2:
            reversal_type = st.selectbox(
                "Loại đảo chiều",
                ["pct", "diff"],
                index=0,
                key="t6_rtype",
                format_func=lambda v: "% (phần trăm)" if v == "pct" else "Tuyệt đối (giá)",
            )
        with c3:
            is_pct = reversal_type == "pct"
            reversal_value = st.number_input(
                "Ngưỡng đảo chiều",
                min_value=0.01,
                value=4.0 if is_pct else 1.0,
                step=0.5 if is_pct else 0.1,
                key=f"t6_rval_{reversal_type}",
                help=(
                    "% biến động tối thiểu so với điểm pivot gần nhất để vẽ cột mới"
                    if is_pct else
                    "Biến động giá tuyệt đối tối thiểu so với điểm pivot gần nhất"
                ),
            )
        with c4:
            period = st.selectbox(
                "Chu kỳ", list(PERIOD_DAYS.keys()), index=3, key="t6_period",
            )

        return {
            "symbol":         symbol,
            "reversal_type":  reversal_type,
            "reversal_value": reversal_value,
            "period":         period,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Section: Kagi Chart
# ══════════════════════════════════════════════════════════════════════════════

class _KagiChartSection:
    """Builds the KagiChart via KagiService and renders the matplotlib figure."""

    def __init__(self, kagi_svc) -> None:
        self._svc = kagi_svc

    def render(
        self,
        symbol: str,
        reversal_type: str,
        reversal_value: float,
        from_date,
        to_date,
    ) -> list | None:
        try:
            segments = self._svc.build_chart(
                symbol,
                reversal_type=reversal_type,
                reversal_value=reversal_value,
                from_date=from_date,
                to_date=to_date,
            )
        except ValueError as e:
            st.warning(f"⚠️ {e}")
            return None
        except Exception as e:
            st.error(f"Lỗi khi tính Kagi chart cho {symbol}: {e}")
            return None

        n_yang = sum(1 for s in segments if s.uptrend)
        n_ying = len(segments) - n_yang

        m1, m2, m3 = st.columns(3)
        m1.metric("Tổng số đoạn", len(segments))
        m2.metric("🟢 Yang (tăng)", n_yang)
        m3.metric("🔴 Ying (giảm)", n_ying)

        fig = self._svc.get_plot(
            segments,
            title=f"{symbol} — Kagi Chart ({reversal_type} = {reversal_value})",
        )
        st.pyplot(fig)

        return segments


# ══════════════════════════════════════════════════════════════════════════════
# Section: Pivot Point Table
# ══════════════════════════════════════════════════════════════════════════════

class _SegmentTableSection:
    """Collapsible table of pivot vertices — one row per Kagi vertex."""

    _COLUMN_MAP = {
        "segment": "Đoạn",
        "trend":   "Xu hướng",
        "x":       "Cột",
        "close":   "Giá",
        "date":    "Ngày",
    }

    def __init__(self, kagi_svc) -> None:
        self._svc = kagi_svc

    def render(self, segments: list | None) -> None:
        if not segments:
            return

        df = self._svc.segments_to_df(segments)
        if df.empty:
            return

        with st.expander("📋 Bảng điểm đảo chiều (pivot points)", expanded=False):
            disp = df.rename(columns=self._COLUMN_MAP)
            styled = disp.style.format({"Giá": "{:,.2f}"})
            st.dataframe(
                styled, width="stretch", height=min(40 * len(disp) + 40, 500)
            )


# ══════════════════════════════════════════════════════════════════════════════
# Page6 — Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class Page6:
    """
    Orchestrates Tab 6 — Kagi Chart.

    Usage (from app entry-point):
        page = Page6(db, kagi_svc, symbols_df, has_data)
        page.render()
    """

    def __init__(self, db, kagi_svc, symbols_df: pd.DataFrame, has_data: bool) -> None:
        self._has_data  = has_data
        self._controls  = _KagiControls(symbols_df)
        self._chart_sec = _KagiChartSection(kagi_svc)
        self._table_sec = _SegmentTableSection(kagi_svc)

    def render(self) -> None:
        st.subheader("🌀 Kagi Chart")

        if not self._has_data:
            st.warning(
                "Chưa có dữ liệu. Hãy đồng bộ danh mục securities ở Tab Đồng bộ trước."
            )
            return

        st.caption(
            "Kagi chart bỏ qua thời gian, chỉ phản ứng theo biến động **giá**. "
            "Đường **Yang** (xanh, dày) = xu hướng tăng · **Ying** (đỏ, mỏng) = xu hướng giảm. "
            "Cột mới chỉ được vẽ khi giá đảo chiều vượt ngưỡng đã chọn bên dưới."
        )

        sel = self._controls.render()

        today     = datetime.now().date()
        from_date = today - timedelta(days=PERIOD_DAYS[sel["period"]])

        segments = self._chart_sec.render(
            sel["symbol"],
            sel["reversal_type"],
            sel["reversal_value"],
            from_date,
            today,
        )

        self._table_sec.render(segments)


# ── Backward-compatible module-level entry point ──────────────────────────────

def render(db, kagi_svc, symbols_df: pd.DataFrame, has_data: bool) -> None:
    """Backward-compatible shim — delegates to Page6."""
    Page6(db, kagi_svc, symbols_df, has_data).render()
