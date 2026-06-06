"""
src/utils/price_utils.py
════════════════════════════════════════════════════════════════════════════
Shared price-adjustment utility.

Previously _adjust_prices() was copy-pasted identically into both
IndicatorService and MFService. Any bug fix or rounding change would
have to be applied in two places — this module is the single source
of truth.

Contract
────────
Input  : raw DataFrame from daily_stock_prices, must contain:
             close_price            (raw unadjusted close)
             close_price_adjusted   (corporate-action adjusted close)
             open_price
             highest_price
             lowest_price

Output : new DataFrame (never mutates caller's data) with:
             close_price   → replaced by close_price_adjusted (rounded 2dp)
             open_price    → multiplied by adj_factor          (rounded 2dp)
             highest_price → multiplied by adj_factor          (rounded 2dp)
             lowest_price  → multiplied by adj_factor          (rounded 2dp)

         Rows where close_price <= 0 or close_price_adjusted is null
         are dropped before adjustment — these are data errors that
         would produce meaningless or infinite adj_factors.

         Volume and foreign flow columns are intentionally NOT touched:
         they are denominated in shares, which are unaffected by cash
         dividends or stock splits from the price perspective.
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pandas as pd


def adjust_prices(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply the close_price_adjusted / close_price factor to all OHLC columns.

    Parameters
    ----------
    df : pd.DataFrame
        Raw price data fetched from daily_stock_prices.

    Returns
    -------
    pd.DataFrame
        New DataFrame with adjusted OHLC prices and a reset integer index.
        The original DataFrame is never modified.
    """
    valid = (df["close_price"] > 0) & df["close_price_adjusted"].notna()
    df = df[valid].copy()

    adj_factor = (df["close_price_adjusted"] / df["close_price"]).fillna(1.0)

    df["close_price"]   = df["close_price_adjusted"].round(2)
    df["open_price"]    = (df["open_price"]    * adj_factor).round(2)
    df["highest_price"] = (df["highest_price"] * adj_factor).round(2)
    df["lowest_price"]  = (df["lowest_price"]  * adj_factor).round(2)

    return df.reset_index(drop=True)