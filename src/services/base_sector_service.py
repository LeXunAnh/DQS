"""
src/services/base_sector_service.py
════════════════════════════════════════════════════════════════════════════
BaseSectorService — Abstract base for date-range sector pipeline services.

Eliminates the duplicated run_maintenance / run_all / _normalize_db_date
logic that was copy-pasted identically across SectorAggregationService
and SectorScoringService.

Subclass contract
─────────────────
Every concrete service MUST implement:

    _get_latest_date() -> date | None
        Query the service's own output table for the most recent date
        already stored (no symbol filter — sector services are global).

    run_date(date_str) -> int
        Process all sectors for ONE calendar date.
        Returns number of sector-date rows written.

    run_range(from_date, to_date, **kwargs) -> int
        Process all sectors over a DATE RANGE.
        Each service owns its own batching / iteration strategy so this
        stays abstract — the base does NOT try to unify it because
        AggregationService iterates day-by-day while ScoringService
        uses multi-day batches fed into _run_pipeline.
        Returns total sector-date rows written.

Every concrete service MAY override:

    _label : str
        Human-readable name for log / progress-bar messages.
        Default: class name.

    FALLBACK_FROM_DATE : str
        Earliest from_date used when the output table is empty.
        Default: "2021-01-01".

Shared orchestration provided by this base
──────────────────────────────────────────
    _normalize_db_date(raw) -> date | None
        Safely coerce whatever the DB driver returns (str, datetime,
        date, or None) into a plain datetime.date — the single source
        of truth for this conversion, previously duplicated in both
        services with identical code and a DEBUG log comment.

    run_maintenance() -> int
        1. Query _get_latest_date()
        2. Normalize via _normalize_db_date()
        3. Skip if already up-to-date
        4. Compute from_date / to_date strings
        5. Delegate to run_range()

    run_all(from_date) -> int
        Compute to_date = today, log, delegate to run_range().
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date as date_type, datetime, timedelta
from typing import Optional

from src.database.handler import DatabaseHandler

logger = logging.getLogger(__name__)


class BaseSectorService(ABC):
    """
    Abstract base class for services that process sector data over date ranges.

    Concrete subclasses implement _get_latest_date(), run_date(), and
    run_range(); this class provides _normalize_db_date(), run_maintenance(),
    and run_all() for free.
    """

    # Override in subclass to customise log messages
    _label: str = ""

    # Override if history starts from a different date
    FALLBACK_FROM_DATE: str = "2021-01-01"

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    # ── Subclass identity ─────────────────────────────────────────────────────

    @property
    def label(self) -> str:
        """Display name used in log messages."""
        return self._label or type(self).__name__

    # ── Abstract interface ────────────────────────────────────────────────────
    @abstractmethod
    def _get_latest_date(self) -> Optional[date_type]:
        """
        Return the most recent date stored in this service's output table,
        or None if the table is empty.

        Example:
            SELECT MAX(date) FROM sector_factor_daily
        """

    @abstractmethod
    def run_date(self, date_str: str) -> int:
        """
        Process all sectors for ONE calendar date.

        Args:
            date_str : 'YYYY-MM-DD'

        Returns:
            Number of sector-date rows written.
        """

    @abstractmethod
    def run_range(self, from_date: str, to_date: str, **kwargs) -> int:
        """
        Process all sectors over a DATE RANGE.

        Each subclass owns its own batching strategy so this stays abstract.

        Args:
            from_date : 'YYYY-MM-DD' inclusive lower bound
            to_date   : 'YYYY-MM-DD' inclusive upper bound
            **kwargs  : subclass-specific options (e.g. batch_days)

        Returns:
            Total sector-date rows written.
        """

    # ── Shared utility ────────────────────────────────────────────────────────
    @staticmethod
    def _normalize_db_date(raw) -> Optional[date_type]:
        """
        Coerce the raw value returned by DB scalar() into datetime.date.

        DB drivers may return:
            str        "2025-05-28"  or  "2025-05-28T00:00:00"
            datetime   datetime(2025, 5, 28, 0, 0)
            date       date(2025, 5, 28)        ← already correct
            None       (table is empty)

        Returns datetime.date or None.
        Previously this block was copy-pasted verbatim in both
        SectorAggregationService and SectorScoringService.
        """
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date_type):
            return raw
        if isinstance(raw, str):
            return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date()
        # Fallback: try casting via str (handles numpy.datetime64, etc.)
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            logger.warning(f"⚠️  _normalize_db_date: unrecognised type {type(raw)}: {raw!r}")
            return None

    # ── Shared orchestration ──────────────────────────────────────────────────
    def run_maintenance(self) -> int:
        """
        Maintenance mode — only process dates not yet in the output table.
        Safe to run daily as a cron job.

        Steps:
          1. Query _get_latest_date() → normalize via _normalize_db_date()
          2. Skip if already up-to-date (last >= today)
          3. Compute from_date = last + 1 day  (or FALLBACK_FROM_DATE)
          4. Delegate to run_range(from_date, to_date)

        Returns:
            Total sector-date rows written (0 if already up-to-date).
        """
        raw   = self._get_latest_date()
        today = datetime.now().date()
        last  = self._normalize_db_date(raw)

        logger.info(
            f"🔍 {self.label} bảo trì: "
            f"ngày cuối trong DB = {last} | hôm nay = {today}"
        )

        if last and last >= today:
            logger.info(f"✅ {self.label}: đã cập nhật đến hôm nay, bỏ qua.")
            return 0

        from_date = (
            (last + timedelta(days=1)).strftime("%Y-%m-%d")
            if last else self.FALLBACK_FROM_DATE
        )
        to_date = today.strftime("%Y-%m-%d")

        logger.info(f"🔄 {self.label} bảo trì: {from_date} → {to_date}")
        return self.run_range(from_date, to_date)

    def run_all(self, from_date: str = "2021-01-01") -> int:
        """
        Full rebuild from from_date to today.
        Use for initial population or after upstream data is re-run.

        Args:
            from_date : 'YYYY-MM-DD' start of rebuild window.

        Returns:
            Total sector-date rows written.
        """
        to_date = datetime.now().date().strftime("%Y-%m-%d")
        logger.info(f"🚀 {self.label} full rebuild: {from_date} → {to_date}")
        return self.run_range(from_date, to_date)