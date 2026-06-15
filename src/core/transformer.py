from typing import List
import pandas as pd

class DataTransformer:
    """Chuyển đổi dữ liệu từ API response sang DataFrame cho DB"""
    @staticmethod
    def securities_to_df(data: List[dict]) -> pd.DataFrame:
        """Chuyển danh sách securities"""
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        return pd.DataFrame({
            'symbol': df['Symbol'],
            'market': df['Market'],
            'stock_name': df['StockName'],
            'stock_en_name': df['StockEnName'],
            'sector_name': None,
            'is_active': True,
        })

    @staticmethod
    def daily_ohlc_to_df(symbol: str, data: List[dict]) -> pd.DataFrame:
        """Chuyển OHLC data"""
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        return pd.DataFrame({
            'symbol': symbol,
            'trading_date': pd.to_datetime(df['TradingDate'], dayfirst=True).dt.date,
            'open_price': pd.to_numeric(df['Open']),
            'highest_price': pd.to_numeric(df['High']),
            'lowest_price': pd.to_numeric(df['Low']),
            'close_price': pd.to_numeric(df['Close']),
            'volume': pd.to_numeric(df['Volume']).astype(int),
            'total_value': pd.to_numeric(df['Value'])
        })

    @staticmethod
    def daily_stock_price_to_df(symbol: str, data: List[dict]) -> pd.DataFrame:
        """Chuyển dữ liệu giá chi tiết"""
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        return pd.DataFrame({
            'symbol': symbol,
            'trading_date': pd.to_datetime(df['TradingDate'], dayfirst=True).dt.date,
            'price_change': pd.to_numeric(df['PriceChange']),
            'per_price_change': pd.to_numeric(df['PerPriceChange']),
            'ceiling_price': pd.to_numeric(df['CeilingPrice']),
            'floor_price': pd.to_numeric(df['FloorPrice']),
            'ref_price': pd.to_numeric(df['RefPrice']),
            'open_price': pd.to_numeric(df['OpenPrice']),
            'highest_price': pd.to_numeric(df['HighestPrice']),
            'lowest_price': pd.to_numeric(df['LowestPrice']),
            'close_price': pd.to_numeric(df['ClosePrice']),
            'average_price': pd.to_numeric(df['AveragePrice']),
            'close_price_adjusted': pd.to_numeric(df['ClosePriceAdjusted']),
            'total_match_vol': pd.to_numeric(df['TotalMatchVol']).astype(int),
            'total_match_val': pd.to_numeric(df['TotalMatchVal']).astype(float),
            'total_deal_vol': pd.to_numeric(df['TotalDealVol']).astype(int),
            'total_deal_val': pd.to_numeric(df['TotalDealVal']).astype(float),
            'foreign_buy_vol_total': pd.to_numeric(df['ForeignBuyVolTotal']).astype(int),
            'foreign_sell_vol_total': pd.to_numeric(df['ForeignSellVolTotal']).astype(int),
            'foreign_buy_val_total': pd.to_numeric(df['ForeignBuyValTotal']).astype(float),
            'foreign_sell_val_total': pd.to_numeric(df['ForeignSellValTotal']).astype(float),
            'foreign_current_room': pd.to_numeric(df['ForeignCurrentRoom']).astype(float),
            'net_buy_sell_vol': pd.to_numeric(df['NetBuySellVol']).astype(int),
            'net_buy_sell_val': pd.to_numeric(df['NetBuySellVal']).astype(int),
            'total_traded_vol': pd.to_numeric(df['TotalTradedVol']).astype(int),
            'total_traded_value': pd.to_numeric(df['TotalTradedValue']).astype(float),
            'total_buy_trade': pd.to_numeric(df['TotalBuyTrade']).astype(float),
            'total_buy_trade_vol': pd.to_numeric(df['TotalBuyTradeVol']).astype(float),
            'total_sell_trade': pd.to_numeric(df['TotalSellTrade']).astype(float),
            'total_sell_trade_vol': pd.to_numeric(df['TotalSellTradeVol']).astype(float),
            'time_str': df['TradingDate']
        })

    @staticmethod
    def index_list_to_df(data: List[dict]) -> pd.DataFrame:
        """Transform index list API response -> DataFrame"""
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)

        # Xử lý vector hóa xóa khoảng trắng bẩn (\xa0) và fallback về IndexCode nếu rỗng/None
        index_name = df['IndexName'].str.replace('\xa0', ' ', regex=False).str.strip()
        index_name = index_name.replace('', None).fillna(df['IndexCode'])

        result_df = pd.DataFrame({
            'index_code': df['IndexCode'],
            'index_name': index_name,
            'exchange': df['Exchange']
        })
        return result_df.drop_duplicates(subset=['index_code'])

    @staticmethod
    def daily_index_to_df(index_code: str, data: List[dict]) -> pd.DataFrame:
        """Transform daily index data -> DataFrame"""
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        return pd.DataFrame({
            'index_code': index_code,
            'trading_date': pd.to_datetime(df['TradingDate'], dayfirst=True).dt.date,
            'index_value': pd.to_numeric(df['IndexValue']).astype(float),
            'change': pd.to_numeric(df['Change']).astype(float),
            'ratio_change': pd.to_numeric(df['RatioChange']).astype(float),
            'total_trade': pd.to_numeric(df['TotalTrade']).astype(int),
            'total_match_vol': pd.to_numeric(df['TotalMatchVol']).astype(int),
            'total_match_val': pd.to_numeric(df['TotalMatchVal']).astype(float),
            'total_deal_vol': pd.to_numeric(df['TotalDealVol']).astype(int),
            'total_deal_val': pd.to_numeric(df['TotalDealVal']).astype(float),
            'total_vol': pd.to_numeric(df['TotalVol']).astype(int),
            'total_val': pd.to_numeric(df['TotalVal']).astype(float),
            'advances': pd.to_numeric(df['Advances']).astype(int),
            'no_changes': pd.to_numeric(df['NoChanges']).astype(int),
            'declines': pd.to_numeric(df['Declines']).astype(int),
            'ceilings': pd.to_numeric(df['Ceilings']).astype(int),
            'floors': pd.to_numeric(df['Floors']).astype(int),
            'trading_session': df['TradingSession']
        })