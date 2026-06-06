from src.core.api_client import SSIAPIClient
from src.core.transformer import DataTransformer
from src.database.handler import DatabaseHandler
import time
from tqdm import tqdm
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class SyncService:
    """Điều phối quá trình đồng bộ dữ liệu"""
    def __init__(self, api_client: SSIAPIClient, db_handler: DatabaseHandler,transformer: DataTransformer = None):
        self.api = api_client
        self.db = db_handler
        self.transformer = transformer or DataTransformer()

    def sync_securities(self, market: str = 'HOSE') -> bool:
        """Đồng bộ bảng securities"""
        try:
            res = self.api.get_securities(market, 1, 1000)
            status = res.get('status')
            if status == 'Success':
                data = res.get('data', [])
                if not data:
                    logger.warning(f"Không có data securities cho {market}")
                    return False
                df = self.transformer.securities_to_df(data)
                self.db.save_data(df, 'securities', ['symbol'])
                logger.info(f"Đã đồng bộ securities cho {market}")
                return True
            elif status in (401, 429) or res.get('statusCode') in (401, 429):
                logger.error("Lỗi xác thực hoặc rate limit khi lấy securities")
                return False
            else:
                logger.error(f"Lỗi lấy securities {market}: {res}")
                return False
        except Exception as e:
            logger.exception(f"Lỗi trong sync_securities: {e}")
            return False

    def sync_all_markets(self):
        """Đồng bộ tất cả sàn"""
        for market in ['HOSE', 'HNX', 'UPCOM']:
            logger.info(f"🔄 Đang đồng bộ danh mục sàn: {market}")
            self.sync_securities(market)

    def fetch_daily_ohlc(self, symbol: str, from_date: str, to_date: str,max_retries: int = 3) -> bool:
        """Lấy OHLC cho một symbol và lưu vào DB"""
        for attempt in range(max_retries):
            try:
                res = self.api.get_daily_ohlc(symbol, from_date, to_date)
                status = res.get('status')
                if status == 'Success':
                    data = res.get('data', [])
                    df = self.transformer.daily_ohlc_to_df(symbol, data)
                    if not df.empty:
                        self.db.save_data(df, 'daily_ohlc', ['symbol', 'trading_date'])
                    return True
                elif status == 401 or res.get('statusCode') == 401:
                    logger.error("Token hết hạn, đang refresh...")
                    self.api.get_access_token()
                elif status == 429 or res.get('statusCode') == 429:
                    wait = (attempt + 1) * 2
                    logger.warning(f"Rate limit OHLC cho {symbol}, đợi {wait}s")
                    time.sleep(wait)
                else:
                    logger.error(f"Lỗi OHLC cho {symbol}: {res}")
                    return False
            except Exception as e:
                logger.exception(f"Exception trong fetch_daily_ohlc: {e}")
                time.sleep(1)
        return False

    def sync_all_ohlc(self, market: str = 'HOSE',from_date: str = '01/01/2015',to_date: str = '13/02/2026'):
        """Đồng bộ OHLC cho toàn bộ sàn"""
        symbols = self.db.get_all_symbols(market=market)
        pbar = tqdm(symbols, desc=f"🚀 Syncing {market}", unit="symbol")
        for symbol in pbar:
            try:
                pbar.set_postfix({"Current": symbol})
                self.fetch_daily_ohlc(symbol, from_date, to_date)
            except Exception as e:
                logger.error(f"Lỗi tại mã {symbol}: {e}")
            finally:
                time.sleep(1.2)

    def fetch_daily_stock_prices(self, symbol: str, from_date: str, to_date: str,chunk_days: int = 30, max_retries: int = 3) -> bool:
        """Lấy dữ liệu giá chi tiết theo từng chunk để tránh rate limit"""
        start_dt = datetime.strptime(from_date, '%d/%m/%Y')
        end_dt = datetime.strptime(to_date, '%d/%m/%Y')
        current_start = start_dt
        delta = timedelta(days=chunk_days - 1)  # để inclusive

        with tqdm(desc=f"  ↳ {symbol}", unit="chunk", leave=False) as pbar_chunks:
            while current_start <= end_dt:
                current_end = min(current_start + delta, end_dt)
                str_start = current_start.strftime('%d/%m/%Y')
                str_end = current_end.strftime('%d/%m/%Y')
                pbar_chunks.set_postfix({"range": f"{str_start}-{str_end}"})

                success = False
                for attempt in range(max_retries):
                    if self._execute_fetch_stock_prices(symbol, str_start, str_end):
                        success = True
                        break
                    else:
                        logger.warning(f"⚠️ Thử lại lần {attempt+1} cho {symbol} [{str_start}]")
                        time.sleep(1)
                pbar_chunks.update(1)
                time.sleep(1)

                if success:
                    current_start = current_end + timedelta(days=1)
                else:
                    logger.error(f"Fail at {symbol} {str_start}")
                    current_start = current_end + timedelta(days=1)
        return True

    def _execute_fetch_stock_prices(self, symbol: str, from_date: str, to_date: str) -> bool:
        """Thực hiện một request lấy dữ liệu giá và lưu vào DB"""
        try:
            res = self.api.get_daily_stock_price(symbol, from_date, to_date)
            status = res.get('status')
            if status == 'Success':
                data = res.get('data', [])
                if not data:
                    # Không có dữ liệu (ngày nghỉ) vẫn coi là thành công
                    return True
                df = self.transformer.daily_stock_price_to_df(symbol, data)
                self.db.save_data(df, 'daily_stock_prices', ['symbol', 'trading_date'])
                return True
            elif status in (401, 429) or res.get('statusCode') in (401, 429):
                logger.warning(f"Rate limit hoặc auth lỗi: {status}")
                return False
            else:
                logger.error(f"API Error: {res.get('message')}")
                return False
        except Exception as e:
            logger.error(f"Exception: {e}")
            return False

    def sync_all_stock_prices(self, market: str = 'HOSE', from_date: str = '01/01/2021'):
        """Đồng bộ dữ liệu giá chi tiết cho tất cả mã trên sàn"""
        symbols = self.db.get_all_symbols_except_CQ(market=market, only_companies=True)
        now = datetime.now()
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        to_date = now.date() if now >= market_close else now.date() - timedelta(days=1)
        to_date_str = to_date.strftime('%d/%m/%Y')
        logger.info(f"🚀 Sync toàn bộ {market}: {len(symbols)} mã | {from_date} → {to_date_str}")

        pbar = tqdm(symbols, desc=f"Overall Progress {market}", unit="symbol")
        for symbol in pbar:
            try:
                pbar.set_postfix({"current": symbol})
                self.fetch_daily_stock_prices(symbol, from_date, to_date_str)
            except Exception as e:
                logger.error(f"Lỗi tại mã {symbol}: {e}")
            finally:
                time.sleep(0.3)

    def maintenance_sync(self, market: str = 'HOSE', mode: str = 'ohlc'):
        """Chạy đồng bộ bảo trì (chỉ các ngày thiếu)"""
        symbols = self.db.get_all_symbols_except_CQ(market=market, only_companies=True)
        now = datetime.now()
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        to_date = now.date() if now >= market_close else now.date() - timedelta(days=1)
        to_date_str = to_date.strftime('%d/%m/%Y')

        logger.info(f"🔄 Bắt đầu bảo trì dữ liệu sàn {market} cho {len(symbols)} mã")
        pbar = tqdm(symbols, desc=f"Maintenance {market}")

        for symbol in pbar:
            try:
                pbar.set_postfix({"current": symbol})
                if mode == 'ohlc':
                    last_date = self.db.get_latest_trading_date('daily_ohlc', symbol)
                    start_date = (last_date + timedelta(days=1)) if last_date \
                        else datetime.strptime("01/01/2015", '%d/%m/%Y').date()
                    if start_date <= to_date:
                        self.fetch_daily_ohlc(symbol, start_date.strftime('%d/%m/%Y'), to_date_str)
                elif mode == 'price':
                    last_date = self.db.get_latest_trading_date('daily_stock_prices', symbol)
                    start_date = (last_date + timedelta(days=1)) if last_date \
                        else datetime.strptime("01/01/2021", '%d/%m/%Y').date()
                    if start_date <= to_date:
                        self.fetch_daily_stock_prices(symbol, start_date.strftime('%d/%m/%Y'), to_date_str)
            except Exception as e:
                logger.error(f"Lỗi bảo trì tại mã {symbol}: {e}")
            finally:
                time.sleep(0.3)

    def sync_one_ohlc(self, symbol: str, from_date: str, to_date: str):
        """Đồng bộ OHLC cho một mã cụ thể"""
        logger.info(f"Bắt đầu đồng bộ OHLC cho {symbol} từ {from_date} đến {to_date}")
        return self.fetch_daily_ohlc(symbol, from_date, to_date)

    def sync_one_stock_price(self, symbol: str, from_date: str, to_date: str):
        """Đồng bộ dữ liệu giá chi tiết cho một mã cụ thể"""
        logger.info(f"Bắt đầu đồng bộ giá chi tiết cho {symbol} từ {from_date} đến {to_date}")
        return self.fetch_daily_stock_prices(symbol, from_date, to_date)

    def fetch_index_list(self, market: str = 'HOSE', max_retries: int = 3) -> bool:
        """Đồng bộ danh sách chỉ số (Index List) của một sàn"""
        for attempt in range(max_retries):
            try:
                res = self.api.get_index_list(market, 1, 100)
                status = res.get('status')

                if status == 'Success':
                    data = res.get('data', [])
                    if not data:
                        logger.warning(f"Không có dữ liệu index list cho sàn {market}")
                        return False

                    # Lọc sạch phần tử lỗi trước khi đưa vào transformer giống logic cũ
                    clean_data = [item for item in data if item.get('IndexCode')]
                    df = self.transformer.index_list_to_df(clean_data)

                    if not df.empty:
                        self.db.save_data(df, 'index_list', ['index_code'])
                        logger.info(f"✅ Đã đồng bộ {len(df)} index cho sàn {market}")
                        return True
                    return False

                elif status == 401 or res.get('statusCode') == 401:
                    logger.error("Token hết hạn khi lấy index list, đang refresh...")
                    self.api.get_access_token()
                elif status == 429 or res.get('statusCode') == 429:
                    wait = (attempt + 1) * 2
                    logger.warning(f"Rate limit index list cho sàn {market}, đợi {wait}s")
                    time.sleep(wait)
                else:
                    logger.error(f"Lỗi lấy index list sàn {market}: {res}")
                    return False
            except Exception as e:
                logger.exception(f"Lỗi trong fetch_index_list ({market}): {e}")
                time.sleep(1)
        return False

    def sync_index_lists(self) -> bool:
        """Đồng bộ danh sách chỉ số cho tất cả các sàn"""
        markets = ['HOSE', 'HNX', 'UPCOM']
        total_success = 0

        for market in markets:
            logger.info(f"🔄 Đang đồng bộ danh mục chỉ số sàn: {market}")
            if self.fetch_index_list(market):
                total_success += 1
            time.sleep(0.5)

        logger.info(f"✅ Hoàn tất đồng bộ danh mục chỉ số ({total_success}/{len(markets)} sàn thành công)")
        return total_success == len(markets)

    def fetch_daily_index(self, index_code: str, from_date: str, to_date: str, chunk_days: int = 30,max_retries: int = 3) -> bool:
        """
        Lấy dữ liệu lịch sử của một chỉ số theo từng đoạn nhỏ (chunk) để tránh giới hạn 30 ngày của API.
        """
        start_dt = datetime.strptime(from_date, '%d/%m/%Y')
        end_dt = datetime.strptime(to_date, '%d/%m/%Y')
        current_start = start_dt
        delta = timedelta(days=chunk_days - 1)  # Đảm bảo tính cả ngày bắt đầu (inclusive)

        # Sử dụng tqdm con dạng ngắn để theo dõi tiến độ từng mã chỉ số (nếu cần hiển thị lồng)
        with tqdm(desc=f"  ↳ {index_code}", unit="chunk", leave=False) as pbar_chunks:
            while current_start <= end_dt:
                current_end = min(current_start + delta, end_dt)
                str_start = current_start.strftime('%d/%m/%Y')
                str_end = current_end.strftime('%d/%m/%Y')
                pbar_chunks.set_postfix({"range": f"{str_start}-{str_end}"})

                success = False
                for attempt in range(max_retries):
                    if self._execute_fetch_daily_index(index_code, str_start, str_end):
                        success = True
                        break
                    else:
                        logger.warning(f"⚠️ Thử lại lần {attempt + 1} lấy daily index mã {index_code} [{str_start}]")
                        time.sleep(1)

                pbar_chunks.update(1)

                # Sleep 0.5s giữa các chu kỳ chunk để giảm tải cho API
                time.sleep(0.5)

                if success:
                    current_start = current_end + timedelta(days=1)
                else:
                    logger.error(f"❌ Thất bại hoàn toàn tại mã chỉ số {index_code} khoảng [{str_start} - {str_end}]")
                    # Tiếp tục nhảy sang chunk tiếp theo để tránh bị treo tiến trình
                    current_start = current_end + timedelta(days=1)

        return True

    def _execute_fetch_daily_index(self, index_code: str, from_date: str, to_date: str) -> bool:
        """
        Thực hiện một request thực tế để lấy dữ liệu daily index trong phạm vi an toàn (<=30 ngày)
        """
        try:
            res = self.api.get_daily_index(index_code, from_date, to_date)
            status = res.get('status')

            if status == 'Success':
                data = res.get('data', [])
                if not data:
                    # Không có dữ liệu (ngày nghỉ/cuối tuần) vẫn coi là thành công để chạy tiếp
                    return True

                df = self.transformer.daily_index_to_df(index_code, data)
                if not df.empty:
                    self.db.save_data(df, 'daily_index', ['index_code', 'trading_date'])
                return True

            elif status == 401 or res.get('statusCode') == 401:
                logger.error("Token hết hạn khi lấy daily index, đang refresh...")
                self.api.get_access_token()
                return False
            elif status == 429 or res.get('statusCode') == 429:
                logger.warning(f"Rate limit daily index cho mã {index_code}")
                return False
            else:
                logger.error(f"API Error khi lấy daily index {index_code}: {res.get('message')}")
                return False
        except Exception as e:
            logger.error(f"Exception trong _execute_fetch_daily_index ({index_code}): {e}")
            return False

    def sync_all_daily_index(self, market: str = 'HOSE', from_date: str = '01/01/2021', maintenance_mode: bool = False):
        """
        Đồng bộ dữ liệu lịch sử (Daily Index) cho toàn bộ chỉ số của một sàn.
        :param market: Tên sàn ('HOSE', 'HNX', 'UPCOM') tương ứng cột `exchange` trong DB
        :param from_date: Ngày bắt đầu mặc định nếu không chạy maintenance (định dạng dd/mm/yyyy)
        :param maintenance_mode: Nếu True, tự động check ngày gần nhất trong DB để sync bù dữ liệu thiếu
        """
        # 1. Lấy danh sách các index_code từ bảng index_list thông qua handler
        indices = self.db.get_all_indices(market=market)

        if not indices:
            logger.warning(
                f"❌ Không tìm thấy mã chỉ số nào có exchange = '{market}' trong bảng index_list. Vui lòng chạy sync_index_lists trước.")
            return

        # 2. Xác định ngày kết thúc (to_date) dựa theo giờ đóng cửa thị trường (16:00)
        now = datetime.now()
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        to_date = now.date() if now >= market_close else now.date() - timedelta(days=1)
        to_date_str = to_date.strftime('%d/%m/%Y')

        logger.info(
            f"🚀 Bắt đầu sync bảng daily_index cho sàn {market}: {len(indices)} chỉ số | Chế độ bảo trì: {maintenance_mode}")

        # 3. Vòng lặp đồng bộ từng chỉ số
        pbar = tqdm(indices, desc=f"📈 Sync Daily Index {market}", unit="index")
        for index_code in pbar:
            try:
                pbar.set_postfix({"current": index_code})

                # Tính toán ngày bắt đầu dựa theo chế độ chạy
                if maintenance_mode:
                    # Lấy ngày lớn nhất hiện tại của index_code này trong bảng daily_index
                    last_date = self.db.get_latest_trading_date('daily_index', index_code)
                    start_date = (last_date + timedelta(days=1)) if last_date \
                        else datetime.strptime(from_date, '%d/%m/%Y').date()
                else:
                    start_date = datetime.strptime(from_date, '%d/%m/%Y').date()

                # Chỉ gọi API nếu start_date nhỏ hơn hoặc bằng to_date
                if start_date <= to_date:
                    start_date_str = start_date.strftime('%d/%m/%Y')
                    self.fetch_daily_index(index_code, start_date_str, to_date_str)
                else:
                    logger.debug(f"Chỉ số {index_code} đã cập nhật đầy đủ đến ngày mới nhất.")
            except Exception as e:
                logger.error(f"❌ Lỗi khi đồng bộ dữ liệu daily_index cho mã {index_code}: {e}")
            finally:
                time.sleep(0.5)


