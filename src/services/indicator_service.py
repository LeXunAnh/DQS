from __future__ import annotations

import logging
from typing import Optional

from src.indicators import ma,atr,bollinger,foreign_flow,macd,rsi,stochastic,volume
from src.utils.price_utils import adjust_prices
from src.services.base_symbol_service import BaseSymbolService

import pandas as pd
from sqlalchemy import text


from src.database.handler import DatabaseHandler

logger = logging.getLogger(__name__)

class IndicatorService(BaseSymbolService):
    """
    Điều phối tính toán & lưu trữ chỉ báo kỹ thuật.

    Cách dùng nhanh:
        svc = IndicatorService(db)
        svc.run_one("SSI")
        svc.run_all("HOSE")
        svc.run_maintenance("HOSE")   # chỉ tính ngày chưa có
    """

    # Cần ít nhất bao nhiêu nến để kết quả có ý nghĩa (MA200)
    _label = "Indicators"

    # Cần ít nhất bao nhiêu nến để kết quả có ý nghĩa (MA200)
    MIN_HISTORY_DAYS = 300
    _WARMUP_BUFFER = 30

    _PRICE_COLUMNS = (
        "trading_date",
        "open_price",
        "highest_price",
        "lowest_price",
        "close_price",
        "close_price_adjusted",
        "total_traded_vol",
        "net_buy_sell_vol",
        "net_buy_sell_val",
    )
    # No extra WHERE filters — invalid rows are dropped inside adjust_prices()
    _EXTRA_WHERE = ""

    # Cột DB output tương ứng với cột pandas
    _OUTPUT_COLS = [
        "symbol", "trading_date",
        # Trend
        "ma5", "ma10", "ma20", "ma50", "ma200",
        "ema9", "ema12", "ema26",
        # Momentum
        "rsi14",
        "macd", "macd_signal", "macd_hist",
        "stoch_k", "stoch_d",
        # Volatility
        "bb_upper", "bb_middle", "bb_lower", "bb_width", "atr14",
        # Volume
        "vol_ma20", "vol_ratio", "obv",
        # Foreign flow
        "net_foreign_vol_5d", "net_foreign_vol_10d",
        "net_foreign_val_5d", "net_foreign_val_10d",
    ]

    def _get_latest_date(self, symbol: str):
        """Ngày gần nhất đã có indicator trong DB (hoặc None)"""
        query = text(
            "SELECT MAX(trading_date) FROM technical_indicators WHERE symbol = :sym"
        )
        try:
            with self.db.engine.connect() as conn:
                return conn.execute(query, {"sym": symbol}).scalar()
        except Exception as e:
            logger.error(f"Lỗi lấy max indicator date {symbol}: {e}")
            return None

    def _save(self, df: pd.DataFrame):
        """Upsert vào technical_indicators"""
        if df.empty:
            return
        # Chỉ lấy các cột đã khai báo, bỏ NaN-all rows
        out = df[self._OUTPUT_COLS].dropna(subset=["ma5"])  # ít nhất MA5 phải có
        if out.empty:
            return
        self.db.save_data(out, "technical_indicators", ["symbol", "trading_date"])

    # ─── Compute pipeline ───────────────────────────────────────

    def _compute(self, symbol: str, raw: pd.DataFrame, from_date: Optional[str] = None) -> pd.DataFrame:
        """Pipeline tính toán tuần tự tất cả indicators"""
        raw["symbol"] = symbol
        # 1. Điều chỉnh giá
        df = adjust_prices(raw)

        # 2. Gọi từng module chỉ báo
        df = ma.calc_ma_family(df)
        df = rsi.calc_rsi(df)
        df = macd.calc_macd(df)
        df = stochastic.calc_stochastic(df)
        df = bollinger.calc_bollinger(df)
        df = atr.calc_atr(df)
        df = volume.calc_volume_indicators(df)
        df = foreign_flow.calc_volume_indicators(df)

        # Lọc từ from_date
        if from_date:
            df = df[df["trading_date"] >= pd.Timestamp(from_date)]
        return df

    # ─── Public API ─────────────────────────────────────────────

    def run_one(self, symbol: str, from_date: Optional[str] = None) -> int:
        """
        Tính và lưu indicators cho MỘT mã.

        Args:
            symbol   : Mã chứng khoán, ví dụ 'SSI'
            from_date: 'YYYY-MM-DD' — chỉ lưu từ ngày này trở đi.
                       None → tính toàn bộ lịch sử.

        Returns:
            Số rows đã ghi (0 nếu thất bại hoặc không có dữ liệu).
        """
        raw = self._fetch_prices(symbol, from_date)
        if raw.empty:
            logger.warning(f"⚠️  {symbol}: Không có dữ liệu giá")
            return 0

        result = self._compute(symbol, raw, from_date)
        if result.empty:
            return 0

        self._save(result)
        n = len(result)
        logger.info(f"✅ {symbol}: Đã tính {n} rows indicators")
        return n

    def run_single_date(self, symbol: str, date: str) -> pd.Series | None:
        """
        Tính indicators cho MỘT NGÀY CỤ THỂ của một mã.
        Trả về pd.Series chứa tất cả giá trị indicators.
        """
        raw = self._fetch_prices(symbol, date)  # warmup đã có sẵn
        if raw.empty:
            return None
        raw["symbol"] = symbol
        result = self._compute(symbol, raw, from_date=None)
        row = result[result["trading_date"] == pd.Timestamp(date)]
        return row.squeeze() if not row.empty else None

# ═══════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    db = DatabaseHandler()
    svc = IndicatorService(db)

    while True:
        print("\n" + "=" * 50)
        print("INDICATOR SERVICE")
        print("=" * 50)
        print("1. Tính 1 mã (toàn bộ lịch sử)")
        print("2. Tính 1 mã từ ngày cụ thể")
        print("3. Tính tất cả mã (1 sàn)")
        print("4. Bảo trì (chỉ ngày còn thiếu)")
        print("5. Kiểm tra 1 ngày cụ thể (debug)")
        print("6. Thoát")

        choice = input("Lựa chọn (1-6): ").strip()

        if choice == "1":
            sym = input("Mã (ví dụ SSI): ").strip().upper()
            svc.run_one(sym)

        elif choice == "2":
            sym  = input("Mã: ").strip().upper()
            date = input("Từ ngày (YYYY-MM-DD): ").strip()
            svc.run_one(sym, from_date=date)

        elif choice == "3":
            market = input("Sàn (HOSE/HNX/UPCOM, mặc định HOSE): ").strip() or "HOSE"
            date   = input("Từ ngày (YYYY-MM-DD, Enter = toàn bộ): ").strip() or None
            svc.run_all(market, from_date=date)

        elif choice == "4":
            market = input("Sàn (mặc định HOSE): ").strip() or "HOSE"
            svc.run_maintenance(market)

        elif choice == "5":
            sym  = input("Mã: ").strip().upper()
            date = input("Ngày (YYYY-MM-DD): ").strip()
            row  = svc.run_single_date(sym, date)
            if row is not None:
                print(f"\n── Indicators {sym} @ {date} ──")
                print(row.to_string())
            else:
                print("Không có dữ liệu.")

        elif choice == "6":
            print("Thoát.")
            break
        else:
            print("Vui lòng nhập 1-6.")
