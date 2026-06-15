# src/interfaces/page2.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Biểu đồ giá (TradingView lightweight) + Chỉ số hiệu suất nhanh
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st

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
    "1 năm": 365,
    "2 năm": 730,
    "Toàn bộ": 3650,
}


def render(db, symbols_df: pd.DataFrame, has_data: bool) -> None:
    if not has_data:
        st.warning(
            "Chưa có dữ liệu. Hãy đồng bộ danh mục securities ở Tab Đồng bộ trước."
        )
        return

    # ── Controls ──────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([1.0, 1.2, 1.0, 1.8])

    with c1:
        sym_list = symbols_df["symbol"].tolist()
        default_ix = sym_list.index("SSI") if "SSI" in sym_list else 0
        t2_symbol = st.selectbox("Mã chứng khoán", sym_list, index=default_ix, key="t2_sym")

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
        t2_sig_filter = st.multiselect(
            "Hiển thị tín hiệu",
            ALL_SIGNAL_TYPES,
            default=[],
            key="t2_sig_filter",
        )

    # ── Date range ────────────────────────────────────────────────────────────
    today = datetime.now().date()
    start_date = today - timedelta(days=_PERIOD_DAYS[t2_period])
    t2_show_sig = len(t2_sig_filter) > 0

    # Tự động hiển thị toàn bộ các đường MA Overlay được cấu hình sẵn
    t2_mas = [ma for ma in MA_PERIODS.keys() if ma not in ["MA5", "MA10"]]

    # ── Fetch & process ───────────────────────────────────────────────────────
    raw_df = db.fetch_price_with_warmup(t2_symbol, start_date, today)

    if raw_df.empty:
        st.warning(f"Không có dữ liệu giá cho {t2_symbol}.")
        return

    raw_adj = compute_adj_prices(raw_df)
    price_df = raw_adj[raw_adj["trading_date"] >= start_date].reset_index(drop=True)

    ind_df = db.fetch_indicator_data(t2_symbol, start_date, today)
    if not ind_df.empty:
        price_df = price_df.merge(ind_df, on="trading_date", how="left")
    else:
        price_df["vol_ma20"] = np.nan

    if price_df.empty:
        st.warning("Không đủ dữ liệu sau khi filter theo ngày.")
        return

    # ── Fetch latest MFI / CMF from stock_mf_daily ───────────────────────────
    mfi_val = cmf_val = None
    try:
        from sqlalchemy import text as _sql_text
        with db.engine.connect() as _conn:
            _mf_row = _conn.execute(
                _sql_text("""
                    SELECT mfi, cmf
                    FROM stock_mf_daily
                    WHERE symbol = :sym
                    ORDER BY date DESC
                    LIMIT 1
                """),
                {"sym": t2_symbol},
            ).fetchone()
        if _mf_row:
            mfi_val = float(_mf_row[0]) if _mf_row[0] is not None else None
            cmf_val = float(_mf_row[1]) if _mf_row[1] is not None else None
    except Exception:
        pass

    # ── Metric Calculations ───────────────────────────────────────────────────
    last = price_df.iloc[-1]
    prev = price_df.iloc[-2] if len(price_df) > 1 else last
    chg = float(last["adj_close"]) - float(prev["adj_close"])
    chg_pct = chg / float(prev["adj_close"]) * 100 if prev["adj_close"] else 0

    # Tính toán các chỉ số Trung bình (TB) 1 tuần ~ 5 ngày giao dịch gần nhất
    price_df["ma5_close"] = price_df["adj_close"].rolling(window=5, min_periods=1).mean()
    price_df["ma5_vol"] = price_df["total_match_vol"].rolling(window=5, min_periods=1).mean()
    price_df["net_foreign"] = price_df["foreign_buy_vol_total"] - price_df["foreign_sell_vol_total"]
    price_df["ma5_foreign"] = price_df["net_foreign"].rolling(window=5, min_periods=1).mean()
    price_df["val_ma20"] = price_df["total_match_val"].rolling(window=20, min_periods=1).mean()

    if len(price_df) >= 6:
        # So sánh MA5 phiên hiện tại với MA5 của 5 phiên giao dịch trước đó
        curr_ma_close = price_df["ma5_close"].iloc[-1]
        prev_ma_close = price_df["ma5_close"].iloc[-6]
        price_avg_chg = ((curr_ma_close - prev_ma_close) / prev_ma_close * 100) if prev_ma_close else 0.0

        curr_ma_vol = price_df["ma5_vol"].iloc[-1]
        prev_ma_vol = price_df["ma5_vol"].iloc[-6]
        vol_avg_chg = ((curr_ma_vol - prev_ma_vol) / prev_ma_vol * 100) if prev_ma_vol else 0.0

        curr_ma_fgn = price_df["ma5_foreign"].iloc[-1]
        prev_ma_fgn = price_df["ma5_foreign"].iloc[-6]
        fgn_avg_chg = ((curr_ma_fgn - prev_ma_fgn) / abs(prev_ma_fgn) * 100) if prev_ma_fgn else 0.0
    else:
        price_avg_chg, vol_avg_chg, fgn_avg_chg = 0.0, 0.0, 0.0

    # Tính toán tỷ lệ thanh khoản so với khối lượng trung bình 20 phiên (vol_ma20)
    val_ma20_last = price_df["val_ma20"].iloc[-1] if not price_df.empty else np.nan
    if pd.notna(val_ma20_last) and val_ma20_last > 0:
        liquidity_ratio = last["total_match_val"] / val_ma20_last
    else:
        liquidity_ratio = np.nan

    # Tiêu đề & Thông tin Mã chứng khoán
    info_row = symbols_df[symbols_df["symbol"] == t2_symbol]
    stock_name = info_row["stock_name"].values[0] if not info_row.empty else t2_symbol

    st.markdown(
        f"#### {t2_symbol} &nbsp;"
        f"<span style='font-size:14px;color:#6b7280'>{stock_name}</span>",
        unsafe_allow_html=True,
    )

    # ── Build overlays & markers ──────────────────────────────────────────────
    ma_series = build_ma_series(raw_adj, t2_mas, start_date) if t2_mas else []

    sig_df = pd.DataFrame()
    if t2_show_sig:
        sig_df = db.fetch_signals_for_chart(t2_symbol, start_date, today)
        if not sig_df.empty:
            sig_df = sig_df[sig_df["signal_type"].isin(t2_sig_filter)]

    markers = build_markers(sig_df) if not sig_df.empty else []

    # ── Layout: Chart (Left) + Metric Cards Grid (Right) ──────────────────────
    col_left, col_right = st.columns([5, 4])

    with col_left:
        chart_key = (
            f"c_{t2_symbol}_{start_date}_{t2_chart_type}"
            f"_{''.join(t2_mas)}_{t2_show_sig}"
        )
        render_price_chart(price_df, ma_series, markers, t2_chart_type, chart_key)

        # Hiển thị nhãn giá trị các đường MA phía dưới biểu đồ
        if t2_mas:
            parts = []
            for ma in t2_mas:
                n = MA_PERIODS[ma]
                vals = raw_adj["adj_close"].rolling(n, min_periods=n).mean().dropna()
                v = f"{vals.iloc[-1]:,.2f}" if not vals.empty else "—"
                parts.append(
                    f"<span style='background:{MA_COLORS[ma]};color:#fff;"
                    f"padding:2px 9px;border-radius:10px;"
                    f"font-size:12px;margin:2px'>{ma}: {v}</span>"
                )
            st.markdown(" ".join(parts), unsafe_allow_html=True)

    with col_right:
        st.markdown("<div style='padding-top: 10px;'></div>", unsafe_allow_html=True)

        # Hàng 1: Đóng cửa | Cao nhất | Thấp nhất
        r1_c1, r1_c2, r1_c3 = st.columns(3)
        r1_c1.metric("Đóng cửa", f"{last['adj_close']:,.0f}", f"{chg:+.2f} ({chg_pct:+.2f}%)")
        r1_c2.metric("Cao nhất", f"{last['adj_high']:,.0f}")
        r1_c3.metric("Thấp nhất", f"{last['adj_low']:,.0f}")

        # Hàng 2: Khối lượng | Khối ngoại mua | Khối ngoại bán
        r2_c1, r2_c2, r2_c3 = st.columns(3)
        r2_c1.metric("Khối lượng", f"{last['total_match_vol'] / 1e6:.2f}M")
        r2_c2.metric("Khối ngoại mua", f"{last['foreign_buy_vol_total'] / 1e6:.2f}M")
        r2_c3.metric("Khối ngoại bán", f"{last['foreign_sell_vol_total'] / 1e6:.2f}M")

        # Hàng 3: % tăng giảm giá TB 1 tuần | % tăng giảm khối lượng TB 1 tuần | % tăng giảm khối ngoại
        r3_c1, r3_c2, r3_c3 = st.columns(3)
        r3_c1.metric("% tăng giảm giá TB 1 tuần",f"{price_avg_chg:+.2f}%")
        r3_c2.metric("% tăng giảm khối lượng TB 1 tuần",f"{vol_avg_chg:+.2f}%")
        r3_c3.metric("% tăng giảm khối ngoại TB 1 tuần",f"{fgn_avg_chg:+.2f}%")

        # Hàng 4: MFI | CMF | Tỷ lệ thanh khoản (Mới bổ sung)
        r4_c1, r4_c2, r4_c3 = st.columns(3)
        r4_c1.metric("MFI", f"{mfi_val:.1f}" if mfi_val is not None else "—")
        cmf_pct = cmf_val * 100
        r4_c2.metric("CMF", f"{cmf_pct:.1f}%" if cmf_pct is not None else "—")
        r4_c3.metric("Tỷ lệ thanh khoản",f"{liquidity_ratio:.2f}x" if pd.notna(liquidity_ratio) else "—",)