# src/interfaces/page1.py
# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — Data Management: Sync · Indicators · Signals · Sector Pipeline
#
# OOP Structure
# ─────────────────────────────────────────────────────────────────────────────
# Page1                          ← orchestrator, injected with all services
#   ├── _DbBadge                 ← DB connection status badge
#   ├── SyncSection              ← Raw Data Sync column
#   ├── IndicatorSection         ← Technical Indicators column
#   ├── SignalSection            ← Trading Signals column
#   ├── IndexDataSection         ← Index Data column
#   ├── SectorPipelineSection    ← MF · Aggregation · Scoring + Full Sync
#   └── SystemLogSection         ← Collapsible log expander
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import streamlit as st

# ── Shared CSS ────────────────────────────────────────────────────────────────
_CSS = """
<style>
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
.pg1-divider {
    border: none;
    border-top: 1px solid #e9ecef;
    margin: 14px 0;
}
</style>
"""


# ── Shared UI helpers ─────────────────────────────────────────────────────────

def _section_header(icon: str, title: str) -> None:
    st.markdown(
        f'<div class="pg1-section-header">{icon}&nbsp; {title}</div>',
        unsafe_allow_html=True,
    )


def _divider() -> None:
    st.markdown('<hr class="pg1-divider">', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Section: DB Badge
# ══════════════════════════════════════════════════════════════════════════════

class _DbBadge:
    """Renders the DB connection badge (host / database name)."""

    def __init__(self, db) -> None:
        self._db = db

    def render(self) -> None:
        host   = self._db.engine.url.host or "localhost"
        dbname = self._db.engine.url.database or "—"
        st.markdown(
            f'<div class="pg1-db-badge">'
            f'<span class="dot"></span>'
            f'{host} &nbsp;/&nbsp; {dbname}'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Raw Data Sync
# ══════════════════════════════════════════════════════════════════════════════

class SyncSection:
    """Column 1 — Raw Data Sync tasks."""

    _TASK_OPTIONS: dict[str, str] = {
        "Securities list":         "sec",
        "Single symbol — OHLC":    "ohlc_one",
        "All symbols — OHLC":      "ohlc_all",
        "Single — detailed price": "price_one",
        "All — detailed price":    "price_all",
        "Maintenance":             "maint",
        "Repair gaps":             "gaps",
    }

    def __init__(self, sync_service, gap_service) -> None:
        self._sync = sync_service
        self._gap  = gap_service

    def render(self) -> None:
        st.markdown("**📡 Raw Data Sync**")

        task_label = st.selectbox("Task", list(self._TASK_OPTIONS), index=5, key="s1_task")
        task_key   = self._TASK_OPTIONS[task_label]
        market     = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], index=2, key="s1_mkt")

        needs_symbol = task_key in ("ohlc_one", "price_one")
        needs_dates  = task_key in ("ohlc_one", "ohlc_all", "price_one", "price_all")

        symbol  = st.text_input("Symbol", value="SSI", key="s1_sym") if needs_symbol else "SSI"
        from_dt = st.date_input("From", value=datetime(2021, 1, 1), key="s1_from") if needs_dates else datetime(2021, 1, 1).date()
        to_dt   = st.date_input("To", value=datetime.now() - timedelta(days=1), key="s1_to") if needs_dates else (datetime.now() - timedelta(days=1)).date()

        if st.button("▶ Run Sync", type="primary", use_container_width=True, key="s1_run"):
            st.session_state.log_messages = []
            with st.status(f"{task_label}…", expanded=False) as status:
                try:
                    self._execute(task_key, market, symbol, from_dt, to_dt)
                    status.update(label="✅ Done", state="complete", expanded=False)
                    st.cache_data.clear()
                except Exception as e:
                    logging.exception(e)
                    status.update(label=f"❌ {e}", state="error")

    def _execute(self, task: str, market: str, symbol: str, from_dt, to_dt) -> None:
        f = from_dt.strftime("%d/%m/%Y")
        t = to_dt.strftime("%d/%m/%Y")
        s = symbol.strip().upper()
        markets = ["HOSE", "HNX"] if market == "HOSE + HNX" else [market]

        match task:
            case "sec":
                self._sync.sync_all_markets()
            case "ohlc_one":
                self._sync.sync_one_ohlc(s, f, t)
            case "ohlc_all":
                for mkt in markets:
                    st.write(f"⏳ OHLC — {mkt}…")
                    self._sync.sync_all_ohlc(mkt, f, t)
            case "price_one":
                self._sync.sync_one_stock_price(s, f, t)
            case "price_all":
                for mkt in markets:
                    st.write(f"⏳ Prices — {mkt}…")
                    self._sync.sync_all_stock_prices(mkt, f)
            case "maint":
                for mkt in markets:
                    st.write(f"⏳ Maintenance — {mkt}…")
                    self._sync.maintenance_sync(mkt, "price")
            case "gaps":
                for mkt in markets:
                    st.write(f"⏳ Gap repair — {mkt}…")
                    self._gap.repair_all_gaps(mkt)


# ══════════════════════════════════════════════════════════════════════════════
# Section: Technical Indicators
# ══════════════════════════════════════════════════════════════════════════════

class IndicatorSection:
    """Column 2 — Technical Indicator computation."""

    def __init__(self, indicator_svc) -> None:
        self._svc = indicator_svc

    def render(self) -> None:
        st.markdown("**🧮 Indicators**")

        mode      = st.radio("Mode", ["Maintenance", "Single symbol", "Full market"],
                             horizontal=False, key="i_mode")
        market    = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], index=2, key="i_mkt")
        symbol    = st.text_input("Symbol", value="SSI", key="i_sym") if mode == "Single symbol" else ""
        from_date = st.date_input("From date", value=None, key="i_date") if mode != "Maintenance" else None

        if st.button("▶ Compute", use_container_width=True, key="btn_ind"):
            with st.status("Computing…") as status:
                fd      = from_date.strftime("%Y-%m-%d") if from_date else None
                markets = ["HOSE", "HNX"] if market == "HOSE + HNX" else [market]
                match mode:
                    case "Maintenance":
                        for mkt in markets:
                            self._svc.run_maintenance(mkt)
                    case "Single symbol":
                        self._svc.run_one(symbol.strip().upper(), fd)
                    case "Full market":
                        for mkt in markets:
                            self._svc.run_all(mkt, fd)
                status.update(label="✅ Done", state="complete")


# ══════════════════════════════════════════════════════════════════════════════
# Section: Trading Signals
# ══════════════════════════════════════════════════════════════════════════════

class SignalSection:
    """Column 3 — Signal detection."""

    def __init__(self, signal_svc) -> None:
        self._svc = signal_svc

    def render(self) -> None:
        st.markdown("**🔔 Signals**")

        mode      = st.radio("Mode", ["Maintenance", "Single symbol", "Full market"],
                             horizontal=False, key="s_mode")
        market    = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], index=2, key="s_mkt")
        symbol    = st.text_input("Symbol", value="SSI", key="s_sym") if mode == "Single symbol" else ""
        from_date = st.date_input("From date", value=None, key="s_date") if mode != "Maintenance" else None

        if st.button("▶ Detect", use_container_width=True, key="btn_sig"):
            with st.status("Scanning…") as status:
                fd      = from_date.strftime("%Y-%m-%d") if from_date else None
                markets = ["HOSE", "HNX"] if market == "HOSE + HNX" else [market]
                match mode:
                    case "Maintenance":
                        for mkt in markets:
                            self._svc.run_maintenance(mkt)
                    case "Single symbol":
                        self._svc.run_one(symbol.strip().upper(), fd)
                    case "Full market":
                        for mkt in markets:
                            self._svc.run_all(mkt, fd)
                status.update(label="✅ Done", state="complete")


# ══════════════════════════════════════════════════════════════════════════════
# Section: Index Data
# ══════════════════════════════════════════════════════════════════════════════

class IndexDataSection:
    """Column 4 — Index list sync + daily history."""

    def __init__(self, sync_service) -> None:
        self._sync = sync_service

    def render(self) -> None:
        st.markdown("**📊 Index Data**")
        self._render_list_sync()
        st.markdown("**Daily History**")
        self._render_daily_sync()

    def _render_list_sync(self) -> None:
        market = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], index=2, key="il_mkt")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Sync list", use_container_width=True, key="btn_il_one"):
                with st.spinner("Syncing…"):
                    markets = ["HOSE", "HNX"] if market == "HOSE + HNX" else [market]
                    ok = all(self._sync.fetch_index_list(m) for m in markets)
                    st.success("Done") if ok else st.error("Failed")
        with c2:
            if st.button("Sync all", use_container_width=True, key="btn_il_all"):
                with st.spinner("Syncing…"):
                    ok = self._sync.sync_index_lists()
                    st.success("Done") if ok else st.warning("Partial")

    def _render_daily_sync(self) -> None:
        market         = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], index=2, key="id_mkt")
        mode           = st.radio("Mode", ["Maintenance", "Full sync"], horizontal=True, key="id_mode")
        is_maintenance = mode == "Maintenance"
        from_date      = "01/01/2022" if is_maintenance else st.text_input(
            "From (dd/mm/yyyy)", value="01/01/2022", key="id_from"
        )

        if st.button("▶ Sync Daily Index", type="primary", use_container_width=True, key="btn_id"):
            with st.spinner("Syncing…"):
                try:
                    markets = ["HOSE", "HNX"] if market == "HOSE + HNX" else [market]
                    for mkt in markets:
                        self._sync.sync_all_daily_index(
                            market=mkt,
                            from_date=from_date,
                            maintenance_mode=is_maintenance,
                        )
                    st.success("Done")
                except Exception as e:
                    st.error(f"Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Sector Pipeline — three sub-sections
# ══════════════════════════════════════════════════════════════════════════════

class _MFSubSection:
    """Money Flow Indicators column inside the sector pipeline."""

    def __init__(self, mf_svc) -> None:
        self.svc = mf_svc

    def render(self) -> None:
        st.markdown("**Money Flow Indicators**")
        mode      = st.radio("Mode", ["Maintenance", "Single symbol", "Full market"],
                             horizontal=False, key="mf_mode")
        market    = st.selectbox("Market", ["HOSE", "HNX", "HOSE + HNX"], index=2, key="mf_mkt")
        symbol    = st.text_input("Symbol", value="SSI", key="mf_sym") if mode == "Single symbol" else ""
        from_date = st.date_input("From date", value=None, key="mf_date") if mode != "Maintenance" else None

        if st.button("▶ Run MF", use_container_width=True, key="btn_mf"):
            with st.status("Computing MF…") as status:
                fd      = from_date.strftime("%Y-%m-%d") if from_date else None
                markets = ["HOSE", "HNX"] if market == "HOSE + HNX" else [market]
                match mode:
                    case "Maintenance":
                        for mkt in markets:
                            self.svc.run_maintenance(mkt)
                    case "Single symbol":
                        self.svc.run_one(symbol.strip().upper(), fd)
                    case "Full market":
                        for mkt in markets:
                            self.svc.run_all(mkt, fd)
                status.update(label="✅ Done", state="complete")


class _AggregationSubSection:
    """Sector Aggregation column."""

    def __init__(self, agg_svc) -> None:
        self.svc = agg_svc

    def render(self) -> None:
        st.markdown("**Sector Aggregation**")
        mode = st.radio("Mode", ["Maintenance", "Single date", "Date range", "Full rebuild"],
                        horizontal=False, key="agg_mode")

        agg_date = agg_from = agg_to = None
        match mode:
            case "Single date":
                agg_date = st.date_input("Date", key="agg_date")
            case "Date range":
                agg_from = st.date_input("From", value=datetime(2024, 1, 1), key="agg_from")
                agg_to   = st.date_input("To", key="agg_to")
            case "Full rebuild":
                agg_from = st.date_input("From", value=datetime(2021, 1, 1), key="agg_rebuild_from")

        if st.button("▶ Run Aggregation", use_container_width=True, key="btn_agg"):
            with st.status("Aggregating…") as status:
                match mode:
                    case "Maintenance":
                        n = self.svc.run_maintenance()
                    case "Single date":
                        n = self.svc.run_date(agg_date.strftime("%Y-%m-%d"))
                    case "Date range":
                        n = self.svc.run_range(agg_from.strftime("%Y-%m-%d"), agg_to.strftime("%Y-%m-%d"))
                    case "Full rebuild":
                        n = self.svc.run_all(agg_from.strftime("%Y-%m-%d"))
                status.update(label=f"✅ {n} sector-date rows", state="complete")


class _ScoringSubSection:
    """Sector Scoring column."""

    def __init__(self, scoring_svc) -> None:
        self.svc = scoring_svc

    def render(self) -> None:
        st.markdown("**Sector Scoring**")
        mode = st.radio("Mode", ["Maintenance", "Single date", "Date range", "Full rebuild"],
                        horizontal=False, key="sc_mode")

        sc_date = sc_from = sc_to = None
        match mode:
            case "Single date":
                sc_date = st.date_input("Date", key="sc_date")
            case "Date range":
                sc_from = st.date_input("From", value=datetime(2024, 1, 1), key="sc_from")
                sc_to   = st.date_input("To", key="sc_to")
            case "Full rebuild":
                sc_from = st.date_input("From", value=datetime(2021, 1, 1), key="sc_rebuild_from")

        if st.button("▶ Run Scoring", use_container_width=True, key="btn_sc"):
            with st.status("Scoring…") as status:
                match mode:
                    case "Maintenance":
                        n = self.svc.run_maintenance()
                    case "Single date":
                        n = self.svc.run_date(sc_date.strftime("%Y-%m-%d"))
                    case "Date range":
                        n = self.svc.run_range(sc_from.strftime("%Y-%m-%d"), sc_to.strftime("%Y-%m-%d"))
                    case "Full rebuild":
                        n = self.svc.run_all(sc_from.strftime("%Y-%m-%d"))
                status.update(label=f"✅ {n} sector rows", state="complete")


class SectorPipelineSection:
    """
    Full Sector Rotation pipeline section:
      MF Indicators | Aggregation | Scoring  +  Full Sync button
    """

    def __init__(self, sync_service, indicator_svc, signal_svc, mf_svc, agg_svc, scoring_svc) -> None:
        self._sync    = sync_service
        self._ind     = indicator_svc
        self._sig     = signal_svc
        self._mf      = _MFSubSection(mf_svc)
        self._agg     = _AggregationSubSection(agg_svc)
        self._scoring = _ScoringSubSection(scoring_svc)

    def render(self) -> None:
        _section_header("🔄", "Sector Rotation Pipeline")

        mf_col, agg_col, sc_col = st.columns(3, gap="large")
        with mf_col:
            self._mf.render()
        with agg_col:
            self._agg.render()
        with sc_col:
            self._scoring.render()

        _divider()
        self._render_full_sync()

    def _render_full_sync(self) -> None:
        fs_col, info_col = st.columns([2, 3])

        with fs_col:
            st.markdown(
                "**🚀 Full Sync** "
                "<span style='font-size:11px;color:#6b7280;font-weight:400'>"
                "(HOSE + HNX · Maintenance)</span>",
                unsafe_allow_html=True,
            )
            if st.button("🚀 Run Full Sync", type="primary",
                         use_container_width=True, key="btn_pipeline"):
                self._run_full_sync()

        with info_col:
            st.markdown(
                "**Execution order** (Maintenance mode — missing dates only)\n\n"
                "`1` Raw Data Sync → `2` Indicators → `3` Signals → "
                "`4` Daily Index → `5` MF Indicators → `6` Sector Aggregation → `7` Sector Scoring"
            )

    def _run_full_sync(self) -> None:
        st.session_state.log_messages = []
        with st.status("Running Full Sync…", expanded=False) as status:
            try:
                markets = ["HOSE", "HNX"]

                status.update(label="1/7 · Raw Data Sync…")
                for mkt in markets:
                    self._sync.maintenance_sync(mkt, "price")

                status.update(label="2/7 · Indicators…")
                for mkt in markets:
                    self._ind.run_maintenance(mkt)

                status.update(label="3/7 · Signals…")
                for mkt in markets:
                    self._sig.run_maintenance(mkt)

                status.update(label="4/7 · Daily Index History…")
                for mkt in markets:
                    self._sync.sync_all_daily_index(
                        market=mkt,
                        from_date="01/01/2022",
                        maintenance_mode=True,
                    )

                status.update(label="5/7 · Money Flow Indicators…")
                for mkt in markets:
                    self._mf.svc.run_maintenance(mkt)

                status.update(label="6/7 · Sector Aggregation…")
                self._agg.svc.run_maintenance()

                status.update(label="7/7 · Sector Scoring…")
                self._scoring.svc.run_maintenance()

                status.update(label="✅ Full Sync complete", state="complete")
                st.cache_data.clear()
            except Exception as e:
                logging.exception(e)
                status.update(label=f"❌ {e}", state="error")


# ══════════════════════════════════════════════════════════════════════════════
# Section: System Logs
# ══════════════════════════════════════════════════════════════════════════════
class SystemLogSection:
    """Collapsible expander showing session log messages."""

    def render(self) -> None:
        _divider()
        with st.expander("🖥 System logs", expanded=False):
            logs = st.session_state.get("log_messages", [])
            if logs:
                st.code("\n".join(logs), language="text")
            else:
                st.caption("No logs yet.")


# ══════════════════════════════════════════════════════════════════════════════
# Page1 — Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════
class Page1:
    """
    Orchestrates all sections of Tab 1 — Data Management.

    Usage (from app entry-point):
        page = Page1(
            db=db,
            sync_service=sync_svc,
            gap_service=gap_svc,
            indicator_svc=indicator_svc,
            signal_svc=signal_svc,
            mf_svc=mf_svc,
            agg_svc=agg_svc,
            scoring_svc=scoring_svc,
        )
        page.render()
    """

    def __init__(
        self,
        db,
        sync_service,
        gap_service,
        indicator_svc,
        signal_svc,
        mf_svc=None,
        agg_svc=None,
        scoring_svc=None,
    ) -> None:
        self._badge          = _DbBadge(db)
        self._sync_section   = SyncSection(sync_service, gap_service)
        self._ind_section    = IndicatorSection(indicator_svc)
        self._sig_section    = SignalSection(signal_svc)
        self._index_section  = IndexDataSection(sync_service)
        self._log_section    = SystemLogSection()

        self._has_sector = all(s is not None for s in (mf_svc, agg_svc, scoring_svc))
        if self._has_sector:
            self._sector_section = SectorPipelineSection(
                sync_service, indicator_svc, signal_svc,
                mf_svc, agg_svc, scoring_svc,
            )

    def render(self) -> None:
        st.markdown(_CSS, unsafe_allow_html=True)

        self._badge.render()

        # ── Row 1: four operation columns ─────────────────────────────────────
        _section_header("⚙️", "Data Operations")
        col_sync, col_ind, col_sig, col_idx = st.columns(4, gap="medium")

        with col_sync:
            self._sync_section.render()
        with col_ind:
            self._ind_section.render()
        with col_sig:
            self._sig_section.render()
        with col_idx:
            self._index_section.render()

        # ── Row 2: Sector pipeline ─────────────────────────────────────────────
        if self._has_sector:
            self._sector_section.render()
        else:
            _section_header("🔄", "Sector Rotation Pipeline")
            st.info("Sector services not initialised.")

        # ── System logs ────────────────────────────────────────────────────────
        self._log_section.render()


# ── Backward-compatible module-level entry point ──────────────────────────────
def render(
    db,
    sync_service,
    gap_service,
    indicator_svc,
    signal_svc,
    mf_svc=None,
    agg_svc=None,
    scoring_svc=None,
) -> None:
    """Backward-compatible shim — delegates to Page1."""
    Page1(
        db=db,
        sync_service=sync_service,
        gap_service=gap_service,
        indicator_svc=indicator_svc,
        signal_svc=signal_svc,
        mf_svc=mf_svc,
        agg_svc=agg_svc,
        scoring_svc=scoring_svc,
    ).render()