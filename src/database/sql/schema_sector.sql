-- ============================================================
-- SECTOR ROTATION MONEY FLOW — DATABASE SCHEMA
-- ============================================================
-- Run order:
--   1. sector_master
--   2. stock_sector_mapping
--   3. stock_mf_daily
--   4. sector_factor_daily
--   5. sector_score_daily
--   6. sector_rank_weekly
-- ============================================================
-- ============================================================
-- 0. SECTOR REFERENCE TABLES
-- ============================================================

-- Canonical sector list
-- Populate once with your sector taxonomy (ICB / GICS / custom VN)
CREATE TABLE IF NOT EXISTS sector_master (
    sector_id   SERIAL          PRIMARY KEY,
    sector_name VARCHAR(255)    NOT NULL UNIQUE,    -- display name, e.g. 'Banking'
    sector_code VARCHAR(20)     UNIQUE,             -- short code, e.g. 'BNK'
    description TEXT,
    created_at  TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
);

-- Clean symbol → sector mapping
-- Replaces the thin sector_mapping (symbol, sector_name) table.
-- One symbol belongs to exactly one sector at any time.
CREATE TABLE IF NOT EXISTS stock_sector_mapping (
    symbol      VARCHAR(20)     NOT NULL
                                REFERENCES securities(symbol)
                                ON UPDATE CASCADE
                                ON DELETE CASCADE,
    sector_id   INTEGER         NOT NULL
                                REFERENCES sector_master(sector_id)
                                ON UPDATE CASCADE,
    source      VARCHAR(50)     DEFAULT 'manual',   -- 'manual','fiinpro','vietstock', etc.
    mapped_at   TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol)
);

-- After populating stock_sector_mapping, sync back to securities.sector_name
-- so existing queries that use securities.sector_name keep working:
--
  UPDATE securities s
  SET sector_name = sm2.sector_name
  FROM stock_sector_mapping ssm
  JOIN sector_master sm2 ON sm2.sector_id = ssm.sector_id
  WHERE s.symbol = ssm.symbol;


-- ============================================================
-- 1. STOCK_MF_DAILY  —  Stock-level money flow indicators
-- ============================================================
-- All indicators calculated per stock per day using
-- adj prices: adj_factor = close_price_adjusted / close_price
--   adj_high  = highest_price  * adj_factor
--   adj_low   = lowest_price   * adj_factor
--   adj_open  = open_price     * adj_factor
--   adj_close = close_price_adjusted
-- ============================================================

CREATE TABLE IF NOT EXISTS stock_mf_daily (
    date            DATE            NOT NULL,
    symbol          VARCHAR(20)     NOT NULL
                                    REFERENCES securities(symbol)
                                    ON UPDATE CASCADE,
    sector_name     VARCHAR(255),               -- denormalized for fast aggregation

    -- ── Core Indicators ────────────────────────────────────
    mfi             NUMERIC(8, 4),              -- Money Flow Index          [0, 100]
    cmf             NUMERIC(10, 6),             -- Chaikin Money Flow        [-1, +1]
    rvol            NUMERIC(10, 4),             -- Relative Volume           [0, ∞)
    nmf             NUMERIC(25, 2),             -- Net Money Flow            signed VND value
    nmf_zscore      NUMERIC(10, 4),             -- NMF Z-Score               abnormal flow
    nmf_accel       NUMERIC(10, 6),             -- NMF Acceleration          EMA5/EMA20
    nff_zscore      NUMERIC(10, 4),             -- Net Foreign Flow Z-Score  institutional signal

    -- ── Aggregation Weight ─────────────────────────────────
    -- Stored here to avoid re-joining daily_stock_prices during aggregation
    trading_value   NUMERIC(25, 2),             -- total_match_val (VND)

    computed_at     TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, symbol)
);

-- Indexes for aggregation queries (GROUP BY sector, date)
CREATE INDEX IF NOT EXISTS idx_smf_date
    ON stock_mf_daily (date DESC);

CREATE INDEX IF NOT EXISTS idx_smf_sector_date
    ON stock_mf_daily (sector_name, date DESC);

CREATE INDEX IF NOT EXISTS idx_smf_symbol_date
    ON stock_mf_daily (symbol, date DESC);


-- ============================================================
-- 2. SECTOR_FACTOR_DAILY  —  Aggregated sector-level features
-- ============================================================
-- Aggregated from stock_mf_daily using:
--   Weighted = liquidity-weighted mean  (institutional concentration)
--   Median   = cross-sectional median   (breadth participation)
-- ============================================================

CREATE TABLE IF NOT EXISTS sector_factor_daily (
    date                    DATE            NOT NULL,
    sector_name             VARCHAR(255)    NOT NULL,

    -- ── Weighted (Institutional) Metrics ───────────────────
    weighted_mfi            NUMERIC(8, 4),
    weighted_cmf            NUMERIC(10, 6),
    weighted_rvol           NUMERIC(10, 4),
    weighted_nmf_z          NUMERIC(10, 4),
    weighted_accel          NUMERIC(10, 6),
    weighted_nff_z          NUMERIC(10, 4),

    -- ── Median (Breadth) Metrics ───────────────────────────
    median_mfi              NUMERIC(8, 4),
    median_cmf              NUMERIC(10, 6),
    median_rvol             NUMERIC(10, 4),
    median_nmf_z            NUMERIC(10, 4),
    median_accel            NUMERIC(10, 6),
    median_nff_z            NUMERIC(10, 4),

    -- ── Breadth Participation Metrics ─────────────────────
    -- Fraction [0.0, 1.0] of stocks meeting each condition
    breadth_cmf_positive    NUMERIC(5, 4),  -- % with CMF > 0
    breadth_mfi_above_50    NUMERIC(5, 4),  -- % with MFI > 50
    breadth_accel_above_1   NUMERIC(5, 4),  -- % with nmf_accel > 1
    breadth_nff_positive    NUMERIC(5, 4),  -- % with nff_zscore > 0

    -- ── Coverage / Quality Gate ────────────────────────────
    -- Filter out thin sectors before scoring (e.g. n_stocks < 3)
    n_stocks                INTEGER,        -- stocks with valid data that day
    coverage_pct            NUMERIC(5, 4),  -- valid / total stocks in sector

    computed_at             TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, sector_name)
);

CREATE INDEX IF NOT EXISTS idx_sfd_date
    ON sector_factor_daily (date DESC);

CREATE INDEX IF NOT EXISTS idx_sfd_sector_date
    ON sector_factor_daily (sector_name, date DESC);


-- ============================================================
-- 3. SECTOR_SCORE_DAILY  —  Tactical sector ranking
-- ============================================================
-- Scoring formula:
--   inst_score    = 0.30×weighted_cmf + 0.20×weighted_mfi
--                 + 0.20×weighted_rvol + 0.15×weighted_nmf_z
--                 + 0.15×weighted_accel
--   breadth_score = 0.40×median_cmf   + 0.30×median_mfi
--                 + 0.30×breadth_cmf_positive
--   total_score   = 0.60×inst_score   + 0.40×breadth_score
-- ============================================================

CREATE TABLE IF NOT EXISTS sector_score_daily (
    date            DATE            NOT NULL,
    sector_name     VARCHAR(255)    NOT NULL,

    -- ── Composite Scores ───────────────────────────────────
    inst_score      NUMERIC(8, 4),              -- institutional concentration
    breadth_score   NUMERIC(8, 4),              -- breadth participation
    total_score     NUMERIC(8, 4),              -- final ranking score

    -- ── Ranking ────────────────────────────────────────────
    rank            INTEGER,                    -- 1 = strongest sector that day

    -- ── Regime ─────────────────────────────────────────────
    -- regime_score maps to: 5=Expansion, 4=EarlyRotation,
    --                        3=Neutral, 2=Distribution, 1=Contraction
    regime          VARCHAR(20),
    regime_score    NUMERIC(4, 2),

    -- ── Score Trend / Momentum ─────────────────────────────
    score_delta_1d  NUMERIC(8, 4),              -- total_score change vs D-1
    score_delta_5d  NUMERIC(8, 4),              -- total_score change vs D-5

    computed_at     TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, sector_name)
);

CREATE INDEX IF NOT EXISTS idx_ssd_date
    ON sector_score_daily (date DESC);

CREATE INDEX IF NOT EXISTS idx_ssd_sector_date
    ON sector_score_daily (sector_name, date DESC);

-- Useful for screener: "top sectors by score today"
CREATE INDEX IF NOT EXISTS idx_ssd_date_score
    ON sector_score_daily (date DESC, total_score DESC);

-- Useful for regime filter: "all Expansion sectors this week"
CREATE INDEX IF NOT EXISTS idx_ssd_date_regime
    ON sector_score_daily (date DESC, regime);


-- ============================================================
-- 4. SECTOR_RANK_WEEKLY  —  Structural weekly sector ranking
-- ============================================================
-- NOTE: Do NOT recalculate from weekly candles.
-- Aggregate daily scores: WeeklyScore = MEAN(DailyScores in week)
-- year_week format: YYYYWW integer  e.g. 202518
-- ============================================================

CREATE TABLE IF NOT EXISTS sector_rank_weekly (
    year_week       INTEGER         NOT NULL,   -- e.g. 202518
    date_from       DATE            NOT NULL,   -- Monday of ISO week
    date_to         DATE            NOT NULL,   -- Friday of ISO week
    sector_name     VARCHAR(255)    NOT NULL,

    -- ── Scores (mean of daily) ─────────────────────────────
    inst_score      NUMERIC(8, 4),
    breadth_score   NUMERIC(8, 4),
    total_score     NUMERIC(8, 4),

    -- ── Ranking ────────────────────────────────────────────
    rank            INTEGER,

    -- ── Regime ─────────────────────────────────────────────
    regime          VARCHAR(20),                -- modal regime of the week
    regime_score    NUMERIC(4, 2),

    -- ── Weekly Momentum ────────────────────────────────────
    score_delta_1w  NUMERIC(8, 4),              -- vs previous week

    -- ── Quality ────────────────────────────────────────────
    n_trading_days  INTEGER,                    -- actual days with data (≤5)

    computed_at     TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (year_week, sector_name)
);

CREATE INDEX IF NOT EXISTS idx_srw_year_week
    ON sector_rank_weekly (year_week DESC);

CREATE INDEX IF NOT EXISTS idx_srw_sector
    ON sector_rank_weekly (sector_name, year_week DESC);


-- ============================================================
-- MIGRATION: populate sector_master and stock_sector_mapping
--            from existing securities.sector_name data
-- Run after creating the tables above.
-- ============================================================

-- Step A: seed sector_master from whatever is already in securities
INSERT INTO sector_master (sector_name)
SELECT DISTINCT sector_name
FROM securities
WHERE sector_name IS NOT NULL
  AND sector_name <> ''
ON CONFLICT (sector_name) DO NOTHING;

-- Step B: populate stock_sector_mapping
INSERT INTO stock_sector_mapping (symbol, sector_id, source)
SELECT s.symbol, sm.sector_id, 'migration'
FROM securities s
JOIN sector_master sm ON sm.sector_name = s.sector_name
WHERE s.sector_name IS NOT NULL
ON CONFLICT (symbol) DO UPDATE
    SET sector_id = EXCLUDED.sector_id,
        mapped_at = CURRENT_TIMESTAMP;


-- ============================================================
 
INSERT INTO sector_master (sector_id, sector_name, sector_code, description)
VALUES
    ( 1, 'Ngân hàng',                      'BNK', 'Banking'),
    ( 2, 'Bất động sản',                   'REA', 'Real Estate'),
    ( 3, 'Xây dựng và Vật liệu',           'CON', 'Construction & Materials'),
    ( 4, 'Thực phẩm và đồ uống',           'F&B', 'Food & Beverage'),
    ( 5, 'Hàng & Dịch vụ Công nghiệp',    'IND', 'Industrial Goods & Services'),
    ( 6, 'Dịch vụ tài chính',              'FIN', 'Financial Services'),
    ( 7, 'Tài nguyên Cơ bản',              'BRS', 'Basic Resources'),
    ( 8, 'Hóa chất',                       'CHM', 'Chemicals'),
    ( 9, 'Điện, nước & xăng dầu khí đốt', 'UTL', 'Utilities'),
    (10, 'Dầu khí',                        'OIL', 'Oil & Gas'),
    (11, 'Công nghệ Thông tin',            'ICT', 'Information Technology'),
    (12, 'Bảo hiểm',                       'INS', 'Insurance'),
    (13, 'Hàng cá nhân & Gia dụng',        'CGS', 'Personal & Household Goods'),
    (14, 'Bán lẻ',                         'RET', 'Retail'),
    (15, 'Y tế',                           'HLT', 'Healthcare'),
    (16, 'Du lịch và Giải trí',            'TRV', 'Travel & Leisure'),
    (17, 'Truyền thông',                   'MED', 'Media & Telecommunications'),
    (18, 'Ô tô và phụ tùng',              'AUT', 'Automobiles & Parts')
ON CONFLICT (sector_name) DO UPDATE
    SET sector_code = EXCLUDED.sector_code,
        description = EXCLUDED.description;
 
-- Fix the serial sequence so future INSERTs don't collide with our manual IDs
SELECT setval('sector_master_sector_id_seq', (SELECT MAX(sector_id) FROM sector_master));
 
 
-- ============================================================
-- MIGRATION: populate stock_sector_mapping from securities
-- ============================================================
-- Run this AFTER seeding sector_master.
-- Assumes securities.sector_name already contains the VN sector strings above.
-- ============================================================
 
INSERT INTO stock_sector_mapping (symbol, sector_id, source)
SELECT
    s.symbol,
    sm.sector_id,
    'migration_v1'
FROM securities s
JOIN sector_master sm ON sm.sector_name = s.sector_name
WHERE s.sector_name IS NOT NULL
  AND s.sector_name <> ''
ON CONFLICT (symbol) DO UPDATE
    SET sector_id = EXCLUDED.sector_id,
        source    = EXCLUDED.source,
        mapped_at = CURRENT_TIMESTAMP;
 
-- Report: how many symbols mapped vs unmapped
SELECT
    'Mapped'   AS status, COUNT(*) AS n FROM stock_sector_mapping
UNION ALL
SELECT
    'Unmapped' AS status, COUNT(*) AS n
FROM securities
WHERE symbol NOT IN (SELECT symbol FROM stock_sector_mapping)
  AND length(symbol) = 3;   -- company stocks only
 
-- ============================================================
-- Sync back to securities.sector_name for backward compatibility
-- ============================================================
UPDATE securities s
SET sector_name = sm2.sector_name
FROM stock_sector_mapping ssm
JOIN sector_master sm2 ON sm2.sector_id = ssm.sector_id
WHERE s.symbol = ssm.symbol
  AND (s.sector_name IS DISTINCT FROM sm2.sector_name);
 
-- ============================================================







