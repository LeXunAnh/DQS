import logging
from datetime import date as date_type
from typing import Optional

import pandas as pd
from sqlalchemy import text

from src.database.handler import DatabaseHandler
from src.pnf_service import PointFigureChart

logger = logging.getLogger(__name__)

class PNFService:
    """Service to build and analyse Point & Figure charts from DB data."""

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler
    # ------------------------------------------------------------------
    # 1. Data Fetching
    # ------------------------------------------------------------------
    def _fetch_ohlc(self, symbol: str, from_date: Optional[date_type] = None, to_date: Optional[date_type] = None) -> pd.DataFrame:
        """
        Fetch raw OHLC + adjusted close. If no dates given, fetch all available.
        Uses `close_price_adjusted` as the adjusted close – same as your chart tab.
        """
        params = {"symbol": symbol}
        date_clause = ""

        if from_date:
            date_clause += " AND trading_date >= :from_date"
            params["from_date"] = from_date
        if to_date:
            date_clause += " AND trading_date <= :to_date"
            params["to_date"] = to_date

        query = text(f"""
            SELECT trading_date,
                   open_price,
                   highest_price,
                   lowest_price,
                   close_price,
                   close_price_adjusted
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
            logger.error(f"Error fetching OHLC for {symbol}: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 2. Convert DataFrame → dict of arrays (the P&F library format)
    # ------------------------------------------------------------------
    @staticmethod
    def _df_to_ts_dict(df: pd.DataFrame, method: str) -> dict:
        """
        Build a dict with keys 'date', 'open', 'high', 'low', 'close'.
        Dates are passed as strings (YYYY-MM-DD) so the library can parse them.
        All prices are adjusted using `close_price_adjusted / close_price`.
        """
        adj_factor = (df["close_price_adjusted"] / df["close_price"]).fillna(1.0)

        ts = {
            "date":  df["trading_date"].dt.strftime("%Y-%m-%d").values,
            "open":  (df["open_price"] * adj_factor).round(4).values,
            "high":  (df["highest_price"] * adj_factor).round(4).values,
            "low":   (df["lowest_price"] * adj_factor).round(4).values,
            "close": df["close_price_adjusted"].round(4).values,
        }
        return ts

    # ------------------------------------------------------------------
    # 3. Build the PointFigureChart object
    # ------------------------------------------------------------------
    def build_chart(self,
                    symbol: str,
                    method: str = "h/l",
                    reversal: int = 3,
                    boxsize: float = 2.0,
                    scaling: str = "log",
                    from_date: Optional[date_type] = None,
                    to_date: Optional[date_type] = None,
                    ) -> PointFigureChart:
        """
        Fetch data and return a fully initialised PointFigureChart.
        All subsequent features (breakouts, trendlines, indicators) can be
        called on this object.
        """
        df = self._fetch_ohlc(symbol, from_date, to_date)
        if df.empty:
            raise ValueError(f"No data for {symbol} in the given date range.")

        ts_dict = self._df_to_ts_dict(df, method)
        chart = PointFigureChart(ts_dict,
                                 method=method,
                                 reversal=reversal,
                                 boxsize=boxsize,
                                 scaling=scaling,
                                 title=symbol)
        return chart

    # ------------------------------------------------------------------
    # 4. Convenience: get breakouts / trendlines as DataFrames
    # ------------------------------------------------------------------
    @staticmethod
    def get_breakouts_df(chart: PointFigureChart) -> pd.DataFrame:
        """Return breakouts as a user‑friendly DataFrame."""
        bo = chart.get_breakouts()
        return pd.DataFrame({
            "date":       bo["ts index"],
            "trend":      bo["trend"],
            "type":       bo["type"],
            "hits":       bo["hits"],
            "width":      bo["width"],
            "outer_width": bo["outer width"],
        })

    @staticmethod
    def get_trendlines_df(chart: PointFigureChart,
                          length: int = 6, mode: str = "strong") -> pd.DataFrame:
        """Return trendlines as a user‑friendly DataFrame."""
        # 1. Gọi hàm lấy trendline từ thư viện
        tl = chart.get_trendlines(length=length, mode=mode)

        # 2. Kiểm tra nếu không có trendline nào (tl là None hoặc dict rỗng)
        if tl is None or not isinstance(tl, dict) or len(tl.get("type", [])) == 0:
            return pd.DataFrame(columns=["bounded", "type", "length", "col_start", "box_start"])

        # 3. Sử dụng .get() để tránh crash nếu thiếu key và đảm bảo dữ liệu tồn tại
        try:
            return pd.DataFrame({
                "bounded": tl.get("bounded", []),
                "type": tl.get("type", []),
                "length": tl.get("length", []),
                "col_start": tl.get("column index", []),
                "box_start": tl.get("box index", []),
            })
        except Exception as e:
            # Trường hợp dữ liệu trả về bị lệch độ dài mảng
            return pd.DataFrame(columns=["bounded", "type", "length", "col_start", "box_start"])

    # ------------------------------------------------------------------
    # 5. Plotting helper (for Streamlit)
    # ------------------------------------------------------------------
    @staticmethod
    def get_plot(chart: PointFigureChart,
                 show_breakouts: bool = False,
                 show_trendlines: bool = False) -> object:
        """
        Build the P&F chart figure and return it so Streamlit can render it.
        Call `st.pyplot(PNFService.get_plot(chart))` in your UI.
        """
        # Apply display options
        chart.show_breakouts = show_breakouts
        chart.show_trendlines = "external" if show_trendlines else False
        chart.size = "auto"          # change to your preference
        chart.grid = True
        chart.show_markers = True

        # Build the figure (does not call plt.show())
        chart._assemble_plot_chart()
        chart.fig.set_size_inches(8, 6)
        return chart.fig
