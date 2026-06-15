# src/interfaces/page3.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — Signals Screener
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.interfaces.helpers import ALL_SIGNAL_TYPES


def render(signal_svc) -> None:
    st.subheader("🔔 Screener tín hiệu giao dịch")

    # ── Filters ───────────────────────────────────────────────────────────────
    f1, f2, f3, f4, f5 = st.columns([1.2, 1, 1, 1.5, 1])

    with f1:
        scr_market    = st.selectbox("Sàn", ["HOSE", "HNX", "UPCOM", "Tất cả"], key="scr_mkt")
    with f2:
        scr_direction = st.selectbox("Chiều", ["Tất cả", "BUY", "SELL"], key="scr_dir")
    with f3:
        scr_strength  = st.slider("Strength tối thiểu", 0.0, 1.0, 0.2, 0.05, key="scr_str")
    with f4:
        scr_types = st.multiselect(
            "Loại tín hiệu (trống = tất cả)",
            ALL_SIGNAL_TYPES,
            default=[],
            key="scr_types",
        )
    with f5:
        scr_date = st.date_input("Ngày (trống = mới nhất)", value=None, key="scr_date")

    if st.button("🔍 Tìm tín hiệu", type="primary", key="scr_search"):
        with st.spinner("Đang truy vấn…"):
            try:
                result = signal_svc.get_latest_signals(
                    market       = None if scr_market == "Tất cả" else scr_market,
                    date         = scr_date.strftime("%Y-%m-%d") if scr_date else None,
                    direction    = None if scr_direction == "Tất cả" else scr_direction,
                    min_strength = scr_strength,
                    signal_types = scr_types or None,
                    limit        = 300,
                )
                st.session_state["scr_result"] = result
            except Exception as e:
                st.error(f"Lỗi truy vấn: {e}")
                st.session_state["scr_result"] = pd.DataFrame()

    result: pd.DataFrame = st.session_state.get("scr_result", pd.DataFrame())

    if result.empty:
        st.info("Chưa có kết quả. Nhấn 'Tìm tín hiệu' để bắt đầu.")
        return

    # ── Summary metrics ───────────────────────────────────────────────────────
    buy_n  = int((result["signal_direction"] == "BUY").sum())
    sell_n = int((result["signal_direction"] == "SELL").sum())

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Tổng tín hiệu", len(result))
    s2.metric("🟢 BUY",        buy_n)
    s3.metric("🔴 SELL",       sell_n)
    s4.metric("Strength TB",   f"{result['strength'].mean():.2f}")

    # ── Distribution bar chart ────────────────────────────────────────────────
    type_counts = (
        result
        .groupby(["signal_type", "signal_direction"])
        .size()
        .reset_index(name="count")
    )
    if not type_counts.empty:
        with st.expander("Phân bổ theo loại tín hiệu", expanded=True):
            pivot = (
                type_counts
                .pivot(index="signal_type", columns="signal_direction", values="count")
                .fillna(0)
            )
            st.bar_chart(pivot)

    # ── Main table ────────────────────────────────────────────────────────────
    st.markdown("---")
    want_cols = [
        "signal_date", "symbol", "stock_name", "market",
        "signal_type", "signal_direction", "strength", "close_price",
    ]
    avail = [c for c in want_cols if c in result.columns]
    disp  = result[avail].copy().rename(columns={
        "signal_date"     : "Ngày",
        "symbol"          : "Mã",
        "stock_name"      : "Tên",
        "market"          : "Sàn",
        "signal_type"     : "Loại tín hiệu",
        "signal_direction": "Chiều",
        "strength"        : "Strength",
        "close_price"     : "Giá",
    })
    disp = disp.sort_values(["Ngày", "Strength"], ascending=[False, False]).reset_index(drop=True)

    def _dir_color(val: str) -> str:
        if val == "BUY":
            return "background-color:#d1fae5;color:#065f46;font-weight:600"
        if val == "SELL":
            return "background-color:#fee2e2;color:#991b1b;font-weight:600"
        return ""

    styled = disp.style.format({"Strength": "{:.2%}", "Giá": "{:,.2f}"})
    if "Chiều" in disp.columns:
        styled = styled.map(_dir_color, subset=["Chiều"])

    st.dataframe(styled, width='stretch', height=460)

    # ── Detail JSON ───────────────────────────────────────────────────────────
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
                f"<span style='color:{color}'>{r['signal_direction']}</span> &nbsp;"
                f"strength={float(r['strength']):.2f} &nbsp; "
                f"giá={float(r['close_price']):,.2f}</div>",
                unsafe_allow_html=True,
            )
            st.json(params)