# src/interfaces/page3.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — Signal Screener
#
# OOP Structure  (mirrors page1.py pattern)
# ─────────────────────────────────────────────────────────────────────────────
# Page3                          ← top-level orchestrator
#   ├── _FilterBar               ← 5-column filter controls + search button
#   ├── _SummaryMetricsSection   ← 4 KPI cards (total, BUY, SELL, avg strength)
#   ├── _DistributionSection     ← signal-type bar chart expander
#   ├── _SignalTableSection      ← styled main table
#   └── _DetailJsonSection       ← per-symbol JSON parameter expander
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.interfaces.helpers import ALL_SIGNAL_TYPES, direction_color_style


# ══════════════════════════════════════════════════════════════════════════════
# Section: Filter Bar
# ══════════════════════════════════════════════════════════════════════════════

class _FilterBar:
    """Renders the 5-column filter row and the Search button."""

    _SESSION_KEY = "scr_result"

    def render(self, signal_svc) -> None:
        """
        Render controls, run the query on button click, and store the
        result in st.session_state[_SESSION_KEY].
        """
        f1, f2, f3, f4, f5 = st.columns([1.2, 1, 1, 1.5, 1])

        with f1:
            market = st.selectbox(
                "Sàn", ["HOSE", "HNX", "UPCOM", "Tất cả"], key="scr_mkt"
            )
        with f2:
            direction = st.selectbox(
                "Chiều", ["Tất cả", "BUY", "SELL"], key="scr_dir"
            )
        with f3:
            strength = st.slider(
                "Strength tối thiểu", 0.0, 1.0, 0.2, 0.05, key="scr_str"
            )
        with f4:
            signal_types = st.multiselect(
                "Loại tín hiệu (trống = tất cả)",
                ALL_SIGNAL_TYPES,
                default=[],
                key="scr_types",
            )
        with f5:
            date = st.date_input(
                "Ngày (trống = mới nhất)", value=None, key="scr_date"
            )

        if st.button("🔍 Tìm tín hiệu", type="primary", key="scr_search"):
            with st.spinner("Đang truy vấn…"):
                try:
                    result = signal_svc.get_latest_signals(
                        market       = None if market == "Tất cả" else market,
                        date         = date.strftime("%Y-%m-%d") if date else None,
                        direction    = None if direction == "Tất cả" else direction,
                        min_strength = strength,
                        signal_types = signal_types or None,
                        limit        = 300,
                    )
                    st.session_state[self._SESSION_KEY] = result
                except Exception as e:
                    st.error(f"Lỗi truy vấn: {e}")
                    st.session_state[self._SESSION_KEY] = pd.DataFrame()

    @staticmethod
    def get_result() -> pd.DataFrame:
        return st.session_state.get(_FilterBar._SESSION_KEY, pd.DataFrame())


# ══════════════════════════════════════════════════════════════════════════════
# Section: Summary Metrics
# ══════════════════════════════════════════════════════════════════════════════

class _SummaryMetricsSection:
    """Four KPI cards: total signals, BUY count, SELL count, avg strength."""

    def render(self, result: pd.DataFrame) -> None:
        buy_n  = int((result["signal_direction"] == "BUY").sum())
        sell_n = int((result["signal_direction"] == "SELL").sum())

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Tổng tín hiệu", len(result))
        s2.metric("🟢 BUY",        buy_n)
        s3.metric("🔴 SELL",       sell_n)
        s4.metric("Strength TB",   f"{result['strength'].mean():.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# Section: Distribution Chart
# ══════════════════════════════════════════════════════════════════════════════

class _DistributionSection:
    """Signal-type distribution bar chart inside an expander."""

    def render(self, result: pd.DataFrame) -> None:
        type_counts = (
            result
            .groupby(["signal_type", "signal_direction"])
            .size()
            .reset_index(name="count")
        )
        if type_counts.empty:
            return

        with st.expander("Phân bổ theo loại tín hiệu", expanded=True):
            pivot = (
                type_counts
                .pivot(index="signal_type", columns="signal_direction", values="count")
                .fillna(0)
            )
            st.bar_chart(pivot)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Signal Table
# ══════════════════════════════════════════════════════════════════════════════

class _SignalTableSection:
    """Styled, colour-coded main signal result table."""

    _COLUMN_MAP = {
        "signal_date":      "Ngày",
        "symbol":           "Mã",
        "stock_name":       "Tên",
        "market":           "Sàn",
        "signal_type":      "Loại tín hiệu",
        "signal_direction": "Chiều",
        "strength":         "Strength",
        "close_price":      "Giá",
    }

    def render(self, result: pd.DataFrame) -> None:
        st.markdown("---")

        avail = [c for c in self._COLUMN_MAP if c in result.columns]
        disp  = (
            result[avail]
            .copy()
            .rename(columns=self._COLUMN_MAP)
            .sort_values(["Ngày", "Strength"], ascending=[False, False])
            .reset_index(drop=True)
        )

        styled = disp.style.format({"Strength": "{:.2%}", "Giá": "{:,.2f}"})
        if "Chiều" in disp.columns:
            styled = styled.map(direction_color_style, subset=["Chiều"])

        st.dataframe(styled, width="stretch", height=460)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Detail JSON Expander
# ══════════════════════════════════════════════════════════════════════════════

class _DetailJsonSection:
    """Per-symbol parameter detail in a collapsible expander."""

    def render(self, result: pd.DataFrame) -> None:
        with st.expander("Chi tiết parameters (JSON)"):
            syms = result["symbol"].unique().tolist()
            if not syms:
                return

            sel  = st.selectbox("Chọn mã:", syms, key="scr_detail")
            rows = result[result["symbol"] == sel]

            for _, r in rows.iterrows():
                color = "#22c55e" if r["signal_direction"] == "BUY" else "#ef4444"
                try:
                    params = (
                        json.loads(r["parameters"])
                        if isinstance(r["parameters"], str)
                        else r["parameters"]
                    )
                except Exception:
                    params = str(r.get("parameters", ""))

                st.markdown(
                    f"<div style='border-left:4px solid {color};padding:8px 14px;"
                    f"margin:4px 0;background:#f9fafb;border-radius:0 8px 8px 0'>"
                    f"<b>{r['signal_date']}</b> &nbsp; {r['signal_type']} &nbsp;"
                    f"<span style='color:{color}'>{r['signal_direction']}</span>"
                    f" &nbsp; strength={float(r['strength']):.2f}"
                    f" &nbsp; giá={float(r['close_price']):,.2f}</div>",
                    unsafe_allow_html=True,
                )
                st.json(params)


# ══════════════════════════════════════════════════════════════════════════════
# Page3 — Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class Page3:
    """
    Orchestrates Tab 3 — Signal Screener.

    Usage (from app entry-point):
        page = Page3(signal_svc)
        page.render()
    """

    def __init__(self, signal_svc) -> None:
        self._signal_svc    = signal_svc
        self._filter_bar    = _FilterBar()
        self._summary_sec   = _SummaryMetricsSection()
        self._dist_sec      = _DistributionSection()
        self._table_sec     = _SignalTableSection()
        self._detail_sec    = _DetailJsonSection()

    def render(self) -> None:
        st.subheader("🔔 Screener tín hiệu giao dịch")

        # Filter controls + search trigger
        self._filter_bar.render(self._signal_svc)

        result = _FilterBar.get_result()
        if result.empty:
            st.info("Chưa có kết quả. Nhấn 'Tìm tín hiệu' để bắt đầu.")
            return

        # Result sections (only rendered when data exists)
        self._summary_sec.render(result)
        self._dist_sec.render(result)
        self._table_sec.render(result)
        self._detail_sec.render(result)


# ── Backward-compatible module-level entry point ──────────────────────────────

def render(signal_svc) -> None:
    """Backward-compatible shim — delegates to Page3."""
    Page3(signal_svc).render()