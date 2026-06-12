# src/interfaces/page1.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — Data Management: Sync · Indicators · Signals · Sector Pipeline
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import streamlit as st

# ── Shared CSS injected once ──────────────────────────────────────────────────
_CSS = """
<style>
/* Section header strip */
.pg1-section-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    background: #f0f2f6;
    border-left: 3px solid #4f6ef7;
    border-radius: 0 4px 4px 0;
    margin: 16px 0 10px 0;
    font-size: 13px;
    font-weight: 600;
    color: #1e2942;
    letter-spacing: 0.3px;
}

/* DB badge */
.pg1-db-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #1e2942;
    color: #e2e8f0;
    font-size: 12px;
    padding: 5px 12px;
    border-radius: 4px;
    font-family: 'Courier New', monospace;
    letter-spacing: 0.2px;
}
.pg1-db-badge .dot {
    width: 7px; height: 7px;
    background: #4ade80;
    border-radius: 50%;
    display: inline-block;
}

/* Compact card for pipeline steps */
.pg1-card {
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    padding: 12px 14px;
}

/* Slim divider */
.pg1-divider {
    border: none;
    border-top: 1px solid #e9ecef;
    margin: 14px 0;
}
</style>
"""


def _section(icon: str, title: str) -> None:
    st.markdown(
        f'<div class="pg1-section-header">{icon}&nbsp; {title}</div>',
        unsafe_allow_html=True,
    )


def _divider() -> None:
    st.markdown('<hr class="pg1-divider">', unsafe_allow_html=True)


# ── Main render ───────────────────────────────────────────────────────────────

def render(
        db, sync_service, gap_service,
        indicator_svc, signal_svc,
        mf_svc=None, agg_svc=None, scoring_svc=None,
) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    # ── DB connection badge ───────────────────────────────────────────────────
    host = db.engine.url.host or "localhost"
    dbname = db.engine.url.database or "—"
    st.markdown(
        f'<div class="pg1-db-badge">'
        f'<span class="dot"></span>'
        f'{host} &nbsp;/&nbsp; {dbname}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ═════════════════════════════════════════════════════════════════════════
    # SECTION 1 — RAW DATA SYNC
    # ═════════════════════════════════════════════════════════════════════════
    _section("📡", "Raw Data Sync")

    sync_options = {
        "Securities list (all markets)": "sec",
        "Single symbol — OHLC": "ohlc_one",
        "All symbols — OHLC": "ohlc_all",
        "Single symbol — detailed price": "price_one",
        "All symbols — detailed price": "price_all",
        "Maintenance (fill missing days)": "maint",
        "Repair data gaps": "gaps",
    }

    col_task, col_mkt = st.columns([3, 1])
    with col_task:
        task_label = st.selectbox(
            "Task", list(sync_options.keys()), key="s1_task", label_visibility="collapsed"
        )
    task_key = sync_options[task_label]

    maint_markets = ["HOSE", "HNX", "HOSE + HNX"]
    std_markets = ["HOSE", "HNX", "HOSE + HNX"]

    with col_mkt:
        market_opts = maint_markets if task_key == "maint" else std_markets
        t1_market = st.selectbox("Market", market_opts, key="s1_mkt", label_visibility="collapsed")

    # Context inputs row
    needs_symbol = task_key in ("ohlc_one", "price_one")
    needs_dates = task_key in ("ohlc_one", "ohlc_all", "price_one", "price_all")
    needs_mode = task_key == "maint"

    ctx_cols = st.columns(4)
    t1_symbol = "SSI"
    t1_from = datetime(2021, 1, 1).date()
    t1_to = (datetime.now() - timedelta(days=1)).date()
    t1_mode = "price"

    if needs_symbol:
        with ctx_cols[0]:
            t1_symbol = st.text_input("Symbol", value="SSI", key="s1_sym")
    if needs_dates:
        with ctx_cols[1]:
            t1_from = st.date_input("From", value=datetime(2021, 1, 1), key="s1_from")
        with ctx_cols[2]:
            t1_to = st.date_input("To", value=datetime.now() - timedelta(days=1), key="s1_to")
    if needs_mode:
        with ctx_cols[0]:
            t1_mode = st.selectbox("Data type", ["price"], key="s1_mode")

    if st.button("▶ Run Sync", type="primary", key="s1_run"):
        st.session_state.log_messages = []
        with st.status(f"Running: {task_label}…", expanded=True) as status:
            try:
                f = t1_from.strftime("%d/%m/%Y")
                t = t1_to.strftime("%d/%m/%Y")
                s = t1_symbol.strip().upper()

                markets = ["HOSE", "HNX"] if t1_market == "HOSE + HNX" else [t1_market]

                match task_key:
                    case "sec":
                        sync_service.sync_all_markets()
                    case "ohlc_one":
                        sync_service.sync_one_ohlc(s, f, t)
                    case "ohlc_all":
                        for mkt in markets:
                            st.write(f"⏳ OHLC — {mkt}…")
                            sync_service.sync_all_ohlc(mkt, f, t)
                    case "price_one":
                        sync_service.sync_one_stock_price(s, f, t)
                    case "price_all":
                        for mkt in markets:
                            st.write(f"⏳ Prices — {mkt}…")
                            sync_service.sync_all_stock_prices(mkt, f)
                    case "maint":
                        for mkt in markets:
                            st.write(f"⏳ Maintenance — {mkt}…")
                            sync_service.maintenance_sync(mkt, t1_mode)
                    case "gaps":
                        for mkt in markets:
                            st.write(f"⏳ Gap repair — {mkt}…")
                            gap_service.repair_all_gaps(mkt)

                status.update(label="✅ Done", state="complete", expanded=False)
                st.cache_data.clear()
            except Exception as e:
                logging.exception(e)
                status.update(label=f"❌ Error: {e}", state="error")

    _divider()

    # ═════════════════════════════════════════════════════════════════════════
    # SECTION 2 — INDICATORS & SIGNALS  (side by side)
    # ═════════════════════════════════════════════════════════════════════════
    _section("🧮", "Indicators & Signals")

    ind_col, sig_col = st.columns(2, gap="large")

    # ── Indicators ────────────────────────────────────────────────────────────
    with ind_col:
        st.markdown("**Technical Indicators**")
        i_mode = st.radio(
            "Mode", ["Maintenance", "Single symbol", "Full market"],
            horizontal=True, key="i_mode",
        )
        r1, r2 = st.columns(2)
        with r1:
            i_mkt = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], key="i_mkt")
        with r2:
            i_sym = (
                st.text_input("Symbol", value="SSI", key="i_sym")
                if i_mode == "Single symbol" else ""
            )
        i_date = (
            st.date_input("From date (blank = all history)", value=None, key="i_date")
            if i_mode != "Maintenance" else None
        )

        if st.button("▶ Compute Indicators", use_container_width=True, key="btn_ind"):
            with st.status("Computing…") as s:
                fd = i_date.strftime("%Y-%m-%d") if i_date else None
                i_markets = ["HOSE", "HNX"] if i_mkt == "HOSE + HNX" else [i_mkt]
                match i_mode:
                    case "Maintenance":
                        for mkt in i_markets:
                            indicator_svc.run_maintenance(mkt)
                    case "Single symbol":
                        indicator_svc.run_one(i_sym.strip().upper(), fd)
                    case "Full market":
                        for mkt in i_markets:
                            indicator_svc.run_all(mkt, fd)
                s.update(label="✅ Done", state="complete")

    # ── Signals ───────────────────────────────────────────────────────────────
    with sig_col:
        st.markdown("**Trading Signals**")
        s_mode = st.radio(
            "Mode", ["Maintenance", "Single symbol", "Full market"],
            horizontal=True, key="s_mode",
        )
        r1, r2 = st.columns(2)
        with r1:
            s_mkt = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], key="s_mkt")
        with r2:
            s_sym = (
                st.text_input("Symbol", value="SSI", key="s_sym")
                if s_mode == "Single symbol" else ""
            )
        s_date = (
            st.date_input("From date (blank = all history)", value=None, key="s_date")
            if s_mode != "Maintenance" else None
        )

        if st.button("▶ Detect Signals", use_container_width=True, key="btn_sig"):
            with st.status("Scanning…") as s:
                fd = s_date.strftime("%Y-%m-%d") if s_date else None
                s_markets = ["HOSE", "HNX"] if s_mkt == "HOSE + HNX" else [s_mkt]
                match s_mode:
                    case "Maintenance":
                        for mkt in s_markets:
                            signal_svc.run_maintenance(mkt)
                    case "Single symbol":
                        signal_svc.run_one(s_sym.strip().upper(), fd)
                    case "Full market":
                        for mkt in s_markets:
                            signal_svc.run_all(mkt, fd)
                s.update(label="✅ Done", state="complete")

    _divider()

    # ═════════════════════════════════════════════════════════════════════════
    # SECTION 3 — INDEX DATA
    # ═════════════════════════════════════════════════════════════════════════
    _section("📊", "Index Data")

    idx_list_col, idx_daily_col = st.columns(2, gap="large")

    with idx_list_col:
        st.markdown("**Index List**")
        il_mkt = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], key="il_mkt")
        c1, c2 = st.columns(2)

        with c1:
            if st.button("Sync selected", use_container_width=True, key="btn_il_one"):
                with st.status(f"Syncing {il_mkt} index list…") as s:
                    il_markets = ["HOSE", "HNX"] if il_mkt == "HOSE + HNX" else [il_mkt]
                    ok = all(sync_service.fetch_index_list(m) for m in il_markets)
                    if ok:
                        s.update(label="✅ Done", state="complete")
                    else:
                        s.update(label="❌ Failed", state="error")

        with c2:
            if st.button("Sync all markets", use_container_width=True, key="btn_il_all"):
                with st.status("Syncing all markets…") as s:
                    ok = sync_service.sync_index_lists()
                    if ok:
                        s.update(label="✅ Done", state="complete")
                    else:
                        s.update(label="⚠️ Partial failure", state="error")

    with idx_daily_col:
        st.markdown("**Daily Index History**")
        id_mkt = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], key="id_mkt")
        id_maint = st.checkbox("Maintenance mode (fill missing only)", value=True, key="id_maint")
        id_from = "01/01/2022" if id_maint else st.text_input(
            "From date (dd/mm/yyyy)", value="01/01/2022", key="id_from"
        )

        if st.button("▶ Sync Daily Index", type="primary", use_container_width=True, key="btn_id"):
            with st.status(f"Syncing daily index — {id_mkt}…") as s:
                try:
                    id_markets = ["HOSE", "HNX"] if id_mkt == "HOSE + HNX" else [id_mkt]
                    for mkt in id_markets:
                        sync_service.sync_all_daily_index(
                            market=mkt,
                            from_date=id_from,
                            maintenance_mode=id_maint,
                        )
                    s.update(label="✅ Done", state="complete")
                except Exception as e:
                    s.update(label=f"❌ Error: {e}", state="error")

    _divider()

    # ═════════════════════════════════════════════════════════════════════════
    # SECTION 4 — SECTOR ROTATION PIPELINE
    # ═════════════════════════════════════════════════════════════════════════
    _section("🔄", "Sector Rotation Pipeline")

    if mf_svc is None or agg_svc is None or scoring_svc is None:
        st.info("Sector services not initialised.")
    else:
        mf_col, agg_col, sc_col = st.columns(3, gap="large")

        # ── MF Indicators ─────────────────────────────────────────────────────
        with mf_col:
            st.markdown("**Money Flow Indicators**")
            mf_mode = st.radio(
                "Mode", ["Maintenance", "Single symbol", "Full market"],
                horizontal=False, key="mf_mode",
            )
            mf_mkt = st.selectbox("Market", ["HOSE", "HNX"], key="mf_mkt")
            mf_sym = (
                st.text_input("Symbol", value="SSI", key="mf_sym")
                if mf_mode == "Single symbol" else ""
            )
            mf_date = (
                st.date_input("From date", value=None, key="mf_date")
                if mf_mode != "Maintenance" else None
            )
            if st.button("▶ Run MF", use_container_width=True, key="btn_mf"):
                with st.status("Computing MF…") as s:
                    fd = mf_date.strftime("%Y-%m-%d") if mf_date else None
                    mf_markets = ["HOSE", "HNX"] if mf_mkt == "HOSE + HNX" else [mf_mkt]
                    match mf_mode:
                        case "Maintenance":
                            for mkt in mf_markets:
                                mf_svc.run_maintenance(mkt)
                        case "Single symbol":
                            mf_svc.run_one(mf_sym.strip().upper(), fd)
                        case "Full market":
                            for mkt in mf_markets:
                                mf_svc.run_all(mkt, fd)
                    s.update(label="✅ Done", state="complete")

        # ── Sector Aggregation ────────────────────────────────────────────────
        with agg_col:
            st.markdown("**Sector Aggregation**")
            agg_mode = st.radio(
                "Mode",
                ["Maintenance", "Single date", "Date range", "Full rebuild"],
                horizontal=False, key="agg_mode",
            )
            agg_date = agg_from = agg_to = None
            match agg_mode:
                case "Single date":
                    agg_date = st.date_input("Date", key="agg_date")
                case "Date range":
                    agg_from = st.date_input("From", value=datetime(2024, 1, 1), key="agg_from")
                    agg_to = st.date_input("To", key="agg_to")
                case "Full rebuild":
                    agg_from = st.date_input("From", value=datetime(2021, 1, 1), key="agg_rebuild_from")

            if st.button("▶ Run Aggregation", use_container_width=True, key="btn_agg"):
                with st.status("Aggregating…") as s:
                    match agg_mode:
                        case "Maintenance":
                            n = agg_svc.run_maintenance()
                        case "Single date":
                            n = agg_svc.run_date(agg_date.strftime("%Y-%m-%d"))
                        case "Date range":
                            n = agg_svc.run_range(
                                agg_from.strftime("%Y-%m-%d"),
                                agg_to.strftime("%Y-%m-%d"),
                            )
                        case "Full rebuild":
                            n = agg_svc.run_all(agg_from.strftime("%Y-%m-%d"))
                    s.update(label=f"✅ {n} sector-date rows", state="complete")

        # ── Sector Scoring ────────────────────────────────────────────────────
        with sc_col:
            st.markdown("**Sector Scoring**")
            sc_mode = st.radio(
                "Mode",
                ["Maintenance", "Single date", "Date range", "Full rebuild"],
                horizontal=False, key="sc_mode",
            )
            sc_date = sc_from = sc_to = None
            match sc_mode:
                case "Single date":
                    sc_date = st.date_input("Date", key="sc_date")
                case "Date range":
                    sc_from = st.date_input("From", value=datetime(2024, 1, 1), key="sc_from")
                    sc_to = st.date_input("To", key="sc_to")
                case "Full rebuild":
                    sc_from = st.date_input("From", value=datetime(2021, 1, 1), key="sc_rebuild_from")

            if st.button("▶ Run Scoring", use_container_width=True, key="btn_sc"):
                with st.status("Scoring…") as s:
                    match sc_mode:
                        case "Maintenance":
                            n = scoring_svc.run_maintenance()
                        case "Single date":
                            n = scoring_svc.run_date(sc_date.strftime("%Y-%m-%d"))
                        case "Date range":
                            n = scoring_svc.run_range(
                                sc_from.strftime("%Y-%m-%d"),
                                sc_to.strftime("%Y-%m-%d"),
                            )
                        case "Full rebuild":
                            n = scoring_svc.run_all(sc_from.strftime("%Y-%m-%d"))
                    s.update(label=f"✅ {n} sector rows", state="complete")

        # ── One-click pipeline ────────────────────────────────────────────────
        _divider()
        pipe_col, info_col = st.columns([2, 3])
        with pipe_col:
            st.markdown("**⚡ Full Pipeline — Maintenance**")
            p_mkt = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], key="pipe_mkt")
            if st.button(
                    "🚀 Run Full Pipeline",
                    type="primary",
                    use_container_width=True,
                    key="btn_pipeline",
            ):
                st.session_state.log_messages = []
                with st.status("Running full pipeline…", expanded=True) as status:
                    try:
                        st.write("1/3 · Money Flow Indicators")
                        p_markets = ["HOSE", "HNX"] if p_mkt == "HOSE + HNX" else [p_mkt]
                        for mkt in p_markets:
                            mf_svc.run_maintenance(mkt)
                        st.write("2/3 · Sector Aggregation")
                        agg_svc.run_maintenance()
                        st.write("3/3 · Sector Scoring")
                        scoring_svc.run_maintenance()
                        status.update(label="✅ Pipeline complete", state="complete", expanded=False)
                        st.cache_data.clear()
                    except Exception as e:
                        logging.exception(e)
                        status.update(label=f"❌ {e}", state="error")

        with info_col:
            st.markdown(
                """
                **Execution order**  
                `MF Indicators` → `Sector Aggregation` → `Sector Scoring`  

                Maintenance mode only processes dates not yet in the DB —  
                safe to run daily after market close.
                """,
                unsafe_allow_html=False,
            )

    _divider()

    # ── System logs ───────────────────────────────────────────────────────────
    with st.expander("🖥 System logs", expanded=False):
        logs = st.session_state.get("log_messages", [])
        if logs:
            st.code("\n".join(logs), language="text")
        else:
            st.caption("No logs yet.")