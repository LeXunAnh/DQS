"""
signal_service.py
─────────────────
Đọc dữ liệu từ bảng technical_indicators, phát hiện các tín hiệu
giao dịch và ghi kết quả vào bảng trading_signals.

Chiến lược được implement:
  ┌─────────────────────────────┬───────────────────────────────────────┐
  │ Signal type                 │ Điều kiện                             │
  ├─────────────────────────────┼───────────────────────────────────────┤
  │ MA_GOLDEN_CROSS             │ MA_fast cắt lên MA_slow               │
  │ MA_DEATH_CROSS              │ MA_fast cắt xuống MA_slow             │
  │ RSI_OVERSOLD                │ RSI vừa vượt lên khỏi ngưỡng 30       │
  │ RSI_OVERBOUGHT              │ RSI vừa tụt xuống khỏi ngưỡng 70      │
  │ MACD_BULLISH_CROSS          │ MACD cắt lên Signal Line              │
  │ MACD_BEARISH_CROSS          │ MACD cắt xuống Signal Line            │
  │ BB_SQUEEZE_BREAKOUT_UP      │ Giá đóng cửa vượt BB Upper sau nén    │
  │ BB_SQUEEZE_BREAKOUT_DOWN    │ Giá đóng cửa thủng BB Lower sau nén   │
  │ VOLUME_SPIKE                │ Vol_Ratio >= ngưỡng (mặc định 2.5x)   │
  │ FOREIGN_ACCUMULATION        │ Net foreign vol 5d > ngưỡng dương     │
  │ FOREIGN_DISTRIBUTION        │ Net foreign vol 5d < ngưỡng âm        │
  └─────────────────────────────┴───────────────────────────────────────┘

Strength (0.0 → 1.0) được tính dựa trên độ lớn của tín hiệu,
không phải giá trị cố định — giúp dễ filter ở Execution Engine.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text
from tqdm import tqdm

from src.database.handler import DatabaseHandler
from src.services.sig_detect_service import SignalDetector

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# SIGNAL SERVICE  (tích hợp DB)
# ═══════════════════════════════════════════════════════════════

class SignalService:
    """
    Điều phối phát hiện & lưu trữ tín hiệu giao dịch.

    Cách dùng:
        svc = SignalService(db)
        svc.run_one("SSI")
        svc.run_all("HOSE")
        svc.run_maintenance("HOSE")   # chỉ tính ngày chưa có signal
    """

    def __init__(
        self,
        db_handler: DatabaseHandler,
        ma_pairs: list[tuple[str, str]] | None = None,
        enable: set[str] | None = None,
    ):
        self.db       = db_handler
        self.ma_pairs = ma_pairs  # None → dùng default của _SignalDetector
        self.enable   = enable    # None → bật tất cả

    # ─── DB helpers ─────────────────────────────────────────────

    def _fetch_indicators(self, symbol: str, from_date: Optional[str] = None) -> pd.DataFrame:
        """
        JOIN technical_indicators với daily_stock_prices để lấy giá điều chỉnh
        (close_price_adjusted, open_price * adj_factor) — nhất quán với indicator_service.
        close_price  = close_price_adjusted
        open_price   = open_price_raw * (close_price_adjusted / close_price_raw)
        """
        params: dict = {"symbol": symbol}
        date_clause  = ""
        if from_date:
            date_clause = "AND ti.trading_date >= :from_date"
            params["from_date"] = from_date

        query = text(f"""
            SELECT
                ti.trading_date,
                -- Giá điều chỉnh — khớp với những gì indicator_service đã tính
                sp.close_price_adjusted                                          AS close_price,
                ROUND(sp.open_price *
                    (sp.close_price_adjusted / NULLIF(sp.close_price, 0)), 2)   AS open_price,
                -- Indicators
                ti.ma5, ti.ma10, ti.ma20, ti.ma50, ti.ma200,
                ti.rsi14,
                ti.macd, ti.macd_signal, ti.macd_hist,
                ti.bb_upper, ti.bb_middle, ti.bb_lower, ti.bb_width,
                ti.atr14,
                ti.vol_ma20, ti.vol_ratio,
                ti.net_foreign_vol_5d, ti.net_foreign_val_5d
            FROM technical_indicators ti
            JOIN daily_stock_prices sp
              ON sp.symbol = ti.symbol
             AND sp.trading_date = ti.trading_date
             AND sp.close_price > 0
             AND sp.close_price_adjusted IS NOT NULL
            WHERE ti.symbol = :symbol
              {date_clause}
            ORDER BY ti.trading_date ASC
        """)
        try:
            with self.db.engine.connect() as conn:
                return pd.read_sql(query, conn, params=params)
        except Exception as e:
            logger.error(f"❌ Lỗi fetch indicators {symbol}: {e}")
            return pd.DataFrame()

    def _get_latest_signal_date(self, symbol: str):
        query = text(
            "SELECT MAX(signal_date) FROM trading_signals WHERE symbol = :sym"
        )
        try:
            with self.db.engine.connect() as conn:
                return conn.execute(query, {"sym": symbol}).scalar()
        except Exception as e:
            logger.error(f"Lỗi lấy max signal date {symbol}: {e}")
            return None

    def _ensure_unique_constraint(self):
        """
        Tự động tạo UNIQUE constraint (symbol, signal_date, signal_type)
        nếu chưa tồn tại. Chạy idempotent — safe để gọi nhiều lần.
        """
        check_sql = text("""
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_trading_signals_symbol_date_type'
        """)
        try:
            with self.db.engine.connect() as conn:
                exists = conn.execute(check_sql).scalar()

            if not exists:
                logger.info("⚙️  Thêm UNIQUE constraint vào trading_signals...")
                with self.db.engine.begin() as conn:
                    # Xoá duplicate trước (giữ id nhỏ nhất)
                    conn.execute(text("""
                        DELETE FROM trading_signals
                        WHERE id NOT IN (
                            SELECT MIN(id)
                            FROM trading_signals
                            GROUP BY symbol, signal_date, signal_type
                        )
                    """))
                    conn.execute(text("""
                        ALTER TABLE trading_signals
                            ADD CONSTRAINT uq_trading_signals_symbol_date_type
                            UNIQUE (symbol, signal_date, signal_type)
                    """))
                logger.info("✅ Đã thêm UNIQUE constraint thành công.")
        except Exception as e:
            logger.error(f"❌ Không thể tạo constraint: {e}")
            raise

    def _save(self, signals: list[dict]):
        if not signals:
            return
        #self._ensure_unique_constraint()
        df = pd.DataFrame(signals)
        self.db.save_data(
            df,
            "trading_signals",
            ["symbol", "signal_date", "signal_type"],
        )

    # ─── Public API ─────────────────────────────────────────────

    def run_one(self, symbol: str, from_date: Optional[str] = None) -> int:
        """
        Phát hiện và lưu tín hiệu cho MỘT mã.
        Trả về số tín hiệu được ghi.

        Args:
            symbol   : Mã chứng khoán, ví dụ 'SSI'
            from_date: 'YYYY-MM-DD' — chỉ tính từ ngày này. None = toàn bộ.
        """
        df = self._fetch_indicators(symbol, from_date)
        if df.empty:
            logger.warning(f"⚠️  {symbol}: Không có indicators")
            return 0

        signals = SignalDetector.run(df, symbol, self.ma_pairs, self.enable)
        if not signals:
            logger.debug(f"  {symbol}: Không có tín hiệu mới")
            return 0

        self._save(signals)
        logger.info(f"✅ {symbol}: Ghi {len(signals)} tín hiệu")
        return len(signals)

    def run_all(self, market: str = "HOSE", from_date: Optional[str] = None) -> int:
        """Phát hiện tín hiệu cho TOÀN BỘ mã trên sàn."""
        symbols = self.db.get_all_symbols_except_CQ(market=market, only_companies=True)
        logger.info(f"🚀 Phát hiện tín hiệu: {len(symbols)} mã | sàn {market}")

        total = ok = fail = 0
        pbar = tqdm(symbols, desc=f"Signals {market}", unit="sym")
        for sym in pbar:
            pbar.set_postfix({"current": sym})
            try:
                n = self.run_one(sym, from_date)
                total += n
                ok += 1
            except Exception as e:
                logger.error(f"❌ {sym}: {e}")
                fail += 1

        logger.info(f"✅ Hoàn tất: {total} tín hiệu | {ok} mã OK / {fail} lỗi")
        return total

    def run_maintenance(self, market: str = "HOSE") -> int:
        """
        Chế độ BẢO TRÌ: chỉ tính các ngày chưa có tín hiệu.
        An toàn để chạy hàng ngày sau market close.
        """
        symbols = self.db.get_all_symbols_except_CQ(market=market, only_companies=True)
        logger.info(f"🔄 Bảo trì signals: {len(symbols)} mã | sàn {market}")

        today = datetime.now().date()
        total = ok = skip = fail = 0
        pbar = tqdm(symbols, desc=f"Maintenance signals {market}", unit="sym")

        for sym in pbar:
            pbar.set_postfix({"current": sym})
            try:
                last = self._get_latest_signal_date(sym)
                if last and last >= today:
                    skip += 1
                    continue

                from_date = (last + timedelta(days=1)).strftime("%Y-%m-%d") if last else None
                n = self.run_one(sym, from_date)
                total += n
                ok += 1
            except Exception as e:
                logger.error(f"❌ {sym}: {e}")
                fail += 1

        logger.info(f"✅ Bảo trì xong: {total} tín hiệu mới | {ok} cập nhật / {skip} bỏ qua / {fail} lỗi")
        return total

    def get_latest_signals(self,market: str = "HOSE",date: Optional[str] = None,direction: Optional[str] = None,min_strength: float = 0.0,signal_types: Optional[list[str]] = None,limit: int = 100,) -> pd.DataFrame:
        """
        Query tín hiệu mới nhất — dùng cho screener hoặc dashboard.
        Args:
            market      : Lọc theo sàn
            date        : 'YYYY-MM-DD' — mặc định là ngày gần nhất có tín hiệu
            direction   : 'BUY' | 'SELL' | None (tất cả)
            min_strength: Strength tối thiểu (0.0 → 1.0)
            signal_types: ['MA_GOLDEN_CROSS', ...] — None = tất cả
            limit       : Số kết quả tối đa
        """
        params: dict = {"min_strength": min_strength, "limit": limit}
        clauses = ["ts.strength >= :min_strength"]

        if date:
            clauses.append("ts.signal_date = :date")
            params["date"] = date
        else:
            # Tự lấy ngày gần nhất có signal
            clauses.append("""
                ts.signal_date = (
                    SELECT MAX(signal_date) FROM trading_signals
                )
            """)

        if direction:
            clauses.append("ts.signal_direction = :direction")
            params["direction"] = direction

        if signal_types:
            placeholders = ", ".join([f":st{i}" for i in range(len(signal_types))])
            for i, st in enumerate(signal_types):
                params[f"st{i}"] = st
            clauses.append(f"ts.signal_type IN ({placeholders})")

        if market:
            clauses.append("sec.market = :market")
            params["market"] = market

        where = " AND ".join(clauses)
        query = text(f"""
            SELECT
                ts.signal_date,
                ts.symbol,
                sec.stock_name,
                sec.market,
                ts.signal_type,
                ts.signal_direction,
                ts.strength,
                ts.close_price,
                ts.parameters
            FROM trading_signals ts
            JOIN securities sec ON sec.symbol = ts.symbol
            WHERE {where}
            ORDER BY ts.signal_date DESC, ts.strength DESC
            LIMIT :limit
        """)

        try:
            with self.db.engine.connect() as conn:
                return pd.read_sql(query, conn, params=params)
        except Exception as e:
            logger.error(f"❌ Lỗi query signals: {e}")
            return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    db  = DatabaseHandler()
    svc = SignalService(db)

    while True:
        print("\n" + "=" * 50)
        print("SIGNAL SERVICE")
        print("=" * 50)
        print("1. Phát hiện tín hiệu cho 1 mã")
        print("2. Phát hiện tín hiệu toàn sàn")
        print("3. Bảo trì (chỉ ngày còn thiếu)")
        print("4. Xem tín hiệu mới nhất (screener)")
        print("5. Thoát")

        choice = input("Lựa chọn (1-5): ").strip()

        if choice == "1":
            sym  = input("Mã (ví dụ SSI): ").strip().upper()
            date = input("Từ ngày YYYY-MM-DD (Enter = toàn bộ): ").strip() or None
            svc.run_one(sym, from_date=date)

        elif choice == "2":
            market = input("Sàn (HOSE/HNX/UPCOM, mặc định HOSE): ").strip() or "HOSE"
            date   = input("Từ ngày YYYY-MM-DD (Enter = toàn bộ): ").strip() or None
            svc.run_all(market, from_date=date)

        elif choice == "3":
            market = input("Sàn (mặc định HOSE): ").strip() or "HOSE"
            svc.run_maintenance(market)

        elif choice == "4":
            market    = input("Sàn (mặc định HOSE): ").strip() or "HOSE"
            direction = input("Chiều (BUY/SELL/Enter=tất cả): ").strip().upper() or None
            strength  = float(input("Strength tối thiểu (0.0-1.0, mặc định 0.3): ").strip() or "0.3")
            result    = svc.get_latest_signals(market=market, direction=direction, min_strength=strength)
            if result.empty:
                print("Không có tín hiệu.")
            else:
                print(result.to_string(index=False))

        elif choice == "5":
            print("Thoát.")
            break
        else:
            print("Vui lòng nhập 1-5.")
