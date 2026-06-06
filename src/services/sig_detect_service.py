import json
import logging
from datetime import datetime
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class SignalDetector:
    """
    Nhận DataFrame indicators của 1 mã (đã sort ASC theo trading_date)
    và trả về list[dict] — mỗi dict là 1 signal row sẵn sàng INSERT.
    """

    # ── Cấu hình ngưỡng mặc định ────────────────────────────────
    RSI_OVERSOLD      = 30.0
    RSI_OVERBOUGHT    = 70.0
    BB_SQUEEZE_THR    = 0.05
    VOLUME_SPIKE_THR  = 2.5
    FOREIGN_VOL_THR   = 500_000

    @classmethod
    def _strength_clip(cls, val: float) -> float:
        return float(np.clip(round(val, 4), 0.0, 1.0))

    # ── MA Crossover ────────────────────────────────────────────
    @classmethod
    def detect_ma_cross(cls, df: pd.DataFrame, symbol: str, fast_col: str = "ma5", slow_col: str = "ma20") -> list[dict]:
        signals = []
        prev_fast = df[fast_col].shift(1)
        prev_slow = df[slow_col].shift(1)

        for i, row in df.iterrows():
            f, s = row[fast_col], row[slow_col]
            pf, ps = prev_fast.iloc[i] if i > 0 else np.nan, prev_slow.iloc[i] if i > 0 else np.nan

            if any(pd.isna([f, s, pf, ps])):
                continue

            gap_pct = abs(f - s) / max(abs(s), 1e-9)
            strength = cls._strength_clip(min(gap_pct * 20, 1.0))

            if pf <= ps and f > s:
                signals.append(cls._build(
                    symbol=symbol, date=row["trading_date"],
                    signal_type="MA_GOLDEN_CROSS", direction="BUY",
                    strength=strength, close_price=row["close_price"],
                    params={
                        "fast_ma": fast_col.upper(), "slow_ma": slow_col.upper(),
                        fast_col: round(float(f), 4), slow_col: round(float(s), 4),
                        "gap_pct": round(gap_pct * 100, 3),
                    },
                ))
            elif pf >= ps and f < s:
                signals.append(cls._build(
                    symbol=symbol, date=row["trading_date"],
                    signal_type="MA_DEATH_CROSS", direction="SELL",
                    strength=strength, close_price=row["close_price"],
                    params={
                        "fast_ma": fast_col.upper(), "slow_ma": slow_col.upper(),
                        fast_col: round(float(f), 4), slow_col: round(float(s), 4),
                        "gap_pct": round(gap_pct * 100, 3),
                    },
                ))
        return signals

    # ── RSI ─────────────────────────────────────────────────────
    @classmethod
    def detect_rsi(cls, df: pd.DataFrame, symbol: str) -> list[dict]:
        signals = []
        prev_rsi = df["rsi14"].shift(1)

        for i, row in df.iterrows():
            r, pr = row["rsi14"], prev_rsi.iloc[i] if i > 0 else np.nan
            if pd.isna(r) or pd.isna(pr):
                continue

            if pr < cls.RSI_OVERSOLD <= r:
                strength = cls._strength_clip((cls.RSI_OVERSOLD - min(pr, cls.RSI_OVERSOLD)) / cls.RSI_OVERSOLD)
                signals.append(cls._build(
                    symbol, row["trading_date"], "RSI_OVERSOLD", "BUY",
                    strength, row["close_price"],
                    {"rsi14": round(float(r), 4), "prev_rsi": round(float(pr), 4),
                     "threshold": cls.RSI_OVERSOLD},
                ))
            elif pr > cls.RSI_OVERBOUGHT >= r:
                strength = cls._strength_clip((min(pr, 100) - cls.RSI_OVERBOUGHT) / (100 - cls.RSI_OVERBOUGHT))
                signals.append(cls._build(
                    symbol, row["trading_date"], "RSI_OVERBOUGHT", "SELL",
                    strength, row["close_price"],
                    {"rsi14": round(float(r), 4), "prev_rsi": round(float(pr), 4),
                     "threshold": cls.RSI_OVERBOUGHT},
                ))
        return signals

    # ── MACD ────────────────────────────────────────────────────
    @classmethod
    def detect_macd(cls, df: pd.DataFrame, symbol: str) -> list[dict]:
        signals = []
        hist = df["macd_hist"]
        prev_hist = hist.shift(1)
        hist_std = hist.rolling(60, min_periods=10).std()

        for i, row in df.iterrows():
            h, ph = row["macd_hist"], prev_hist.iloc[i] if i > 0 else np.nan
            if pd.isna(h) or pd.isna(ph):
                continue

            std = hist_std.iloc[i] if not pd.isna(hist_std.iloc[i]) else 1.0
            strength = cls._strength_clip(abs(h) / max(std * 3, 1e-9))

            if ph < 0 <= h:
                signals.append(cls._build(
                    symbol, row["trading_date"], "MACD_BULLISH_CROSS", "BUY",
                    strength, row["close_price"],
                    {
                        "macd": round(float(row["macd"]), 6),
                        "macd_signal": round(float(row["macd_signal"]), 6),
                        "macd_hist": round(float(h), 6),
                    },
                ))
            elif ph > 0 >= h:
                signals.append(cls._build(
                    symbol, row["trading_date"], "MACD_BEARISH_CROSS", "SELL",
                    strength, row["close_price"],
                    {
                        "macd": round(float(row["macd"]), 6),
                        "macd_signal": round(float(row["macd_signal"]), 6),
                        "macd_hist": round(float(h), 6),
                    },
                ))
        return signals

    # ── Bollinger Squeeze Breakout ───────────────────────────────
    @classmethod
    def detect_bb_breakout(cls, df: pd.DataFrame, symbol: str) -> list[dict]:
        signals = []
        squeeze_streak = 0

        for i, row in df.iterrows():
            bw = row.get("bb_width")
            if pd.isna(bw):
                continue

            if bw < cls.BB_SQUEEZE_THR:
                squeeze_streak += 1
                continue

            if squeeze_streak >= 3:
                close = row["close_price"]
                upper = row["bb_upper"]
                lower = row["bb_lower"]
                atr = row.get("atr14") or 1.0

                if not pd.isna(upper) and close > upper:
                    strength = cls._strength_clip((close - upper) / max(atr, 1e-9) / 3)
                    signals.append(cls._build(
                        symbol, row["trading_date"],
                        "BB_SQUEEZE_BREAKOUT_UP", "BUY", strength, close,
                        {
                            "bb_upper": round(float(upper), 4),
                            "bb_lower": round(float(lower), 4),
                            "bb_width": round(float(bw), 6),
                            "squeeze_days": squeeze_streak,
                            "atr14": round(float(atr), 4),
                        },
                    ))
                elif not pd.isna(lower) and close < lower:
                    strength = cls._strength_clip((lower - close) / max(atr, 1e-9) / 3)
                    signals.append(cls._build(
                        symbol, row["trading_date"],
                        "BB_SQUEEZE_BREAKOUT_DOWN", "SELL", strength, close,
                        {
                            "bb_upper": round(float(upper), 4),
                            "bb_lower": round(float(lower), 4),
                            "bb_width": round(float(bw), 6),
                            "squeeze_days": squeeze_streak,
                            "atr14": round(float(atr), 4),
                        },
                    ))

            squeeze_streak = 0
        return signals

    # ── Volume Spike ────────────────────────────────────────────
    @classmethod
    def detect_volume_spike(cls, df: pd.DataFrame, symbol: str) -> list[dict]:
        signals = []
        for _, row in df.iterrows():
            vr = row.get("vol_ratio")
            if pd.isna(vr) or vr < cls.VOLUME_SPIKE_THR:
                continue

            direction = "BUY" if row["close_price"] >= row.get("open_price", row["close_price"]) else "SELL"
            strength = cls._strength_clip((vr - cls.VOLUME_SPIKE_THR) / cls.VOLUME_SPIKE_THR)
            signals.append(cls._build(
                symbol, row["trading_date"], "VOLUME_SPIKE",
                direction, strength, row["close_price"],
                {
                    "vol_ratio": round(float(vr), 4),
                    "vol_ma20": round(float(row.get("vol_ma20") or 0), 0),
                    "threshold": cls.VOLUME_SPIKE_THR,
                },
            ))
        return signals

    # ── Foreign Flow ────────────────────────────────────────────
    @classmethod
    def detect_foreign_flow(cls, df: pd.DataFrame, symbol: str) -> list[dict]:
        signals = []
        prev_dir = 0

        for _, row in df.iterrows():
            nv = row.get("net_foreign_vol_5d")
            if pd.isna(nv):
                continue

            nv = float(nv)
            cur_dir = 0
            if nv > cls.FOREIGN_VOL_THR:
                cur_dir = 1
            elif nv < -cls.FOREIGN_VOL_THR:
                cur_dir = -1

            if cur_dir != 0 and cur_dir != prev_dir:
                strength = cls._strength_clip(abs(nv) / (cls.FOREIGN_VOL_THR * 10))
                signals.append(cls._build(
                    symbol, row["trading_date"],
                    "FOREIGN_ACCUMULATION" if cur_dir == 1 else "FOREIGN_DISTRIBUTION",
                    "BUY" if cur_dir == 1 else "SELL",
                    strength, row["close_price"],
                    {
                        "net_foreign_vol_5d": int(nv),
                        "net_foreign_val_5d": round(float(row.get("net_foreign_val_5d") or 0), 2),
                        "threshold": cls.FOREIGN_VOL_THR,
                    },
                ))
            prev_dir = cur_dir
        return signals

    # ── Builder ─────────────────────────────────────────────────
    @staticmethod
    def _build(symbol: str, date, signal_type: str, direction: str, strength: float, close_price: float, params: dict) -> dict:
        ts = datetime.combine(date, datetime.min.time()) if hasattr(date, "year") else datetime.fromisoformat(str(date))
        return {
            "symbol": symbol,
            "signal_date": date,
            "signal_time": ts,
            "signal_type": signal_type,
            "signal_direction": direction,
            "strength": float(round(strength, 4)),
            "source_type": "EOD",
            "close_price": float(round(close_price, 2)),
            "parameters": json.dumps(params, ensure_ascii=False),
            "is_active": True,
        }

    # ── Run all detectors ────────────────────────────────────────
    @classmethod
    def run(cls, df: pd.DataFrame, symbol: str, ma_pairs: list[tuple[str, str]] | None = None, enable: set[str] | None = None) -> list[dict]:
        if ma_pairs is None:
            ma_pairs = [("ma5", "ma20"), ("ma10", "ma50")]

        all_signals: list[dict] = []

        def _active(name: str) -> bool:
            return enable is None or name in enable

        for fast, slow in ma_pairs:
            if _active("MA_GOLDEN_CROSS") or _active("MA_DEATH_CROSS"):
                all_signals.extend(cls.detect_ma_cross(df, symbol, fast, slow))

        if _active("RSI_OVERSOLD") or _active("RSI_OVERBOUGHT"):
            all_signals.extend(cls.detect_rsi(df, symbol))

        if _active("MACD_BULLISH_CROSS") or _active("MACD_BEARISH_CROSS"):
            all_signals.extend(cls.detect_macd(df, symbol))

        if _active("BB_SQUEEZE_BREAKOUT_UP") or _active("BB_SQUEEZE_BREAKOUT_DOWN"):
            all_signals.extend(cls.detect_bb_breakout(df, symbol))

        if _active("VOLUME_SPIKE"):
            all_signals.extend(cls.detect_volume_spike(df, symbol))

        if _active("FOREIGN_ACCUMULATION") or _active("FOREIGN_DISTRIBUTION"):
            all_signals.extend(cls.detect_foreign_flow(df, symbol))

        all_signals.sort(key=lambda s: str(s["signal_date"]))
        return all_signals