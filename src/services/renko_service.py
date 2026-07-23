"""
src/services/renko_service.py
════════════════════════════════════════════════════════════════════════════
RenkoService — Build and analyse Renko charts from DB data.

Mirrors the existing PNFService / Kagi-service pattern used in this codebase:

    1. Fetch adjusted close-price series from daily_stock_prices
    2. Feed the series into the Renko brick engine (src/renko.py — the
       Renko class you already have, unmodified)
    3. Expose convenience helpers on top of the raw brick list:
         - bricks → tidy DataFrame
         - reversal signals (Renko's equivalent of BUY/SELL triggers)
         - a matplotlib figure for `st.pyplot(...)` in Streamlit

No new DB tables are introduced — like PNFService, Renko charts are
computed on-demand (same as PNF/Kagi), not persisted.

Import path
───────────
This assumes the Renko engine class lives at `src/renko.py`, the same
sibling relationship PointFigureChart has with pnf_service.py
(`from src.pnf_service import PointFigureChart`). Adjust the import
below if your project places renko.py elsewhere.
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Optional

import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle
from sqlalchemy import text

from src.datapipe.handler import DatabaseHandler
from src.renko import Renko

logger = logging.getLogger(__name__)


@dataclass
class RenkoChart:
    """
    Lightweight result object returned by RenkoService.build_chart().

    Mirrors the role KagiService's `segments` list plays for Page6:
    a single self-contained object that get_plot() / bricks_to_df()
    both consume directly, with no extra state carried by the caller.
    """
    symbol:     str
    brick_size: float
    bricks:     list = field(default_factory=list)   # list[dict] — Renko.bricks


class RenkoService:
    """Service to build and analyse Renko charts from DB data."""

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    # ------------------------------------------------------------------
    # 1. Data Fetching
    # ------------------------------------------------------------------
    def _fetch_close_series(
        self,
        symbol: str,
        from_date: Optional[date_type] = None,
        to_date: Optional[date_type] = None,
    ) -> pd.DataFrame:
        """
        Fetch the adjusted close-price series for one symbol.

        The Renko brick engine only ever compares a single scalar price
        per step (see Renko.create_renko — it iterates `self.data` and
        each `d` is one price), so — unlike PNFService, which needs full
        OHLC — we only need `close_price_adjusted` here.
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
            df = df.rename(columns={"close_price_adjusted": "close"})
            return df
        except Exception as e:
            logger.error(f"Error fetching close series for {symbol}: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 2. Brick-size helper (ATR-style auto sizing)
    # ------------------------------------------------------------------
    @staticmethod
    def _auto_brick_size(df: pd.DataFrame, period: int = 14) -> float:
        """
        Fallback brick size when the caller doesn't supply a fixed one.

        Uses the rolling mean of |close-to-close change| as a simple ATR
        proxy — we only have a close-price series here (no H/L, by
        design — see _fetch_close_series), so a true ATR isn't available.
        """
        diffs = df["close"].diff().abs().dropna()
        if diffs.empty:
            return 1.0
        atr = diffs.rolling(period, min_periods=1).mean().iloc[-1]
        return float(round(atr, 2)) if atr and atr > 0 else 1.0

    # ------------------------------------------------------------------
    # 3. Build the Renko chart object
    # ------------------------------------------------------------------
    def build_chart(
        self,
        symbol: str,
        brick_size: Optional[float] = None,
        atr_period: int = 14,
        from_date: Optional[date_type] = None,
        to_date: Optional[date_type] = None,
    ) -> RenkoChart:
        """
        Fetch data and return a fully-built Renko chart.

        Mirrors KagiService.build_chart(): raises ValueError directly when
        there's no data (caller — Page7 — catches it the same way Page6
        catches KagiService's ValueError), and returns a single object
        that get_plot() / bricks_to_df() consume without any extra state.

        Args:
            symbol     : e.g. 'SSI'
            brick_size : fixed brick size. None → auto-sized (see
                         _auto_brick_size).
            atr_period : lookback window used only when brick_size=None.
            from_date / to_date : optional bounds on the price history.

        Returns:
            RenkoChart(symbol, brick_size, bricks)
        """
        df = self._fetch_close_series(symbol, from_date, to_date)
        if df.empty:
            raise ValueError(f"Không có dữ liệu cho {symbol} trong khoảng thời gian này.")

        size = brick_size or self._auto_brick_size(df, atr_period)
        if size <= 0:
            raise ValueError(f"Brick size tính được không hợp lệ cho {symbol}: {size}")

        renko = Renko(size, df["close"].tolist())
        renko.create_renko()

        if not renko.bricks or len(renko.bricks) <= 1:
            raise ValueError(
                f"{symbol}: brick size {size:,.2f} quá lớn so với biến động giá "
                f"trong khoảng đã chọn — không đủ để vẽ brick nào."
            )

        return RenkoChart(symbol=symbol, brick_size=size, bricks=renko.bricks)

    # ------------------------------------------------------------------
    # 4. Bricks → DataFrame  +  reversal signals
    # ------------------------------------------------------------------
    @staticmethod
    def bricks_to_df(chart: RenkoChart) -> pd.DataFrame:
        """
        Convert chart.bricks (list[dict]) into a tidy DataFrame.
        Mirrors KagiService.segments_to_df() — same role, one row per
        chart element (brick vs. pivot segment).

        Note: bricks carry no timestamp — Renko is price-driven, not
        time-driven — so this is index-based. Use
        `build_chart_with_dates()` if you need an approximate calendar
        mapping (e.g. for chart markers).
        """
        if not chart.bricks:
            return pd.DataFrame(columns=["brick_no", "type", "open", "close"])

        return pd.DataFrame([
            {
                "brick_no": i,
                "type":     b["type"],
                "open":     round(float(b["open"]),  4),
                "close":    round(float(b["close"]), 4),
            }
            for i, b in enumerate(chart.bricks)
        ])

    @staticmethod
    def get_reversal_signals(bricks_df: pd.DataFrame) -> pd.DataFrame:
        """
        Detect brick-type reversals (up→down / down→up) — the Renko
        equivalent of a BUY/SELL trigger (mirrors what
        SignalDetector.detect_ma_cross does for MA crossovers elsewhere
        in this codebase).

        Strength scales with how many consecutive same-direction bricks
        preceded the reversal — a longer prior trend implies a stronger
        reversal signal. Clipped to [0.1, 1.0], same convention as
        SignalDetector._strength_clip.
        """
        if bricks_df.empty:
            return pd.DataFrame(columns=["brick_no", "signal", "direction", "strength", "price"])

        signals: list[dict] = []
        streak    = 0
        prev_type: Optional[str] = None

        for _, row in bricks_df.iterrows():
            t = row["type"]

            if t == "first":
                prev_type = t
                continue

            if prev_type in ("up", "down") and t != prev_type:
                strength = float(np.clip(streak / 10.0, 0.1, 1.0))
                signals.append({
                    "brick_no":  int(row["brick_no"]),
                    "signal":    "REVERSAL_UP" if t == "up" else "REVERSAL_DOWN",
                    "direction": "BUY" if t == "up" else "SELL",
                    "strength":  round(strength, 4),
                    "price":     row["close"],
                })
                streak = 0
            else:
                streak += 1

            prev_type = t

        return pd.DataFrame(signals)

    # ------------------------------------------------------------------
    # 5. Optional: approximate calendar-date mapping for bricks
    # ------------------------------------------------------------------
    @staticmethod
    def build_chart_with_dates(
        symbol: str, df: pd.DataFrame, brick_size: float
    ) -> tuple[RenkoChart, pd.DataFrame]:
        """
        Build bricks incrementally via Renko.check_new_price() so each
        brick can be tagged with the trading_date that triggered it.

        Caveat: create_renko() (used by build_chart) carries a "wick"
        adjustment across reversals that check_new_price() does not
        implement — in rare wick-heavy sequences the two paths can
        produce slightly different brick counts. Use this method only
        when you specifically need date-tagged bricks (e.g. chart
        markers); use build_chart() for the canonical brick series.

        Returns:
            (RenkoChart, dates_df) — dates_df has one 'brick_date' row
            per entry in chart.bricks, same order.
        """
        if df.empty:
            raise ValueError("Cannot build dated bricks from an empty DataFrame.")

        closes = df["close"].tolist()
        dates  = df["trading_date"].tolist()

        renko = Renko(brick_size, [])
        renko.add_single_custom_brick("first", closes[0], closes[0])
        brick_dates = [dates[0]]

        for price, dt in zip(closes[1:], dates[1:]):
            before = len(renko.bricks)
            renko.check_new_price(price)
            added = len(renko.bricks) - before
            if added:
                brick_dates.extend([dt] * added)

        chart = RenkoChart(symbol=symbol, brick_size=brick_size, bricks=renko.bricks)
        return chart, pd.DataFrame({"brick_date": brick_dates})

    # ------------------------------------------------------------------
    # 6. Plotting helper (for Streamlit)
    # ------------------------------------------------------------------
    @staticmethod
    def get_plot(chart: RenkoChart, title: Optional[str] = None, show_grid: bool = True) -> object:
        """
        Build the Renko brick chart figure and return it so Streamlit can
        render it via `st.pyplot(RenkoService.get_plot(chart, title=...))`
        — same call shape as KagiService.get_plot(segments, title=...).

        Re-implements Renko.draw_chart()'s layout logic but WITHOUT the
        blocking plt.show() call, so the figure object survives to be
        handed back to the caller — same approach PNFService.get_plot()
        uses for PointFigureChart._assemble_plot_chart().
        """
        import matplotlib.pyplot as plt  # local import — keep matplotlib off the hot path

        brick_width = chart.brick_size / 2
        y_max = 0.0
        fig, ax = plt.subplots()

        count = 1
        for b in chart.bricks:
            if b["type"] == "up":
                y, color = b["open"], "#26a69a"
            elif b["type"] == "down":
                y, color = b["close"], "#ef5350"
            else:
                y, color = b["close"], "#9e9e9e"

            y_max = max(y_max, y)

            r = Rectangle((count * brick_width, y), brick_width, chart.brick_size)
            r.set_color(color)
            ax.add_patch(r)
            count += 1

        ax.set_xlim(0, count * brick_width)
        ax.set_ylim(0, y_max + (y_max * 0.1))
        ax.set_axisbelow(True)
        ax.get_xaxis().set_visible(False)

        if show_grid:
            ticks = np.arange(0, y_max + (y_max * 0.1), chart.brick_size)
            ax.set_yticks(ticks)
            ax.grid(linestyle="--", color="#ccd8c0")

        if title:
            ax.set_title(title, fontsize=11)

        fig.set_size_inches(8, 6)
        return fig
