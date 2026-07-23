"""
src/services/kagi_service.py
════════════════════════════════════════════════════════════════════════════
KagiService — Service wrapper: fetches adjusted close prices from
daily_stock_prices and builds/plots a Kagi chart via src.kagi_chart.KagiChart.

Mirrors the existing PNFService (src/services/pnf_service.py) split between
a pure charting algorithm (kagi_chart.py / pnf_service.py) and a thin DB +
plotting service class.

Public API
──────────
    svc   = KagiService(db)
    chart = svc.build_chart("SSI", reversal_type="pct", reversal_value=4.0)
    fig   = svc.get_plot(chart, title="SSI — Kagi")   # st.pyplot(fig)
    df    = svc.segments_to_df(chart)                 # tidy table / export
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import text

from src.datapipe.handler import DatabaseHandler
from src.kagi_chart import KagiChart, KagiSegment

logger = logging.getLogger(__name__)


class KagiService:
    """Service to build and plot Kagi charts from DB data."""

    # Default styling — Yang (rising) vs Ying (falling) legs
    YANG_COLOR = "#16a34a"   # green
    YING_COLOR = "#dc2626"   # red
    YANG_WIDTH = 2.2
    YING_WIDTH = 1.0

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    # ------------------------------------------------------------------
    # 1. Data Fetching
    # ------------------------------------------------------------------
    def _fetch_close(
        self,
        symbol: str,
        from_date: Optional[date_type] = None,
        to_date: Optional[date_type] = None,
    ) -> pd.DataFrame:
        """
        Fetch adjusted daily close for one symbol. Kagi only needs close
        prices — uses close_price_adjusted, consistent with the chart tabs
        elsewhere in this project (page2 / PNFService).
        """
        params: dict = {"symbol": symbol}
        date_clause = ""

        if from_date:
            date_clause += " AND trading_date >= :from_date"
            params["from_date"] = from_date
        if to_date:
            date_clause += " AND trading_date <= :to_date"
            params["to_date"] = to_date

        query = text(f"""
            SELECT trading_date, close_price_adjusted
            FROM daily_stock_prices
            WHERE symbol = :symbol
              AND close_price > 0
              AND close_price_adjusted IS NOT NULL
              {date_clause}
            ORDER BY trading_date
        """)

        try:
            with self.db.engine.connect() as conn:
                df = pd.read_sql(query, conn, params=params)
            df["trading_date"] = pd.to_datetime(df["trading_date"])
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch close price cho Kagi {symbol}: {e}")
            return pd.DataFrame()

    @staticmethod
    def _df_to_records(df: pd.DataFrame) -> list[dict]:
        """Convert the fetched DataFrame into [{'date':, 'close':}, ...]."""
        return [
            {
                "date": row["trading_date"].strftime("%Y-%m-%d"),
                "close": float(row["close_price_adjusted"]),
            }
            for _, row in df.iterrows()
        ]

    # ------------------------------------------------------------------
    # 2. Build the KagiChart
    # ------------------------------------------------------------------
    def build_chart(
        self,
        symbol: str,
        reversal_type: str = "pct",
        reversal_value: float = 4.0,
        from_date: Optional[date_type] = None,
        to_date: Optional[date_type] = None,
    ) -> list[KagiSegment]:
        """
        Fetch close prices for `symbol` and return the list of Yang/Ying
        KagiSegments.

        Args:
            reversal_type  : 'pct' (default) — % move off last pivot
                              'diff'          — absolute price difference
            reversal_value : e.g. 4.0 for a 4% reversal threshold
        """
        df = self._fetch_close(symbol, from_date, to_date)
        if df.empty:
            raise ValueError(f"No data for {symbol} in the given date range.")

        records = self._df_to_records(df)
        return KagiChart(
            records, reversal_type=reversal_type, reversal_value=reversal_value
        ).build()

    # ------------------------------------------------------------------
    # 3. Convenience: segments → DataFrame (for tables / export)
    # ------------------------------------------------------------------
    @staticmethod
    def segments_to_df(segments: list[KagiSegment]) -> pd.DataFrame:
        """Flatten segments into a tidy DataFrame — one row per pivot vertex."""
        rows = []
        for seg_idx, seg in enumerate(segments):
            for p in seg.points:
                rows.append({
                    "segment": seg_idx,
                    "trend": "Yang" if seg.uptrend else "Ying",
                    "x": p.x,
                    "close": p.close,
                    "date": p.date,
                })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # 4. Plotting helper (matplotlib — for Streamlit's st.pyplot)
    # ------------------------------------------------------------------
    def get_plot(
        self,
        segments: list[KagiSegment],
        title: str = "",
        figsize: tuple[float, float] = (10, 6),
    ) -> object:
        """
        Render the Kagi chart as a matplotlib Figure.
        Call `st.pyplot(KagiService.get_plot(chart, title=symbol))` in the UI.
        """
        fig, ax = plt.subplots(figsize=figsize)

        for seg in segments:
            xs = [p.x for p in seg.points]
            ys = [p.close for p in seg.points]
            color = self.YANG_COLOR if seg.uptrend else self.YING_COLOR
            width = self.YANG_WIDTH if seg.uptrend else self.YING_WIDTH
            ax.plot(xs, ys, color=color, linewidth=width, solid_joinstyle="miter")

        ax.set_title(title or "Kagi Chart")
        ax.set_xlabel("Reversal step (not time-linear)")
        ax.set_ylabel("Price")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        return fig
