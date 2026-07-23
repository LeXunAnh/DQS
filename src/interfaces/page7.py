# src/interfaces/page7.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 7 — Renko Chart
#
# OOP Structure  (mirrors page6.py pattern)
# ─────────────────────────────────────────────────────────────────────────────
# Page7                          ← top-level orchestrator
#   ├── _RenkoControls           ← symbol + brick-size mode/value + period
#   ├── _RenkoChartSection       ← builds chart via RenkoService, renders it
#   └── _BrickTableSection       ← collapsible brick table + reversal signals
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from src.interfaces.helpers import PERIOD_DAYS, direction_color_style


# ══════════════════════════════════════════════════════════════════════════════
# Section: Controls
# ══════════════════════════════════════════════════════════════════════════════

class _RenkoControls:
    """Symbol selector + brick-size mode/value + lookback period."""

    def __init__(self, symbols_df: pd.DataFrame) -> None:
        self._symbols_df = symbols_df

    def render(self) -> dict:
        """Return {'symbol', 'sizing_mode', 'brick_size', 'atr_period', 'period'}."""
        c1, c2, c3, c4 = st.columns([1.0, 1.0, 1.0, 1.0])

        sym_list   = self._symbols_df["symbol"].tolist()
        default_ix = sym_list.index("SSI") if "SSI" in sym_list else 0

        with c1:
            symbol = st.selectbox(
                "Mã chứng khoán", sym_list, index=default_ix, key="t7_sym"
            )
        with c2:
            sizing_mode = st.selectbox(
                "Loại brick size",
                ["auto", "fixed"],
                index=0,
                key="t7_sizing_mode",
                format_func=lambda v: "Tự động (ATR proxy)" if v == "auto" else "Cố định (giá)",
            )
        with c3:
            is_fixed = sizing_mode == "fixed"
            value = st.number_input(
                "Brick size" if is_fixed else "ATR period",
                min_value=0.1 if is_fixed else 3,
                value=1.0 if is_fixed else 14,
                step=0.1 if is_fixed else 1,
                key=f"t7_val_{sizing_mode}",
                help=(
                    "Biến động giá tuyệt đối cố định cho mỗi brick mới"
                    if is_fixed else
                    "Số phiên dùng để ước lượng ATR proxy → brick size tự động"
                ),
            )
        with c4:
            period = st.selectbox(
                "Chu kỳ", list(PERIOD_DAYS.keys()), index=3, key="t7_period",
            )

        return {
            "symbol":      symbol,
            "sizing_mode": sizing_mode,
            "brick_size":  float(value) if is_fixed else None,
            "atr_period":  int(value) if not is_fixed else 14,
            "period":      period,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Section: Renko Chart
# ══════════════════════════════════════════════════════════════════════════════

class _RenkoChartSection:
    """Builds the RenkoChart via RenkoService and renders the matplotlib figure."""

    def __init__(self, renko_svc) -> None:
        self._svc = renko_svc

    def render(
        self,
        symbol: str,
        brick_size: float | None,
        atr_period: int,
        from_date,
        to_date,
    ):
        """Returns the RenkoChart object on success, or None on failure."""
        try:
            chart = self._svc.build_chart(
                symbol,
                brick_size=brick_size,
                atr_period=atr_period,
                from_date=from_date,
                to_date=to_date,
            )
        except ValueError as e:
            st.warning(f"⚠️ {e}")
            return None
        except Exception as e:
            st.error(f"Lỗi khi tính Renko chart cho {symbol}: {e}")
            return None

        n_up   = sum(1 for b in chart.bricks if b["type"] == "up")
        n_down = sum(1 for b in chart.bricks if b["type"] == "down")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Tổng số brick", len(chart.bricks))
        m2.metric("🟢 Up",         n_up)
        m3.metric("🔴 Down",       n_down)
        m4.metric("Brick size",    f"{chart.brick_size:,.2f}")

        fig = self._svc.get_plot(
            chart,
            title=f"{symbol} — Renko Chart (brick = {chart.brick_size:,.2f})",
        )
        st.pyplot(fig)

        return chart


# ══════════════════════════════════════════════════════════════════════════════
# Section: Brick Table + Reversal Signals
# ══════════════════════════════════════════════════════════════════════════════

class _BrickTableSection:
    """Collapsible table of raw bricks + reversal-signal table."""

    _COLUMN_MAP = {
        "brick_no": "Brick #",
        "type":     "Loại",
        "open":     "Mở",
        "close":    "Đóng",
    }

    _SIGNAL_COLUMN_MAP = {
        "brick_no":  "Brick #",
        "signal":    "Tín hiệu",
        "direction": "Chiều",
        "strength":  "Strength",
        "price":     "Giá",
    }

    def __init__(self, renko_svc) -> None:
        self._svc = renko_svc

    def render(self, chart) -> None:
        if chart is None:
            return

        bricks_df = self._svc.bricks_to_df(chart)
        if bricks_df.empty:
            return

        signals_df = self._svc.get_reversal_signals(bricks_df)

        with st.expander("🔔 Reversal signals", expanded=False):
            if signals_df.empty:
                st.info("Không có tín hiệu đảo chiều trong khoảng dữ liệu này.")
            else:
                disp   = signals_df.rename(columns=self._SIGNAL_COLUMN_MAP)
                styled = disp.style.format({"Strength": "{:.2%}", "Giá": "{:,.2f}"})
                if "Chiều" in disp.columns:
                    styled = styled.map(direction_color_style, subset=["Chiều"])
                st.dataframe(styled, width="stretch", height=min(40 * len(disp) + 40, 400))

        with st.expander("📋 Bảng brick", expanded=False):
            disp   = bricks_df.rename(columns=self._COLUMN_MAP)
            styled = disp.style.format({"Mở": "{:,.2f}", "Đóng": "{:,.2f}"})
            st.dataframe(styled, width="stretch", height=min(40 * len(disp) + 40, 500))


# ══════════════════════════════════════════════════════════════════════════════
# Page7 — Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class Page7:
    """
    Orchestrates Tab 7 — Renko Chart.

    Usage (from app entry-point):
        page = Page7(db, renko_svc, symbols_df, has_data)
        page.render()
    """

    def __init__(self, db, renko_svc, symbols_df: pd.DataFrame, has_data: bool) -> None:
        self._has_data  = has_data
        self._controls  = _RenkoControls(symbols_df)
        self._chart_sec = _RenkoChartSection(renko_svc)
        self._table_sec = _BrickTableSection(renko_svc)

    def render(self) -> None:
        st.subheader("🧱 Renko Chart")

        if not self._has_data:
            st.warning(
                "Chưa có dữ liệu. Hãy đồng bộ danh mục securities ở Tab Đồng bộ trước."
            )
            return

        st.caption(
            "Renko chart bỏ qua thời gian, chỉ phản ứng theo biến động **giá**. "
            "Brick **xanh** = xu hướng tăng · **đỏ** = xu hướng giảm. "
            "Brick mới chỉ được vẽ khi giá di chuyển đủ một đơn vị brick size đã chọn bên dưới."
        )

        sel = self._controls.render()

        today      = datetime.now().date()
        from_date  = today - timedelta(days=PERIOD_DAYS[sel["period"]])

        chart = self._chart_sec.render(
            sel["symbol"],
            sel["brick_size"],
            sel["atr_period"],
            from_date,
            today,
        )

        self._table_sec.render(chart)


# ── Backward-compatible module-level entry point ──────────────────────────────

def render(db, renko_svc, symbols_df: pd.DataFrame, has_data: bool) -> None:
    """Backward-compatible shim — delegates to Page7."""
    Page7(db, renko_svc, symbols_df, has_data).render()
