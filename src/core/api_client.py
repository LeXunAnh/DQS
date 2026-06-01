from ssi_fc_data import fc_md_client, model
import pandas as pd
from vnstock import Reference, show_api, show_doc

class SSIAPIClient:
    """Giao tiếp với SSI market data API"""
    def __init__(self, config):
        self.config = config
        self.client = fc_md_client.MarketDataClient(self.config)

    def get_access_token(self):
        return self.client.access_token()

    def get_securities(self, market: str, page: int = 1, page_size: int = 1000) -> dict:
        """Lấy danh sách chứng khoán"""
        req = model.securities(market, page, page_size)
        return self.client.securities(self.config, req)

    def get_daily_ohlc(self, symbol: str, from_date: str, to_date: str, page: int = 1, page_size: int = 9999) -> dict:
        """Lấy dữ liệu OHLC"""
        req = model.daily_ohlc(symbol, from_date, to_date, page, page_size, True)
        return self.client.daily_ohlc(self.config, req)

    def get_daily_stock_price(self, symbol: str, from_date: str, to_date: str, page: int = 1, page_size: int = 1000) -> dict:
        """Lấy dữ liệu giá chi tiết"""
        req = model.daily_stock_price(symbol, from_date, to_date, page, page_size)
        return self.client.daily_stock_price(self.config, req)

    def get_index_list(self, exchange: str, page: int = 1, page_size: int = 1000) -> dict:
        """Lấy dữ liệu giá chi tiết"""
        req = model.index_list(exchange, page, page_size)
        return self.client.index_list(self.config, req)

    def get_daily_index(self,exchange: str,from_date: str,to_date: str,page: int = 1,page_size: int = 1000,request_id: str = "123",order_by: str = "",order_dir: str = "") -> dict:
        """Lấy dữ liệu giá chi tiết"""
        req = model.daily_index(request_id, exchange, from_date, to_date, page, page_size, order_by, order_dir)
        return self.client.daily_index(self.config, req)

class VNSTOCKAPIClient:
    def testing(self):
        return 0

if __name__ == "__main__":
    test = Reference()




