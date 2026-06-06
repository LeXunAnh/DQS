from sqlalchemy import create_engine,text
from sqlalchemy.pool import QueuePool
from datetime import date as date_type, timedelta
import pandas as pd
import config
import logging

logger = logging.getLogger(__name__)

class DatabaseHandler:
    def __init__(self):
        self.db_uri = config.DB_URI
        if not self.db_uri:
            raise ValueError("Not found DB_URI trong file config")
        self.engine = create_engine(
            self.db_uri,
            poolclass=QueuePool,
            pool_size=5,  # Số connection thường trực
            max_overflow=10,  # Cho phép mở thêm khi burst
            pool_timeout=30,  # Timeout chờ connection (giây)
            pool_recycle=1800,  # Recycle connection sau 30 phút (tránh stale)
            pool_pre_ping=True  # Ping trước khi dùng, tránh "connection closed" error
        )

    def save_data(self, df: pd.DataFrame, table_name: str, conflict_columns: list):
        if df.empty:
            return

        cols = df.columns.tolist()
        col_names = ", ".join(cols)
        placeholders = ", ".join([f":{c}" for c in cols])

        update_cols = [c for c in cols if c not in conflict_columns]
        conflict_stmt = ", ".join(conflict_columns)

        if update_cols:
            update_stmt = ", ".join(
                [f"{c} = EXCLUDED.{c}" for c in update_cols]
            )
            conflict_sql = f"DO UPDATE SET {update_stmt}"
        else:
            conflict_sql = "DO NOTHING"

        query = text(f"""
            INSERT INTO {table_name} ({col_names})
            VALUES ({placeholders})
            ON CONFLICT ({conflict_stmt})
            {conflict_sql};
        """)

        batch_size = 1000
        total_rows_affected = 0
        try:
            with self.engine.begin() as conn:
                for i in range(0, len(df), batch_size):
                    batch = df.iloc[i:i + batch_size]
                    result= conn.execute(query,batch.to_dict(orient="records"))
                    total_rows_affected += result.rowcount
            logger.info(f"✅ Finished {table_name}: Processed {len(df)} rows. Affected (Upserted): {total_rows_affected} rows.")
        except Exception as e:
            logger.exception(f"❌ SQL error while saving to {table_name}: {e}")
            raise

    def get_all_symbols(self, market=None):
        """Lấy danh sách symbol từ bảng securities"""
        query = "SELECT symbol FROM securities"
        params = {}

        if market:
            query += " WHERE market = :market"
            params['market'] = market

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query), params)
                # Trả về một list đơn giản: ['SSI', 'FPT', 'VNM', ...]
                return [row[0] for row in result]
        except Exception as e:
            logger.error(f"❌ Lỗi khi lấy danh sách symbol: {e}")
            return []

    def get_all_symbols_except_CQ(self, market=None, only_companies=True):
        """
        Lấy danh sách mã chứng khoán.
        :param only_companies: Nếu True, chỉ lấy mã 3 ký tự (Cổ phiếu/ETF).
                               Nếu False, lấy tất cả (bao gồm Chứng quyền ~6-8 ký tự).
        """
        query = "SELECT symbol FROM securities WHERE 1=1"
        params = {}
        if only_companies:
            query += " AND symbol ~ '^[A-Z0-9]{3}$'"  # Lấy 3 ký tự (chữ hoặc số như ETF)
        if market:
            query += " AND market = :market"
            params["market"] = market
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query), params)
                return [row[0] for row in result]
        except Exception as e:
            logger.error(f"Lỗi khi lấy danh sách mã công ty: {e}")
            raise

    def get_latest_trading_date_index(self, table_name, symbol):
        """Lấy ngày giao dịch gần nhất của 1 mã trong bảng được chỉ định (Hỗ trợ cả Stock và Index)"""

        # Tự động xác định tên cột điều kiện dựa trên tên bảng
        # Nếu bảng chứa chữ 'index', đổi tên cột lọc từ 'symbol' thành 'index_code' (hoặc 'code')
        column_name = "index_code" if "index" in table_name.lower() else "symbol"

        query = text(f"SELECT MAX(trading_date) FROM {table_name} WHERE {column_name} = :symbol")

        try:
            with self.engine.connect() as conn:
                result = conn.execute(query, {"symbol": symbol}).scalar()
                return result  # Trả về đối tượng date hoặc None
        except Exception as e:
            logger.error(f"Lỗi khi lấy max date của {symbol} trong bảng {table_name}: {e}")
            return None

    def get_latest_trading_date(self, table_name: str, symbol: str) -> date_type | None:
        """
        Lấy ngày giao dịch gần nhất của 1 mã / index trong bảng được chỉ định.

        Tự động chọn tên cột điều kiện:
          • Bảng có chữ 'index' trong tên → dùng cột 'index_code'
          • Ngược lại                     → dùng cột 'symbol'
        """
        col = "index_code" if "index" in table_name.lower() else "symbol"
        query = text(
            f"SELECT MAX(trading_date) FROM {table_name} WHERE {col} = :identifier"
        )
        try:
            with self.engine.connect() as conn:
                return conn.execute(query, {"identifier": symbol}).scalar()
        except Exception as e:
            logger.error(f"Lỗi khi lấy max date của {symbol} trong {table_name}: {e}")
            return None

    def optimize_db(self):
        # Dùng AUTOCOMMIT để ANALYZE chạy ngoài transaction block
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("ANALYZE daily_ohlc"))
            conn.execute(text("ANALYZE daily_stock_prices"))
        logger.info("🚀 DB Optimized: Statistics updated for query planner.")

    def get_data_gaps(self, symbol):
        query = text("""
            WITH date_series AS (
                SELECT trading_date,
                       LEAD(trading_date) OVER (ORDER BY trading_date) as next_date
                FROM daily_stock_prices
                WHERE symbol = :symbol
            ),
            gaps AS (
                SELECT trading_date, next_date
                FROM date_series
                WHERE next_date - trading_date > 1
                AND next_date IS NOT NULL
            )
            SELECT g.trading_date + INTERVAL '1 day' as gap_start,
                   g.next_date - INTERVAL '1 day'    as gap_end
            FROM gaps g
            WHERE EXISTS (
                SELECT 1 FROM trading_calendar tc
                WHERE tc.trading_date > g.trading_date
                  AND tc.trading_date < g.next_date
                  AND tc.is_trading_day = TRUE
            )
            ORDER BY gap_start;
        """)

        try:
            with self.engine.connect() as conn:
                result = conn.execute(query, {"symbol": symbol})
                return [(row[0], row[1]) for row in result]
        except Exception as e:
            logger.error(f"❌ Lỗi khi tìm gap cho {symbol}: {e}")
            return []

    def get_all_indices(self, market: str) -> list:
        """
        Lấy danh sách index_code từ bảng index_list dựa trên sàn (exchange)
        :param market: Tên sàn cần lấy ('HOSE', 'HNX', 'UPCOM')
        :return: Danh sách các chuỗi index_code, ví dụ: ['VNINDEX', 'VN30']
        """
        excluded_codes = (
            'VNSMALLCAP', 'VNXALLSHARE', 'VN50 GROWTH', 'VNALLSHARE',
            'VNDIVIDEND', 'VNMIDCAP', 'VNMITECH', 'VNSHINE')

        query = """
            SELECT index_code 
            FROM index_list 
            WHERE exchange = :market 
              AND index_code NOT IN :excluded_codes
        """
        try:
            with self.engine.connect() as conn:
                from sqlalchemy import text
                result = conn.execute(
                    text(query),
                    {
                        "market": market,
                        "excluded_codes": excluded_codes
                    }
                )
                return [row[0] for row in result.fetchall()]
        except Exception as e:
            logger.error(f"Lỗi khi lấy danh sách chỉ số cho sàn {market}: {e}")
            return []

    # def get_latest_trading_date(self, table_name, symbol):
    #     """Lấy ngày giao dịch gần nhất của 1 mã trong bảng được chỉ định"""
    #     query = text(f"SELECT MAX(trading_date) FROM {table_name} WHERE symbol = :symbol")
    #     try:
    #         with self.engine.connect() as conn:
    #             result = conn.execute(query, {"symbol": symbol}).scalar()
    #             return result  # Trả về đối tượng date hoặc None
    #     except Exception as e:
    #         logger.error(f"Lỗi khi lấy max date của {symbol}: {e}")
    #         return None


# ── Chart/ UI fetch methods ────────────────────────────────────────
    def fetch_price_with_warmup(self, symbol: str, start: date_type, end: date_type) -> pd.DataFrame:
        """Fetch giá với warmup 270 ngày lịch để MA200 hội tụ đúng."""
        warmup = start - timedelta(days=270)
        q = text("""
            SELECT trading_date, open_price, highest_price, lowest_price,
                   close_price, close_price_adjusted, total_match_vol,
                   foreign_buy_vol_total, foreign_sell_vol_total
            FROM daily_stock_prices
            WHERE symbol = :sym
              AND trading_date BETWEEN :s AND :e
              AND close_price > 0
              AND close_price_adjusted IS NOT NULL
            ORDER BY trading_date
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn, params={"sym": symbol, "s": warmup, "e": end})
            df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
            return df
        except Exception as ex:
            logger.error(f"Lỗi fetch price {symbol}: {ex}")
            return pd.DataFrame()

    def fetch_signals_for_chart(self, symbol: str, start: date_type, end: date_type) -> pd.DataFrame:
        """Fetch trading signals cho một mã trong khoảng ngày — dùng cho chart."""
        q = text("""
            SELECT signal_date, signal_type, signal_direction, strength, close_price
            FROM trading_signals
            WHERE symbol = :sym
              AND signal_date BETWEEN :s AND :e
            ORDER BY signal_date
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn, params={"sym": symbol, "s": start, "e": end})
            df["signal_date"] = pd.to_datetime(df["signal_date"]).dt.date
            return df
        except Exception:
            return pd.DataFrame()

    def fetch_indicator_data(self, symbol: str, start: date_type, end: date_type) -> pd.DataFrame:
        """Fetch vol_ma20 từ technical_indicators — dùng cho volume panel trên chart."""
        q = text("""
            SELECT trading_date, vol_ma20
            FROM technical_indicators
            WHERE symbol = :sym
              AND trading_date BETWEEN :s AND :e
            ORDER BY trading_date
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn, params={"sym": symbol, "s": start, "e": end})
            df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
            return df
        except Exception as e:
            logger.error(f"Lỗi lấy indicators cho {symbol}: {e}")
            return pd.DataFrame()


# ── Sector Rotation fetch methods ────────────────────────────────────────
    def fetch_sector_score_history(self, from_date: str, to_date: str, sectors: list[str]) -> pd.DataFrame:
        """
        Fetch sector_score_daily time-series cho các ngành được chọn.
        Dùng cho score trend chart trong Sector Rotation dashboard.
        """
        q = text("""
            SELECT date, sector_name, total_score, inst_score,
                   breadth_score, regime, score_delta_1d, score_delta_5d
            FROM sector_score_daily
            WHERE date BETWEEN :f AND :t
              AND sector_name = ANY(:secs)
            ORDER BY date ASC, sector_name
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn,
                                 params={"f": from_date, "t": to_date,
                                         "secs": list(sectors)})
            df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch sector score history: {e}")
            return pd.DataFrame()

    def fetch_sector_heatmap(self, from_date: str, to_date: str) -> pd.DataFrame:
        """
        Fetch (date, sector_name, total_score, regime) cho regime heatmap.
        Caller tự slice lấy N ngày gần nhất.
        """
        q = text("""
            SELECT date, sector_name, total_score, regime
            FROM sector_score_daily
            WHERE date BETWEEN :f AND :t
            ORDER BY date ASC, sector_name
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn, params={"f": from_date, "t": to_date})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch sector heatmap: {e}")
            return pd.DataFrame()

    def fetch_sector_detail(self, sector: str, from_date: str, to_date: str) -> pd.DataFrame:
        """
        Fetch sector_factor_daily JOIN sector_score_daily cho một ngành.
        Dùng cho drill-down indicator time-series charts.
        """
        q = text("""
            SELECT
                sfd.date,
                sfd.weighted_mfi,   sfd.median_mfi,
                sfd.weighted_cmf,   sfd.median_cmf,
                sfd.weighted_rvol,
                sfd.weighted_nmf_z, sfd.weighted_nff_z,
                sfd.weighted_accel,
                sfd.breadth_cmf_positive, sfd.breadth_mfi_above_50,
                sfd.breadth_accel_above_1, sfd.breadth_nff_positive,
                sfd.n_stocks, sfd.coverage_pct,
                ssd.total_score, ssd.regime
            FROM sector_factor_daily sfd
            LEFT JOIN sector_score_daily ssd
              ON ssd.date = sfd.date AND ssd.sector_name = sfd.sector_name
            WHERE sfd.sector_name = :sec
              AND sfd.date BETWEEN :f AND :t
            ORDER BY sfd.date ASC
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn,
                                 params={"sec": sector, "f": from_date, "t": to_date})
            df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch sector detail {sector}: {e}")
            return pd.DataFrame()

    def fetch_symbol_matrix(self, sector: str, date_str: str) -> pd.DataFrame:
        """
        Fetch tất cả cổ phiếu trong một ngành cho MỘT ngày.
        Kết hợp: stock_mf_daily + securities + daily_stock_prices.
        Sắp xếp theo trading_value DESC (thanh khoản cao nhất lên đầu).
        """
        q = text("""
            SELECT
                s.symbol,
                s.stock_name,
                dsp.close_price,
                dsp.per_price_change,
                smd.mfi,
                smd.cmf,
                smd.rvol,
                smd.nmf_zscore,
                smd.nmf_accel,
                smd.nff_zscore,
                smd.trading_value
            FROM stock_mf_daily smd
            JOIN securities s ON s.symbol = smd.symbol
            LEFT JOIN daily_stock_prices dsp
                   ON dsp.symbol       = smd.symbol
                  AND dsp.trading_date = smd.date
            WHERE smd.sector_name = :sector
              AND smd.date = :date
            ORDER BY smd.trading_value DESC NULLS LAST
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn,
                                 params={"sector": sector, "date": date_str})
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch symbol matrix {sector} @ {date_str}: {e}")
            return pd.DataFrame()

    def fetch_symbol_history(self, sector: str, from_date: str, to_date: str) -> pd.DataFrame:
        """
        Fetch stock_mf_daily time-series cho TẤT CẢ cổ phiếu trong một ngành.
        Dùng cho multi-date symbol matrix (rows=symbol, cols=date).
        """
        q = text("""
            SELECT
                smd.date,
                smd.symbol,
                s.stock_name,
                smd.mfi,
                smd.cmf,
                smd.rvol,
                smd.nmf_zscore,
                smd.nmf_accel,
                smd.nff_zscore,
                smd.trading_value
            FROM stock_mf_daily smd
            JOIN securities s ON s.symbol = smd.symbol
            WHERE smd.sector_name = :sector
              AND smd.date BETWEEN :f AND :t
            ORDER BY smd.date ASC, smd.trading_value DESC NULLS LAST
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(q, conn,
                                 params={"sector": sector,
                                         "f": from_date, "t": to_date})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df
        except Exception as e:
            logger.error(f"❌ Lỗi fetch symbol history {sector}: {e}")
            return pd.DataFrame()

if __name__ == "__main__":
    #test
    db_manager = DatabaseHandler()
    #print(db_manager.get_all_symbols())
    #print(db_manager.get_all_symbols_except_CQ())
    #db_manager.optimize_db()
    #db_manager.get_data_gaps()
