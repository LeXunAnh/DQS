from src.core.sync_service import SyncService
from src.database.handler import DatabaseHandler
from tqdm import tqdm
import logging

logger = logging.getLogger(__name__)


class GapRepairService:
    """Tìm và vá các lỗ hổng dữ liệu"""
    def __init__(self, db_handler: DatabaseHandler, sync_service: SyncService):
        self.db = db_handler
        self.sync = sync_service

    def repair_all_gaps(self, market: str = 'HOSE'):
        """Tự động tìm và vá tất cả lỗ hổng cho các mã trên sàn"""
        symbols = self.db.get_all_symbols_except_CQ(market=market, only_companies=True)
        logger.info(f"🛠 Đang kiểm tra lỗ hổng dữ liệu cho {len(symbols)} mã...")

        total_gaps_found = 0
        total_gaps_fixed = 0
        pbar = tqdm(symbols, desc="Repairing Gaps")
        for symbol in pbar:
            gaps = self.db.get_data_gaps(symbol)
            if not gaps:
                continue
            total_gaps_found += len(gaps)
            pbar.set_postfix({"repairing": symbol, "gaps": len(gaps)})

            for gap_start, gap_end in gaps:
                str_start = gap_start.strftime('%d/%m/%Y')
                str_end = gap_end.strftime('%d/%m/%Y')
                logger.info(f"🩹 Vá mã {symbol}: {str_start} -> {str_end}")
                try:
                    # Giả sử dùng phương thức fetch_daily_stock_prices cho gap
                    self.sync.fetch_daily_stock_prices(symbol, str_start, str_end)
                    total_gaps_fixed += 1
                except Exception as e:
                    logger.error(f"Lỗi vá gap {symbol}: {e}")
        logger.info(f"✅ Hoàn tất repair: {total_gaps_fixed}/{total_gaps_found} gaps đã được vá")