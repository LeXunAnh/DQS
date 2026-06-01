-- 1. Bảng danh mục (Cần chạy trước để làm bảng gốc cho khóa ngoại)
CREATE TABLE IF NOT EXISTS securities (
    symbol VARCHAR(20) PRIMARY KEY,
    market VARCHAR(20),
    stock_name VARCHAR(255),
    stock_en_name VARCHAR(255),
	sector_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE daily_ohlc (
    symbol VARCHAR(20) NOT NULL REFERENCES securities(symbol),
    trading_date DATE NOT NULL,
    open_price NUMERIC(18, 2),
    highest_price NUMERIC(18, 2),
    lowest_price NUMERIC(18, 2),
    close_price NUMERIC(18, 2),
    volume BIGINT,
    total_value NUMERIC(25, 2), -- Giá trị giao dịch thường rất lớn
    PRIMARY KEY (symbol, trading_date)
);

CREATE TABLE daily_stock_prices (
    symbol VARCHAR(20) NOT NULL
	REFERENCES securities(symbol)
	ON UPDATE CASCADE
	ON DELETE RESTRICT,
	
	-- Giá và Biến động
    trading_date DATE NOT NULL,
    price_change NUMERIC(18, 2),
    per_price_change NUMERIC(10, 2),
    ceiling_price NUMERIC(18, 2),
    floor_price NUMERIC(18, 2),
    ref_price NUMERIC(18, 2),
    open_price NUMERIC(18, 2),
    highest_price NUMERIC(18, 2),
    lowest_price NUMERIC(18, 2),
    close_price NUMERIC(18, 2),
    average_price NUMERIC(18, 2),
    close_price_adjusted NUMERIC(18, 5),

	-- Khối lượng và Giá trị
    total_match_vol BIGINT,
    total_match_val BIGINT,
    total_deal_vol BIGINT,
    total_deal_val BIGINT,
	
	-- Khối ngoại
    foreign_buy_vol_total BIGINT,
    foreign_sell_vol_total BIGINT,
    foreign_buy_val_total BIGINT,
    foreign_sell_val_total BIGINT,
    foreign_current_room BIGINT,
    net_buy_sell_vol BIGINT,
    net_buy_sell_val BIGINT,
	
	-- Thống kê khác
    total_traded_vol BIGINT,
    total_traded_value BIGINT,
    total_buy_trade INT,
    total_buy_trade_vol BIGINT,
    total_sell_trade INT,
    total_sell_trade_vol BIGINT,

	-- Metadata
    time_str VARCHAR(20),
	created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
	updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trading_date)
);

CREATE TABLE trading_calendar (
    trading_date DATE PRIMARY KEY,
    is_trading_day BOOLEAN NOT NULL DEFAULT TRUE,
    note VARCHAR(100) );

INSERT INTO trading_calendar (trading_date, is_trading_day)
SELECT DISTINCT trading_date, TRUE
FROM daily_ohlc
WHERE symbol IN ('VCB', 'VNM', 'HPG', 'VIC', 'VHM')  -- Blue-chip, ít bị suspend nhất
  AND trading_date >= '2021-01-01'
ON CONFLICT (trading_date) DO NOTHING;

-- Index để truy vấn nhanh
CREATE INDEX idx_daily_date ON daily_stock_prices (trading_date);
CREATE INDEX idx_daily_symbol_date ON daily_stock_prices (symbol, trading_date DESC);
CREATE INDEX idx_ohlc_symbol_date ON daily_ohlc (symbol, trading_date DESC);
CREATE INDEX idx_trading_calendar_date ON trading_calendar (trading_date);
CREATE INDEX idx_securities_market ON securities(market);


----------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------
-- 1. INTRADAY OHLC BARS (từ channel B - Realtime Bars)
--    SSI đã aggregate sẵn 1m bars, ta chỉ cần lưu lại
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intraday_ohlc (
    symbol          VARCHAR(20)   NOT NULL REFERENCES securities(symbol),
    trading_date    DATE          NOT NULL,
    bar_time        TIME          NOT NULL,
    open_price      NUMERIC(18,2),
    high_price      NUMERIC(18,2),
    low_price       NUMERIC(18,2),
    close_price     NUMERIC(18,2),
    volume          BIGINT,
    created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trading_date, bar_time)
);
CREATE INDEX IF NOT EXISTS idx_intraday_ohlc_symbol_date
    ON intraday_ohlc (symbol, trading_date DESC, bar_time DESC);

-- ------------------------------------------------------------
-- 2. INTRADAY TICKS (từ channel X-TRADE)
--    Lưu tick quan trọng: price, vol, session context
--    NOTE: Không lưu toàn bộ tick (quá nhiều), chỉ lưu
--          tick có LastVol > 0 (actual match)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intraday_ticks (
    symbol          VARCHAR(20)   NOT NULL REFERENCES securities(symbol),
    trading_date    DATE          NOT NULL,
    tick_time       TIME(3)       NOT NULL,  -- millisecond precision
    last_price      NUMERIC(18,2),
    last_vol        BIGINT,
    total_vol       BIGINT,
    total_val       NUMERIC(25,2),
    change_val      NUMERIC(18,2),
    ratio_change    NUMERIC(10,4),
    trading_session VARCHAR(10),
    PRIMARY KEY (symbol, trading_date, tick_time)
);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_date
    ON intraday_ticks (symbol, trading_date DESC, tick_time DESC);

-- ------------------------------------------------------------
-- 3. INTRADAY INDEX (từ channel MI)
--    Lưu index snapshot mỗi khi nhận được update
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intraday_index (
    index_id        VARCHAR(20)   NOT NULL,
    trading_date    DATE          NOT NULL,
    bar_time        TIME          NOT NULL,
    index_value     NUMERIC(12,2),
    prior_value     NUMERIC(12,2),
    change_val      NUMERIC(10,4),
    ratio_change    NUMERIC(10,4),
    advances        INT,
    declines        INT,
    no_changes      INT,
    total_vol       NUMERIC(25,0),
    total_val       NUMERIC(30,2),
    trading_session VARCHAR(10),
    PRIMARY KEY (index_id, trading_date, bar_time)
);

-- ------------------------------------------------------------
-- 4. MARKET SESSION LOG (từ channel F)
--    Track trạng thái phiên: ATO → LO → ATC → PT → C
--    Dùng để control logic EOD trigger
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_session_log (
    symbol          VARCHAR(20)   NOT NULL,
    trading_date    DATE          NOT NULL,
    session_time    TIMESTAMP     NOT NULL,
    trading_session VARCHAR(10),  -- ATO, LO, ATC, PT, C, BREAK, HALT
    trading_status  VARCHAR(10),  -- N, H, S, NL, ST, SA, SP
    PRIMARY KEY (symbol, session_time)
);
CREATE INDEX IF NOT EXISTS idx_session_log_date
    ON market_session_log (trading_date DESC, symbol);

-- ------------------------------------------------------------
-- 5. ORDER BOOK CACHE (Placeholder — quyết định sau)
--    Hiện tại chỉ giữ in-memory, bảng này để dành
-- ------------------------------------------------------------
-- CREATE TABLE IF NOT EXISTS order_book_snapshots ( ... );
-- ------------------------------------------------------------
-- 6. TECHNICAL INDICATORS (EOD Analytics)
--    Pre-computed sau mỗi phiên, query nhanh
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS technical_indicators (
    symbol          VARCHAR(20)   NOT NULL REFERENCES securities(symbol),
    trading_date    DATE          NOT NULL,

    -- === TREND ===
    ma5             NUMERIC(18,4),
    ma10            NUMERIC(18,4),
    ma20            NUMERIC(18,4),
    ma50            NUMERIC(18,4),
    ma200           NUMERIC(18,4),
    ema9            NUMERIC(18,4),
    ema12           NUMERIC(18,4),
    ema26           NUMERIC(18,4),

    -- === MOMENTUM ===
    rsi14           NUMERIC(8,4),   -- 0 → 100
    macd            NUMERIC(14,6),
    macd_signal     NUMERIC(14,6),
    macd_hist       NUMERIC(14,6),
    stoch_k         NUMERIC(8,4),   -- Stochastic %K
    stoch_d         NUMERIC(8,4),   -- Stochastic %D

    -- === VOLATILITY ===
    bb_upper        NUMERIC(18,4),  -- Bollinger Band (20,2)
    bb_middle       NUMERIC(18,4),
    bb_lower        NUMERIC(18,4),
    bb_width        NUMERIC(12,6),  -- (upper-lower)/middle — đo mức độ nén
    atr14           NUMERIC(18,4),  -- Average True Range

    -- === VOLUME ===
    vol_ma20        NUMERIC(20,2),
    vol_ratio       NUMERIC(8,4),   -- vol / vol_ma20 — đột biến KL
    obv             NUMERIC(25,0),  -- On Balance Volume

    -- === FOREIGN FLOW (tính từ daily_stock_prices) ===
    net_foreign_vol_5d  BIGINT,     -- Net foreign vol 5 ngày gần nhất
    net_foreign_vol_10d BIGINT,
    net_foreign_val_5d  NUMERIC(25,2),
    net_foreign_val_10d NUMERIC(25,2),

    -- Metadata
    computed_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trading_date)
);

CREATE INDEX IF NOT EXISTS idx_ti_date ON technical_indicators (trading_date DESC);
CREATE INDEX IF NOT EXISTS idx_ti_symbol_date ON technical_indicators (symbol, trading_date DESC);
-- Hỗ trợ query screener: "Tìm tất cả mã RSI < 30 hôm nay"
CREATE INDEX IF NOT EXISTS idx_ti_rsi14 ON technical_indicators (trading_date DESC, rsi14);

-- ------------------------------------------------------------
-- 7. TRADING SIGNALS
--    Output của Analytics Layer — input của Execution Engine
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trading_signals (
    id              SERIAL        PRIMARY KEY,
    symbol          VARCHAR(20)   NOT NULL REFERENCES securities(symbol),
    signal_date     DATE          NOT NULL,
    signal_time     TIMESTAMP     NOT NULL,
    signal_type     VARCHAR(50)   NOT NULL,
    -- Ví dụ: 'MA_GOLDEN_CROSS', 'RSI_OVERSOLD', 'MACD_BULLISH',
    --        'BB_SQUEEZE_BREAKOUT', 'FOREIGN_ACCUMULATION'
    signal_direction VARCHAR(10),             -- 'BUY', 'SELL', 'NEUTRAL'
    strength        NUMERIC(5,4),             -- 0.0 → 1.0 (confidence score)
    source_type     VARCHAR(20),              -- 'EOD', 'INTRADAY', 'REALTIME'
    close_price     NUMERIC(18,2),            -- Giá lúc signal phát ra
    parameters      JSONB,                    -- {"fast_ma":5,"slow_ma":20,"rsi":28.5}
    is_active       BOOLEAN       DEFAULT TRUE,
    created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_date ON trading_signals (symbol, signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_date_type ON trading_signals (signal_date DESC, signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_active
    ON trading_signals (is_active, signal_date DESC)
    WHERE is_active = TRUE;

ADD CONSTRAINT uq_trading_signals_symbol_date_type UNIQUE (symbol, signal_date, signal_type);
----------------------------------------------------------------------------------------------
-- 8. INDEX TABLE
----------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS index_list (
    index_code VARCHAR(50) PRIMARY KEY,
    index_name VARCHAR(255),
    exchange VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS daily_index (
    index_code VARCHAR(50) NOT NULL,
    trading_date DATE NOT NULL,

    index_value NUMERIC(18,4),
    change NUMERIC(18,4),
    ratio_change NUMERIC(18,10),

    total_trade BIGINT,
    total_match_vol BIGINT,
    total_match_val NUMERIC(24,2),

    total_deal_vol BIGINT,
    total_deal_val NUMERIC(24,2),

    total_vol BIGINT,
    total_val NUMERIC(24,2),

    advances INTEGER,
    no_changes INTEGER,
    declines INTEGER,
    ceilings INTEGER,
    floors INTEGER,

    trading_session VARCHAR(10),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (index_code, trading_date), 
	
	ADD CONSTRAINT fk_daily_index_index_code FOREIGN KEY (index_code) REFERENCES index_list(index_code)
);

CREATE INDEX idx_daily_index_date ON daily_index(trading_date);

-- ------------------------------------------------------------
-- 8. SECTOR TABLE 
-- ------------------------------------------------------------
CREATE TABLE sector_mapping (
    symbol VARCHAR(20),
    sector_name VARCHAR(255)
);

UPDATE securities s
SET sector_name = sm.sector_name
FROM sector_mapping sm
WHERE s.symbol = sm.symbol;

