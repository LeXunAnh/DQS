# src/interfaces/page2.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Biểu đồ giá (TradingView lightweight) + Point & Figure
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st
from matplotlib import pyplot as plt

from src.services.pnf_service import PNFService
from src.database.handler import DatabaseHandler
from src.interfaces.helpers import (
    ALL_SIGNAL_TYPES,
    MA_COLORS,
    MA_PERIODS,
    build_ma_series,
    build_markers,
    compute_adj_prices,
    render_price_chart,
)

_PERIOD_DAYS: dict[str, int] = {
    "1 tháng": 30,
    "3 tháng": 90,
    "6 tháng": 180,
    "1 năm"  : 365,
    "2 năm"  : 730,
    "Toàn bộ": 3650,
}


def render(db, symbols_df: pd.DataFrame, has_data: bool) -> None:
    if not has_data:
        st.warning(
            "Chưa có dữ liệu. Hãy đồng bộ danh mục securities ở Tab Đồng bộ trước."
        )
        return

    # ── Controls ──────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns([0.80, 1, 0.6, 1.3, 1.7])

    with c1:
        sym_list   = symbols_df["symbol"].tolist()
        default_ix = sym_list.index("SSI") if "SSI" in sym_list else 0
        t2_symbol  = st.selectbox("Mã chứng khoán", sym_list, index=default_ix, key="t2_sym")

    with c2:
        t2_chart_type = st.selectbox(
            "Loại biểu đồ", ["Nến (Candlestick)", "Đường (Close)"], key="t2_chart_type"
        )

    with c3:
        t2_period = st.selectbox(
            "Chu kỳ",
            list(_PERIOD_DAYS.keys()),
            index=3,
            key="t2_period",
        )

    with c4:
        t2_mas = st.multiselect(
            "Đường MA overlay",
            list(MA_PERIODS.keys()),
            default=["MA20", "MA50"],
            key="t2_mas",
        )

    with c5:
        t2_sig_filter = st.multiselect(
            "Hiển thị tín hiệu",
            ALL_SIGNAL_TYPES,
            default=["MA_GOLDEN_CROSS"],
            key="t2_sig_filter",
        )

    # ── PnF settings ──────────────────────────────────────────────────────────
    st.markdown("P&F Settings")
    cA, cB, cC, cD = st.columns(4)

    with cA:
        pnf_method   = st.selectbox("Method", ["h/l", "ohlc", "l/h", "hlc", "cl"], key="pnf_method")
    with cB:
        pnf_reversal = st.number_input("Reversal", 1, 5, 3, key="pnf_rev")
    with cC:
        pnf_scaling  = st.selectbox("Scaling", ["log", "abs", "cla", "atr"], key="pnf_scaling")
    with cD:
        pnf_boxsize  = st.number_input("Boxsize", value=2.0, key="pnf_bs")

    pnf_show_bo = st.checkbox("Breakouts",  True)
    pnf_show_tl = st.checkbox("Trendlines", False)

    # ── Date range ────────────────────────────────────────────────────────────
    today      = datetime.now().date()
    start_date = today - timedelta(days=_PERIOD_DAYS[t2_period])
    t2_show_sig = len(t2_sig_filter) > 0

    # ── Fetch & process ───────────────────────────────────────────────────────
    raw_df = db.fetch_price_with_warmup(t2_symbol, start_date, today)

    if raw_df.empty:
        st.warning(f"Không có dữ liệu giá cho {t2_symbol}.")
        return

    raw_adj  = compute_adj_prices(raw_df)
    price_df = raw_adj[raw_adj["trading_date"] >= start_date].reset_index(drop=True)

    ind_df = db.fetch_indicator_data(t2_symbol, start_date, today)
    if not ind_df.empty:
        price_df = price_df.merge(ind_df, on="trading_date", how="left")
    else:
        price_df["vol_ma20"] = np.nan

    if price_df.empty:
        st.warning("Không đủ dữ liệu sau khi filter theo ngày.")
        return

    # ── Metric cards ──────────────────────────────────────────────────────────
    last = price_df.iloc[-1]
    prev = price_df.iloc[-2] if len(price_df) > 1 else last
    chg      = float(last["adj_close"]) - float(prev["adj_close"])
    chg_pct  = chg / float(prev["adj_close"]) * 100 if prev["adj_close"] else 0

    info_row   = symbols_df[symbols_df["symbol"] == t2_symbol]
    stock_name = info_row["stock_name"].values[0] if not info_row.empty else t2_symbol

    st.markdown(
        f"#### {t2_symbol} &nbsp;"
        f"<span style='font-size:14px;color:#6b7280'>{stock_name}</span>",
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Đóng cửa",        f"{last['adj_close']:,.0f}",  f"{chg:+.2f} ({chg_pct:+.0f}%)")
    m2.metric("Cao nhất",        f"{last['adj_high']:,.0f}")
    m3.metric("Thấp nhất",       f"{last['adj_low']:,.0f}")
    m4.metric("Khối lượng",      f"{last['total_match_vol']/1e6:.2f}M")
    m5.metric("Khối ngoại mua",  f"{last['foreign_buy_vol_total']/1e6:.2f}M")
    m6.metric("Khối ngoại bán",  f"{last['foreign_sell_vol_total']/1e6:.2f}M")

    # ── Build overlays & markers ──────────────────────────────────────────────
    ma_series = build_ma_series(raw_adj, t2_mas, start_date) if t2_mas else []

    sig_df = pd.DataFrame()
    if t2_show_sig:
        sig_df = db.fetch_signals_for_chart(t2_symbol, start_date, today)
        if not sig_df.empty:
            sig_df = sig_df[sig_df["signal_type"].isin(t2_sig_filter)]

    markers = build_markers(sig_df) if not sig_df.empty else []

    # ── Layout: Chart (left) + PnF (right) ───────────────────────────────────
    col_left, col_right = st.columns([3, 2])

    with col_left:
        chart_key = (
            f"c_{t2_symbol}_{start_date}_{t2_chart_type}"
            f"_{''.join(t2_mas)}_{t2_show_sig}"
        )
        render_price_chart(price_df, ma_series, markers, t2_chart_type, chart_key)

        if t2_mas:
            parts = []
            for ma in t2_mas:
                n    = MA_PERIODS[ma]
                vals = raw_adj["adj_close"].rolling(n, min_periods=n).mean().dropna()
                v    = f"{vals.iloc[-1]:,.2f}" if not vals.empty else "—"
                parts.append(
                    f"<span style='background:{MA_COLORS[ma]};color:#fff;"
                    f"padding:2px 9px;border-radius:10px;"
                    f"font-size:12px;margin:2px'>{ma}: {v}</span>"
                )
            st.markdown(" ".join(parts), unsafe_allow_html=True)

    with col_right:
        with st.spinner("Đang tính toán P&F..."):
            try:
                pnf_svc = PNFService(db)
                chart   = pnf_svc.build_chart(
                    t2_symbol,
                    method  = pnf_method,
                    reversal= pnf_reversal,
                    boxsize = pnf_boxsize,
                    scaling = pnf_scaling,
                )
                fig = PNFService.get_plot(
                    chart,
                    show_breakouts  = pnf_show_bo,
                    show_trendlines = pnf_show_tl,
                )
                st.pyplot(fig)
                plt.close(fig)
            except Exception as e:
                st.error(f"Lỗi P&F: {e}")

    # ── Signals table ─────────────────────────────────────────────────────────
    st.markdown("Signals")
    col_signal1, _ = st.columns(2)

    with col_signal1:
        if t2_show_sig and not sig_df.empty:
            st.markdown(f"#### Có {len(sig_df)} tín hiệu")
            t_disp = sig_df[
                ["signal_date", "signal_type", "signal_direction", "strength", "close_price"]
            ].copy()
            t_disp.columns = ["Ngày", "Loại tín hiệu", "Chiều", "Strength", "Giá"]
            t_disp = t_disp.sort_values("Ngày", ascending=False).reset_index(drop=True)
            st.dataframe(
                t_disp.style.format({"Strength": "{:.2%}", "Giá": "{:,.2f}"}),
                use_container_width=True,
                height=320,
            )
        elif t2_show_sig:
            st.info("Không có tín hiệu nào trong kỳ.")