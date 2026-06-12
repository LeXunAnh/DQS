# main.py
import logging
import sys
from datetime import datetime, timedelta

from src.database.handler import DatabaseHandler
from src.core.api_client import SSIAPIClient
from src.core.transformer import DataTransformer
from src.core.sync_service import SyncService
from src.database.gap_service import GapRepairService
from src.services.indicator_service import IndicatorService
from src.services.signal_service import SignalService
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    try:
        db = DatabaseHandler()
    except Exception as e:
        logger.error(f"Không thể kết nối database: {e}")
        return

    api_client    = SSIAPIClient(config)
    transformer   = DataTransformer()
    sync_service  = SyncService(api_client, db, transformer)
    gap_service   = GapRepairService(db, sync_service)
    indicator_svc = IndicatorService(db)
    signal_svc    = SignalService(db)

    while True:
        print("\n" + "=" * 55)
        print("  QUANTSTOCK — HỆ THỐNG GIAO DỊCH ĐỊNH LƯỢNG")
        print("=" * 55)
        print("── Đồng bộ dữ liệu ──────────────────────────────────")
        print("  1. Đồng bộ danh mục securities (tất cả sàn)")
        print("  2. Đồng bộ 1 mã OHLC")
        print("  3. Đồng bộ tất cả mã OHLC")
        print("  4. Đồng bộ 1 mã giá chi tiết")
        print("  5. Đồng bộ tất cả mã giá chi tiết")
        print("  6. Bảo trì dữ liệu (cập nhật ngày thiếu)")
        print("  7. Vá lỗ hổng dữ liệu")
        print("── Chỉ báo kỹ thuật ──────────────────────────────────")
        print("  8. Tính indicators cho 1 mã")
        print("  9. Tính indicators toàn sàn")
        print(" 10. Bảo trì indicators (chỉ ngày còn thiếu)")
        print(" 11. Kiểm tra indicators 1 ngày (debug)")
        print("── Tín hiệu giao dịch ────────────────────────────────")
        print(" 12. Phát hiện tín hiệu cho 1 mã")
        print(" 13. Phát hiện tín hiệu toàn sàn")
        print(" 14. Bảo trì tín hiệu (chỉ ngày còn thiếu)")
        print(" 15. Screener — xem tín hiệu mới nhất")
        print("──────────────────────────────────────────────────────")
        print("  0. Thoát")

        choice = input("\nNhập lựa chọn: ").strip()

        # ── Sync ──────────────────────────────────────────────

        if choice == '1':
            logger.info("Bắt đầu đồng bộ danh mục securities")
            sync_service.sync_all_markets()

        elif choice == '2':
            symbol   = input("Mã chứng khoán (ví dụ SSI): ").strip().upper()
            from_date = input("Từ ngày (dd/mm/yyyy, mặc định 01/01/2015): ").strip() or '01/01/2015'
            to_date   = input("Đến ngày (dd/mm/yyyy, mặc định hôm qua): ").strip()
            if not to_date:
                to_date = (datetime.now() - timedelta(days=1)).strftime('%d/%m/%Y')
            sync_service.sync_one_ohlc(symbol, from_date, to_date)

        elif choice == '3':
            market    = input("Sàn (HOSE/HNX/UPCOM, mặc định HOSE): ").strip() or 'HOSE'
            from_date = input("Từ ngày (dd/mm/yyyy, mặc định 01/01/2015): ").strip() or '01/01/2015'
            to_date   = input("Đến ngày (dd/mm/yyyy, mặc định hôm qua): ").strip()
            if not to_date:
                to_date = (datetime.now() - timedelta(days=1)).strftime('%d/%m/%Y')
            sync_service.sync_all_ohlc(market, from_date, to_date)

        elif choice == '4':
            symbol    = input("Mã chứng khoán: ").strip().upper()
            from_date = input("Từ ngày (dd/mm/yyyy, mặc định 01/01/2021): ").strip() or '01/01/2021'
            to_date   = input("Đến ngày (dd/mm/yyyy, mặc định hôm qua): ").strip()
            if not to_date:
                to_date = (datetime.now() - timedelta(days=1)).strftime('%d/%m/%Y')
            sync_service.sync_one_stock_price(symbol, from_date, to_date)

        elif choice == '5':
            market    = input("Sàn (HOSE/HNX/UPCOM, mặc định HOSE): ").strip() or 'HOSE'
            from_date = input("Từ ngày (dd/mm/yyyy, mặc định 01/01/2021): ").strip() or '01/01/2021'
            sync_service.sync_all_stock_prices(market, from_date)

        elif choice == '6':
            market = input("Sàn (mặc định HOSE): ").strip() or 'HOSE'
            mode   = input("Loại dữ liệu (ohlc/price, mặc định ohlc): ").strip().lower() or 'ohlc'
            if mode not in ('ohlc', 'price'):
                print("Chế độ không hợp lệ, dùng ohlc")
                mode = 'ohlc'
            sync_service.maintenance_sync(market, mode)

        elif choice == '7':
            market = input("Sàn (mặc định HOSE): ").strip() or 'HOSE'
            gap_service.repair_all_gaps(market)

        # ── Indicators ────────────────────────────────────────

        elif choice == '8':
            symbol    = input("Mã chứng khoán (ví dụ SSI): ").strip().upper()
            from_date = input("Từ ngày YYYY-MM-DD (Enter = toàn bộ lịch sử): ").strip() or None
            indicator_svc.run_one(symbol, from_date=from_date)

        elif choice == '9':
            market    = input("Sàn (HOSE/HNX/UPCOM, mặc định HOSE): ").strip() or 'HOSE'
            from_date = input("Từ ngày YYYY-MM-DD (Enter = toàn bộ lịch sử): ").strip() or None
            indicator_svc.run_all(market, from_date=from_date)

        elif choice == '10':
            market = input("Sàn (mặc định HOSE): ").strip() or 'HOSE'
            indicator_svc.run_maintenance(market)

        elif choice == '11':
            symbol = input("Mã chứng khoán: ").strip().upper()
            date   = input("Ngày (YYYY-MM-DD): ").strip()
            row    = indicator_svc.run_single_date(symbol, date)
            if row is not None:
                print(f"\n── Indicators {symbol} @ {date} ──")
                print(row.to_string())
            else:
                print("Không có dữ liệu cho mã/ngày này.")

        elif choice == '12':
            symbol    = input("Mã chứng khoán (ví dụ SSI): ").strip().upper()
            from_date = input("Từ ngày YYYY-MM-DD (Enter = toàn bộ): ").strip() or None
            signal_svc.run_one(symbol, from_date=from_date)

        elif choice == '13':
            market    = input("Sàn (HOSE/HNX/UPCOM, mặc định HOSE): ").strip() or 'HOSE'
            from_date = input("Từ ngày YYYY-MM-DD (Enter = toàn bộ): ").strip() or None
            signal_svc.run_all(market, from_date=from_date)

        elif choice == '14':
            market = input("Sàn (mặc định HOSE): ").strip() or 'HOSE'
            signal_svc.run_maintenance(market)

        elif choice == '15':
            market    = input("Sàn (mặc định HOSE): ").strip() or 'HOSE'
            direction = input("Chiều (BUY/SELL/Enter=tất cả): ").strip().upper() or None
            strength  = float(input("Strength tối thiểu 0.0-1.0 (mặc định 0.3): ").strip() or '0.3')
            result    = signal_svc.get_latest_signals(market=market, direction=direction, min_strength=strength)
            if result.empty:
                print("Không có tín hiệu.")
            else:
                print(result.to_string(index=False))

        elif choice == '0':
            print("Thoát chương trình.")
            break

        else:
            print("Vui lòng nhập số từ 0 đến 15.")

if __name__ == "__main__":
    main()