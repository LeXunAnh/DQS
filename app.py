# app.py  —  QuantStock Streamlit UI
# ════════════════════════════════════════════════════════════════════════════
# streamlit run app.py
# ════════════════════════════════════════════════════════════════════════════

import pandas as pd
import streamlit as st
from sqlalchemy import text

import config
from src.datapipe.handler import DatabaseHandler
from src.datapipe.api_client import SSIAPIClient
from src.datapipe.transformer import DataTransformer
from src.datapipe.sync_service import SyncService
from src.database.gap_service import GapRepairService
from src.services.indicator_service import IndicatorService
from src.services.signal_service import SignalService
from src.services.mf_service import MFService
from src.services.sector_aggregation_service import SectorAggregationService
from src.services.sector_scoring_service import SectorScoringService
from src.services.index_service import IndexService
from src.services.kagi_service import KagiService
from src.services.renko_service import RenkoService
from src.interfaces import page1, page2, page3, page4, page5, page6, page7
from src.interfaces.helpers import setup_logging

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="DataQuant & Signal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    [data-testid="metric-container"] {
        background:#f8f9fa; border-radius:8px;
        padding:10px 14px; border:1px solid #e9ecef;
    }
    .block-container { padding-top:1.2rem; }
</style>
""", unsafe_allow_html=True)

# ── Logging ───────────────────────────────────────────────────────────────────
if "log_messages" not in st.session_state:
    st.session_state.log_messages = []
setup_logging()

# ── Services  (cache_resource — init 1 lần duy nhất) ─────────────────────────
@st.cache_resource
def init_services():
    db          = DatabaseHandler()
    api_client  = SSIAPIClient(config)
    transformer = DataTransformer()
    sync_svc    = SyncService(api_client, db, transformer)
    gap_svc     = GapRepairService(db, sync_svc)
    ind_svc     = IndicatorService(db)
    sig_svc     = SignalService(db)
    mf_svc      = MFService(db)
    agg_svc     = SectorAggregationService(db)
    scoring_svc = SectorScoringService(db)
    index_svc   = IndexService(db)
    kagi_svc    = KagiService(db)
    renko_svc   = RenkoService(db)
    return db, sync_svc, gap_svc, ind_svc, sig_svc, mf_svc, agg_svc, scoring_svc, index_svc, kagi_svc, renko_svc

db, sync_service, gap_service, indicator_svc, signal_svc, mf_svc, agg_svc, scoring_svc, index_svc, kagi_svc, renko_svc  = init_services()

# ── Symbol list ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_symbols() -> pd.DataFrame:
    try:
        with db.engine.connect() as conn:
            return pd.read_sql(
                text("SELECT symbol, stock_name, market FROM securities ORDER BY symbol"),
                conn,
            )
    except Exception:
        return pd.DataFrame(columns=["symbol", "stock_name", "market"])

symbols_df = load_symbols()
has_data   = not symbols_df.empty

# ── Layout ────────────────────────────────────────────────────────────────────
st.title("📈 DataQuant & Signal")

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "DataPipeline",
    "Chart",
    "Signals",
    "Sector",
    "Index",
    "Kagi",
    "Renko",
])

with tab1:
    page1.render(db, sync_service, gap_service, indicator_svc, signal_svc, mf_svc, agg_svc, scoring_svc)

with tab2:
    page2.render(db, symbols_df, has_data)

with tab3:
    page3.render(signal_svc)

with tab4:
    page4.render(db, scoring_svc)

with tab5:
    page5.render(db, index_svc)

with tab6:
    page6.render(db, kagi_svc, symbols_df, has_data)

with tab7:
    page7.render(db, renko_svc, symbols_df, has_data)