"""Monthly token limit cycle math and enforcement helpers."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _anchor_day_for_month(anchor_day: int, year: int, month: int) -> int:
    return min(anchor_day, calendar.monthrange(year, month)[1])


def _cycle_start_on_or_before(anchor: date, ref: date) -> date:
    """Most recent cycle start date (inclusive) on or before ref."""
    anchor_day = anchor.day
    year, month = ref.year, ref.month
    day = _anchor_day_for_month(anchor_day, year, month)
    candidate = date(year, month, day)
    if candidate > ref:
        if month == 1:
            year, month = year - 1, 12
        else:
            year, month = year, month - 1
        day = _anchor_day_for_month(anchor_day, year, month)
        candidate = date(year, month, day)
    return candidate


def get_cycle_window(anchor_date: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """
    Return [cycle_start, cycle_end) UTC datetimes for the cycle containing `now`.

    Cycles begin on the anchor day each month (e.g. anchor Jan 15 → Jan 15–Feb 14).
    """
    anchor = _parse_date(anchor_date)
    ref = (now or datetime.now(timezone.utc)).date()
    start_date = _cycle_start_on_or_before(anchor, ref)

    if start_date.month == 12:
        next_year, next_month = start_date.year + 1, 1
    else:
        next_year, next_month = start_date.year, start_date.month + 1
    next_day = _anchor_day_for_month(anchor.day, next_year, next_month)
    end_date = date(next_year, next_month, next_day)

    cycle_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    cycle_end = datetime(end_date.year, end_date.month, next_day, tzinfo=timezone.utc)
    return cycle_start, cycle_end


def effective_limit(base_monthly_limit: int, cycle_bonus_tokens: int) -> int:
    return int(base_monthly_limit) + int(cycle_bonus_tokens)


def remaining_tokens(effective: int, used_total: int) -> int:
    return max(0, effective - used_total)
