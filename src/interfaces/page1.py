# src/interfaces/page1.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — Đồng bộ dữ liệu · Indicators · Signals
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import streamlit as st


def render(db, sync_service, gap_service, indicator_svc, signal_svc, mf_svc=None, agg_svc=None, scoring_svc=None) -> None:
    st.info(
        f"🖥️ **Database:** `{db.engine.url.host}` | "
        f"**Schema:** `{db.engine.url.database}`"
    )

    # ── PHẦN 1: ĐỒNG BỘ DỮ LIỆU THÔ ────────────────────────────────────────
    st.subheader("1. Đồng bộ dữ liệu")

    col1, col2 = st.columns([2, 1])
    with col1:
        func = st.selectbox(
            "Tác vụ đồng bộ",
            [
                "Đồng bộ danh mục securities (tất cả sàn)",
                "Đồng bộ 1 mã OHLC",
                "Đồng bộ tất cả mã OHLC",
                "Đồng bộ 1 mã giá chi tiết",
                "Đồng bộ tất cả mã giá chi tiết",
                "Bảo trì (cập nhật thiếu)",
                "Vá lỗ hổng dữ liệu",
            ],
            key="sys_sync_func",
        )
    with col2:
        # Nếu là tác vụ bảo trì, cho phép chọn thêm option HOSE + HNX
        if func == "Bảo trì (cập nhật thiếu)":
            market_options = ["HOSE", "HNX", "HOSE + HNX", "UPCOM"]
        else:
            market_options = ["HOSE", "HNX", "UPCOM"]

        t1_market = st.selectbox("Sàn", market_options, key="sys_market")

    # Dynamic inputs
    t1_from = datetime(2021, 1, 1).date()
    t1_to   = (datetime.now() - timedelta(days=1)).date()
    t1_symbol = "SSI"
    t1_mode   = "ohlc"

    c_a, c_b, c_c = st.columns(3)
    if "1 mã" in func:
        with c_a:
            t1_symbol = st.text_input("Mã chứng khoán", value="SSI", key="sys_sym")

    if "OHLC" in func or "giá chi tiết" in func:
        with c_b:
            t1_from = st.date_input("Từ ngày", value=datetime(2021, 1, 1), key="sys_from")
        with c_c:
            t1_to = st.date_input(
                "Đến ngày",
                value=datetime.now() - timedelta(days=1),
                key="sys_to",
            )

    if func == "Bảo trì (cập nhật thiếu)":
        with c_b:
            t1_mode = st.selectbox(
                "Loại dữ liệu", ["price"], key="sys_maint_mode"
            )

    if st.button("▶️ Chạy Đồng bộ", type="primary", use_container_width=True):
        st.session_state.log_messages = []
        with st.status(f"Đang thực hiện: {func}...", expanded=True) as status:
            try:
                f = t1_from.strftime("%d/%m/%Y")
                t = t1_to.strftime("%d/%m/%Y")
                s = t1_symbol.strip().upper()

                if func == "Đồng bộ danh mục securities (tất cả sàn)":
                    sync_service.sync_all_markets()
                elif func == "Đồng bộ 1 mã OHLC":
                    sync_service.sync_one_ohlc(s, f, t)
                elif func == "Đồng bộ tất cả mã OHLC":
                    sync_service.sync_all_ohlc(t1_market, f, t)
                elif func == "Đồng bộ 1 mã giá chi tiết":
                    sync_service.sync_one_stock_price(s, f, t)
                elif func == "Đồng bộ tất cả mã giá chi tiết":
                    sync_service.sync_all_stock_prices(t1_market, f)
                elif func == "Bảo trì (cập nhật thiếu)":
                    if t1_market == "HOSE + HNX":
                        st.write("⏳ Đang bảo trì sàn HOSE...")
                        sync_service.maintenance_sync("HOSE", t1_mode)
                        st.write("⏳ Đang bảo trì sàn HNX...")
                        sync_service.maintenance_sync("HNX", t1_mode)
                    else:
                        sync_service.maintenance_sync(t1_market, t1_mode)
                elif func == "Vá lỗ hổng dữ liệu":
                    gap_service.repair_all_gaps(t1_market)

                status.update(label="✅ Đồng bộ hoàn tất!", state="complete", expanded=False)
                st.cache_data.clear()
            except Exception as e:
                logging.exception(e)
                status.update(label=f"❌ Lỗi: {e}", state="error")

    st.markdown("---")

    # ── PHẦN 2: INDICATORS & SIGNALS ─────────────────────────────────────────
    st.subheader("2. Xử lý Indicators & Signals")

    col_ind, col_sig = st.columns(2)

    with col_ind:
        st.markdown("#### 🧮 Tính Indicators")
        i_mode = st.radio(
            "Chế độ Indicators",
            ["Bảo trì (thiếu)", "1 mã", "Toàn sàn"],
            horizontal=True,
            key="i_m",
        )
        i_mkt  = st.selectbox("Sàn", ["HOSE", "HNX", "UPCOM"], key="i_mkt")
        i_sym  = st.text_input("Mã", value="SSI", key="i_s") if i_mode == "1 mã" else ""
        i_date = (
            st.date_input("Từ ngày (trống=all)", value=None, key="i_d")
            if i_mode != "Bảo trì (thiếu)"
            else None
        )

        if st.button("Tính Indicators", use_container_width=True):
            with st.status("Đang tính...") as s:
                fd = i_date.strftime("%Y-%m-%d") if i_date else None
                if i_mode == "Bảo trì (thiếu)":
                    indicator_svc.run_maintenance(i_mkt)
                elif i_mode == "1 mã":
                    indicator_svc.run_one(i_sym.upper(), fd)
                else:
                    indicator_svc.run_all(i_mkt, fd)
                s.update(label="✅ Xong", state="complete")

    with col_sig:
        st.markdown("#### 🔔 Phát hiện Signals")
        s_mode = st.radio(
            "Chế độ Signals",
            ["Bảo trì (thiếu)", "1 mã", "Toàn sàn"],
            horizontal=True,
            key="s_m",
        )
        s_mkt  = st.selectbox("Sàn", ["HOSE", "HNX", "UPCOM"], key="s_mkt")
        s_sym  = st.text_input("Mã", value="SSI", key="s_s") if s_mode == "1 mã" else ""
        s_date = (
            st.date_input("Từ ngày (trống=all)", value=None, key="s_d")
            if s_mode != "Bảo trì (thiếu)"
            else None
        )

        if st.button("Tìm Signals", use_container_width=True):
            with st.status("Đang quét...") as s:
                fd = s_date.strftime("%Y-%m-%d") if s_date else None
                if s_mode == "Bảo trì (thiếu)":
                    signal_svc.run_maintenance(s_mkt)
                elif s_mode == "1 mã":
                    signal_svc.run_one(s_sym.upper(), fd)
                else:
                    signal_svc.run_all(s_mkt, fd)
                s.update(label="✅ Xong", state="complete")

    st.subheader("3. Index Management (List & Daily Data)")

    # Tạo 2 cột lớn để phân tách Quản lý Danh mục và Quản lý Dữ liệu Lịch sử
    main_col1, main_col2 = st.columns(2)

    # --- CỘT 1: INDEX LIST MANAGEMENT ---
    with main_col1:
        st.markdown("#### 📋 Index List Management")

        selected_market = st.selectbox(
            "Select Market for List",
            ['HOSE', 'HNX', 'UPCOM'],
            key='index_market'
        )

        if st.button(
                "🔄 Update Index List",
                use_container_width=True,
                key='btn_update_index'
        ):
            st.session_state.log_messages = []
            with st.spinner(f"Đang cập nhật index list {selected_market}..."):
                success = sync_service.fetch_index_list(selected_market)
                if success:
                    st.session_state.log_messages.append(f"✅ Đã cập nhật index list {selected_market}")
                else:
                    st.session_state.log_messages.append(f"❌ Lỗi cập nhật index list {selected_market}")

        if st.button(
                "🚀 Sync All Markets List",
                use_container_width=True,
                key='btn_sync_all_index'
        ):
            st.session_state.log_messages = []
            with st.spinner("Đang sync toàn bộ index list..."):
                success = sync_service.sync_index_lists()
                if success:
                    st.session_state.log_messages.append("✅ Sync toàn bộ index list thành công")
                else:
                    st.session_state.log_messages.append("⚠️ Một số market sync thất bại")

    # --- CỘT 2: DAILY INDEX DATA MANAGEMENT (TÍNH NĂNG MỚI) ---
    with main_col2:
        st.markdown("#### 📈 Daily Index Data Management")

        # Chọn sàn cần đồng bộ dữ liệu lịch sử
        daily_market = st.selectbox(
            "Select Market for Daily Data",
            ['HOSE', 'HNX', 'UPCOM'],
            key='daily_index_market'
        )

        # Tùy chọn Chế độ Bảo trì (Chỉ sync bù ngày thiếu)
        maintenance_mode = st.checkbox(
            "🔧 Chế độ bảo trì (Chỉ cập nhật ngày còn thiếu)",
            value=True,
            key='chk_index_maintenance',
            help="Nếu bật, hệ thống tự động kiểm tra ngày mới nhất trong DB để sync bù. Nếu tắt, sẽ cào lại toàn bộ từ Ngày bắt đầu."
        )

        # Định hình Ngày bắt đầu (Ẩn/Hiện hoặc Vô hiệu hóa tùy theo chế độ bảo trì để UI thông minh hơn)
        if not maintenance_mode:
            start_date_input = st.text_input(
                "📅 Ngày bắt đầu (dd/mm/yyyy)",
                value="01/01/2022",
                key='txt_index_start_date'
            )
        else:
            st.info("💡 Hệ thống sẽ tự động quét từ ngày gần nhất trong DB.")
            start_date_input = "01/01/2022"  # Fallback mặc định bên dưới nếu DB trống

        # Nút kích hoạt Tiến trình Đồng bộ
        if st.button(
                "🚀 Sync Daily Index Data",
                use_container_width=True,
                key='btn_sync_daily_index',
                type="primary"  # Tạo màu nổi bật cho hành động chính
        ):
            st.session_state.log_messages = []
            with st.spinner(f"Đang chạy đồng bộ Daily Index sàn {daily_market}..."):
                try:
                    sync_service.sync_all_daily_index(
                        market=daily_market,
                        from_date=start_date_input,
                        maintenance_mode=maintenance_mode
                    )
                    st.session_state.log_messages.append(
                        f"🎉 Hoàn thành tiến trình đồng bộ Daily Index cho sàn {daily_market}!"
                    )
                except Exception as e:
                    st.session_state.log_messages.append(f"❌ Quá trình đồng bộ xảy ra lỗi: {str(e)}")

        # ── PHẦN 3: SECTOR ROTATION PIPELINE ─────────────────────────────────────
    st.subheader("3. Sector Rotation Pipeline")

    if mf_svc is None or agg_svc is None or scoring_svc is None:
        st.info("Sector Rotation services chưa được khởi tạo.")
    else:
        sr_col1, sr_col2, sr_col3 = st.columns(3)

        # ── MF Indicators ─────────────────────────────────────────────────────
        with sr_col1:
            st.markdown("#### 💹 MF Indicators")
            mf_mode = st.radio(
                "Chế độ MF",
                ["Bảo trì (thiếu)", "1 mã", "Toàn sàn"],
                horizontal=True, key="mf_m",
            )
            mf_mkt = st.selectbox("Sàn", ["HOSE", "HNX"], key="mf_mkt")
            mf_sym = (
                st.text_input("Mã", value="SSI", key="mf_s")
                if mf_mode == "1 mã" else ""
            )
            mf_date = (
                st.date_input("Từ ngày (trống=all)", value=None, key="mf_d")
                if mf_mode != "Bảo trì (thiếu)" else None
            )

            if st.button("▶️ Chạy MF", use_container_width=True, key="btn_mf"):
                with st.status("Đang tính MF indicators...") as s:
                    fd = mf_date.strftime("%Y-%m-%d") if mf_date else None
                    if mf_mode == "Bảo trì (thiếu)":
                        mf_svc.run_maintenance(mf_mkt)
                    elif mf_mode == "1 mã":
                        mf_svc.run_one(mf_sym.upper(), fd)
                    else:
                        mf_svc.run_all(mf_mkt, fd)
                    s.update(label="✅ Xong", state="complete")

        # ── Sector Aggregation ────────────────────────────────────────────────
        with sr_col2:
            st.markdown("#### 🏭 Sector Aggregation")
            agg_mode = st.radio(
                "Chế độ Aggregation",
                ["Bảo trì (thiếu)", "1 ngày", "Khoảng ngày", "Full rebuild"],
                horizontal=False, key="agg_m",
            )
            agg_date = agg_from = agg_to = None
            if agg_mode == "1 ngày":
                agg_date = st.date_input("Ngày", key="agg_date")
            elif agg_mode == "Khoảng ngày":
                agg_from = st.date_input("Từ ngày", value=datetime(2024, 1, 1), key="agg_from")
                agg_to = st.date_input("Đến ngày", key="agg_to")
            elif agg_mode == "Full rebuild":
                agg_from = st.date_input("Từ ngày", value=datetime(2021, 1, 1), key="agg_rebuild_from")

            if st.button("▶️ Chạy Aggregation", use_container_width=True, key="btn_agg"):
                with st.status("Đang aggregate...") as s:
                    if agg_mode == "Bảo trì (thiếu)":
                        n = agg_svc.run_maintenance()
                    elif agg_mode == "1 ngày":
                        n = agg_svc.run_date(agg_date.strftime("%Y-%m-%d"))
                    elif agg_mode == "Khoảng ngày":
                        n = agg_svc.run_range(
                            agg_from.strftime("%Y-%m-%d"),
                            agg_to.strftime("%Y-%m-%d"),
                        )
                    else:
                        n = agg_svc.run_all(agg_from.strftime("%Y-%m-%d"))
                    s.update(label=f"✅ Xong — {n} sector-date rows", state="complete")

        # ── Sector Scoring ────────────────────────────────────────────────────
        with sr_col3:
            st.markdown("#### 🏆 Sector Scoring")
            sc_mode = st.radio(
                "Chế độ Scoring",
                ["Bảo trì (thiếu)", "1 ngày", "Khoảng ngày", "Full rebuild"],
                horizontal=False, key="sc_m",
            )
            sc_date = sc_from = sc_to = None
            if sc_mode == "1 ngày":
                sc_date = st.date_input("Ngày", key="sc_date")
            elif sc_mode == "Khoảng ngày":
                sc_from = st.date_input("Từ ngày", value=datetime(2024, 1, 1), key="sc_from")
                sc_to = st.date_input("Đến ngày", key="sc_to")
            elif sc_mode == "Full rebuild":
                sc_from = st.date_input("Từ ngày", value=datetime(2021, 1, 1), key="sc_rebuild_from")

            if st.button("▶️ Chạy Scoring", use_container_width=True, key="btn_sc"):
                with st.status("Đang scoring...") as s:
                    if sc_mode == "Bảo trì (thiếu)":
                        n = scoring_svc.run_maintenance()
                    elif sc_mode == "1 ngày":
                        n = scoring_svc.run_date(sc_date.strftime("%Y-%m-%d"))
                    elif sc_mode == "Khoảng ngày":
                        n = scoring_svc.run_range(
                            sc_from.strftime("%Y-%m-%d"),
                            sc_to.strftime("%Y-%m-%d"),
                        )
                    else:
                        n = scoring_svc.run_all(sc_from.strftime("%Y-%m-%d"))
                    s.update(label=f"✅ Xong — {n} sector rows", state="complete")

        # ── One-click full pipeline ───────────────────────────────────────────
        st.markdown("---")
        st.markdown("##### ⚡ Chạy toàn bộ pipeline (Bảo trì)")
        st.caption("MF Indicators → Aggregation → Scoring (chỉ ngày còn thiếu)")

        p_mkt = st.selectbox("Sàn pipeline", ["HOSE", "HNX", "UPCOM"], key="pipe_mkt")
        if st.button("🚀 Chạy Full Pipeline", type="primary",
                     use_container_width=True, key="btn_pipeline"):
            st.session_state.log_messages = []
            with st.status("Đang chạy full pipeline...", expanded=True) as status:
                try:
                    st.write("📊 Bước 1/3: MF Indicators...")
                    mf_svc.run_maintenance(p_mkt)
                    st.write("🏭 Bước 2/3: Sector Aggregation...")
                    agg_svc.run_maintenance()
                    st.write("🏆 Bước 3/3: Sector Scoring...")
                    scoring_svc.run_maintenance()
                    status.update(
                        label="✅ Full pipeline hoàn tất!",
                        state="complete", expanded=False,
                    )
                    st.cache_data.clear()
                except Exception as e:
                    logging.exception(e)
                    status.update(label=f"❌ Lỗi: {e}", state="error")

    st.markdown("---")


    with st.expander("Logs hệ thống"):
        st.code(
            "\n".join(st.session_state.get("log_messages", [])) or "Chưa có log."
        )
