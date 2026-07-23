"""
src/kagi_chart.py
════════════════════════════════════════════════════════════════════════════
KagiChart — pure-Python port of the Kagi charting algorithm from the
JavaScript "d3-kagi" library (Arpit Narechania, MIT License,
https://github.com/arpitnarechania/d3-kagi).

Only the DATA-PROCESSING pipeline was ported:
    preprocess_data()             → KagiChart._preprocess()
    filter_same_x_points_from_data() → KagiChart._filter_same_x()
    add_base_shoulder_points()     → KagiChart._add_base_shoulder_points()
    generate_yang_ying_lines()     → KagiChart._generate_segments()

The original's d3.js/svg/jQuery rendering layer was intentionally dropped —
that's UI-specific to a browser DOM. Rendering in this project is done by
KagiService.get_plot() (matplotlib), mirroring src/pnf_service.py /
PNFService, which follows the exact same "pure algorithm + service
wrapper" split already used for the Point & Figure charts.

Kagi chart theory (for context)
────────────────────────────────
A Kagi chart ignores time and reacts only to price:
  • A vertical line extends in the current direction as price keeps moving
    that way.
  • A new column (direction reversal) is only drawn once price reverses by
    at least `reversal_value` — either an absolute price difference
    ("diff") or a percentage move ("pct") — measured from the last
    confirmed pivot, not from the previous single bar.
  • The line switches between "Yang" (thick, historically drawn as a rising
    stroke) and "Ying" (thin, falling stroke) whenever price crosses the
    most recent shoulder (prior local high) or base (prior local low).
    This crossing rule — not the vertical/horizontal direction alone — is
    what determines the thick/thin formatting.

Public API
──────────
    records = [{"date": "2024-01-02", "close": 24.5}, ...]   # ascending
    chart   = KagiChart(records, reversal_type="pct", reversal_value=4.0)
    segments = chart.build()      # list[KagiSegment]

    for seg in segments:
        seg.uptrend      # True = Yang, False = Ying
        seg.points       # list[KagiPoint] — x (column index), close, date
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


# ══════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════

@dataclass
class KagiPoint:
    """One vertex of the Kagi polyline."""
    x: int          # column index along the (non-time) Kagi x-axis
    close: float
    date: object    # passthrough — str / date / datetime, whatever was given


@dataclass
class KagiSegment:
    """One formatted Yang or Ying polyline segment, ready to plot."""
    points: list[KagiPoint]
    uptrend: bool   # True = Yang (rising leg), False = Ying (falling leg)


# ══════════════════════════════════════════════════════════════
# KagiChart — the ported algorithm
# ══════════════════════════════════════════════════════════════

class KagiChart:
    """
    Builds a Kagi chart from a plain close-price series.

    Parameters
    ----------
    data : Sequence[dict]
        Each item needs 'date' and 'close' keys, sorted ascending by date.
    reversal_type : 'pct' | 'diff'
        'pct'  → reversal_value is a % move off the last confirmed pivot.
        'diff' → reversal_value is an absolute price difference.
    reversal_value : float
        Threshold that triggers a direction reversal (a new column).
    """

    def __init__(
        self,
        data: Sequence[dict],
        reversal_type: str = "pct",
        reversal_value: float = 4.0,
    ):
        if reversal_type not in ("pct", "diff"):
            raise ValueError("reversal_type must be 'pct' or 'diff'")
        if not data:
            raise ValueError("data must be a non-empty sequence")
        if "close" not in data[0] or "date" not in data[0]:
            raise ValueError("each item in data must have 'date' and 'close' keys")

        self.data = list(data)
        self.reversal_type = reversal_type
        self.reversal_value = float(reversal_value)

    # ── 1. Trend detection + pivot extraction ───────────────────────────────
    def _preprocess(self) -> list[dict]:
        """
        Port of preprocess_data(): walks the closes and emits only the
        points where the Kagi line actually needs a new vertex (a new
        column, or a same-direction extension). Moves smaller than
        reversal_value are absorbed/ignored, exactly like the JS original.
        """
        data = self.data
        n = len(data)

        output_data: list[dict] = [
            {"x": 0, "close": data[0]["close"], "date": data[0]["date"]}
        ]

        # ── Find the first non-zero move to establish the initial trend ────
        trend: Optional[str] = None
        broke_at = 0
        for k in range(1, n):
            diff = data[k]["close"] - data[k - 1]["close"]
            if diff > 0:
                trend, broke_at = "+", k
                break
            elif diff < 0:
                trend, broke_at = "-", k
                break
            # diff == 0 → keep scanning

        if trend is None:
            # Perfectly flat series — nothing beyond the first point to plot.
            return output_data

        # Re-slice from the first real move onward
        # (mirrors JS `data = data.slice(broke_at - 1)`)
        data = data[broke_at - 1:]
        n = len(data)
        trends: list[Optional[str]] = [None] * n
        trends[0] = trend

        last_close = data[0]["close"]
        counter = 0
        j = 0

        for i in range(1, n):
            diff = data[i]["close"] - last_close

            if diff > 0:
                trend = "+"
            elif diff < 0:
                trend = "-"
            else:
                trend = trends[i - 1]
            trends[i] = trend

            if self.reversal_type == "diff":
                value_to_compare = diff
            else:
                value_to_compare = (diff / last_close * 100.0) if last_close else 0.0

            if abs(value_to_compare) >= self.reversal_value:
                # ── Move is reversal-sized ──────────────────────────────
                if trends[i] != trends[i - 1]:
                    # Direction actually flipped → step to a new column
                    counter += 1
                    output_data.append({"x": counter, "close": last_close, "date": data[i]["date"]})
                    output_data.append({"x": counter, "close": data[i]["close"], "date": data[i]["date"]})
                else:
                    # Same direction, big move → extend along the same column
                    if trends[i] == "+" and data[i]["close"] > data[i - 1]["close"]:
                        output_data.append({"x": counter, "close": data[i]["close"], "date": data[i]["date"]})
                    elif trends[i] == "-" and data[i]["close"] < data[i - 1]["close"]:
                        output_data.append({"x": counter, "close": data[i]["close"], "date": data[i]["date"]})
                last_close = data[i]["close"]
                j = 0
            else:
                # ── Sub-threshold move ───────────────────────────────────
                if trends[i] == trends[i - 1]:
                    if trends[i] == "+" and data[i]["close"] > data[i - 1]["close"]:
                        output_data.append({"x": counter, "close": data[i]["close"], "date": data[i]["date"]})
                    elif trends[i] == "-" and data[i]["close"] < data[i - 1]["close"]:
                        output_data.append({"x": counter, "close": data[i]["close"], "date": data[i]["date"]})
                    last_close = data[i]["close"]
                    j = 0
                else:
                    # Minor wiggle — ignore it, roll back to the trend that
                    # was in effect j+1 bars ago (matches the JS exactly).
                    ref = i - 1 - j
                    last_close = data[ref]["close"]
                    trends[i] = trends[ref]
                    j += 1

        return output_data

    # ── 2. Collapse duplicate-X runs to first/last only ─────────────────────
    @staticmethod
    def _filter_same_x(data: list[dict]) -> list[dict]:
        """
        Port of filter_same_x_points_from_data(): when several consecutive
        points share the same x (same column), keep only the first and last
        of that run — interior points don't change the rendered polyline.
        """
        if len(data) <= 1:
            return list(data)

        filtered = [data[0]]
        for i in range(1, len(data)):
            if data[i]["x"] == data[i - 1]["x"]:
                continue
            filtered.append(data[i - 1])
            filtered.append(data[i])
        filtered.append(data[-1])
        return filtered

    # ── 3. Insert shoulder/base pivot points ────────────────────────────────
    @staticmethod
    def _add_base_shoulder_points(data: list[dict]) -> list[dict]:
        """
        Port of add_base_shoulder_points(): tracks the running "shoulder"
        (most recent local high) and "base" (most recent local low) and
        inserts an explicit pivot point + `to_break=True` flag whenever
        price crosses the opposite pivot — this is the Yang/Ying flip rule
        of a real Kagi chart.
        """
        data = [dict(p) for p in data]  # shallow copy — don't mutate caller's list
        if len(data) < 2:
            return data

        if data[1]["close"] >= data[0]["close"]:
            base, shoulder, uptrend = data[0]["close"], data[1]["close"], True
        else:
            base, shoulder, uptrend = data[1]["close"], data[0]["close"], False

        points_to_add: list[dict] = []
        positions_to_add_to: list[int] = []

        for i in range(len(data)):
            if uptrend and data[i]["close"] < base:
                points_to_add.append(
                    {"date": data[i]["date"], "close": base, "x": data[i]["x"], "to_break": True}
                )
                positions_to_add_to.append(i)
                uptrend = not uptrend
            elif (not uptrend) and data[i]["close"] > shoulder:
                points_to_add.append(
                    {"date": data[i]["date"], "close": shoulder, "x": data[i]["x"], "to_break": True}
                )
                positions_to_add_to.append(i)
                uptrend = not uptrend

            if i > 0 and data[i]["close"] > data[i - 1]["close"]:
                base, shoulder = data[i - 1]["close"], data[i]["close"]
            elif i > 0 and data[i]["close"] < data[i - 1]["close"]:
                base, shoulder = data[i]["close"], data[i - 1]["close"]

        # Insert pivot points at their recorded positions. The "+k" offset
        # accounts for previously-inserted points shifting later indices
        # (mirrors the JS `data.splice(positions_to_add_to[k] + k, 0, ...)`).
        for k, pos in enumerate(positions_to_add_to):
            data.insert(pos + k, points_to_add[k])

        return data

    # ── 4. Group into Yang/Ying polyline segments ───────────────────────────
    @staticmethod
    def _generate_segments(data: list[dict]) -> list[KagiSegment]:
        """
        Port of generate_yang_ying_lines(): slices the flat point list into
        segments at every `to_break` pivot, alternating Yang (uptrend) /
        Ying (downtrend) on each slice. Consecutive segments intentionally
        share their boundary point so the plotted line stays continuous
        (same behaviour as the original splice-based JS logic).

        Note: the original JS omits the `uptrend` key on the *final* segment
        (a minor oversight in the upstream library). This port fixes that
        so every segment consistently carries its trend flag.
        """
        if len(data) < 2:
            pts = [KagiPoint(x=p["x"], close=p["close"], date=p["date"]) for p in data]
            return [KagiSegment(points=pts, uptrend=True)]

        uptrend = data[1]["close"] >= data[0]["close"]

        segments: list[KagiSegment] = []
        start = 0

        for i, p in enumerate(data):
            if p.get("to_break"):
                chunk = data[start:i + 1]
                pts = [KagiPoint(x=q["x"], close=q["close"], date=q["date"]) for q in chunk]
                segments.append(KagiSegment(points=pts, uptrend=uptrend))
                start = i
                uptrend = not uptrend

        tail = data[start:]
        pts = [KagiPoint(x=q["x"], close=q["close"], date=q["date"]) for q in tail]
        segments.append(KagiSegment(points=pts, uptrend=uptrend))

        return segments

    # ── Public entry point ───────────────────────────────────────────────────
    def build(self) -> list[KagiSegment]:
        """Run the full pipeline and return the Yang/Ying segments to plot."""
        pre = self._preprocess()
        filtered = self._filter_same_x(pre)
        formatted = self._add_base_shoulder_points(filtered)
        return self._generate_segments(formatted)
