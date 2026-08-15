"""TimeResolver — relative expressions → concrete start/end dates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from analytics.intent import is_awareness, is_metadata, normalize_intent
from analytics.models import ResolvedTimeframe, ResolverContext


def _parse_utc(current_utc: str) -> datetime:
    raw = (current_utc or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _local_now(ctx: ResolverContext) -> datetime:
    utc_now = _parse_utc(ctx.current_utc)
    tz_name = ctx.entity.timezone if ctx.entity else "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return utc_now.astimezone(tz)


def _next_week_bounds(local_now: datetime) -> Tuple[str, str]:
    """Next calendar week Monday–Sunday in the entity timezone (date-only ISO)."""
    # weekday: Mon=0 ... Sun=6
    days_until_next_monday = (7 - local_now.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    start = (local_now + timedelta(days=days_until_next_monday)).date()
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _this_week_bounds(local_now: datetime) -> Tuple[str, str]:
    start = (local_now - timedelta(days=local_now.weekday())).date()
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _last_week_bounds(local_now: datetime) -> Tuple[str, str]:
    this_monday = (local_now - timedelta(days=local_now.weekday())).date()
    start = this_monday - timedelta(days=7)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def _next_n_days(local_now: datetime, n: int) -> Tuple[str, str]:
    start = local_now.date()
    end = start + timedelta(days=max(n - 1, 0))
    return start.isoformat(), end.isoformat()


def _last_n_days(local_now: datetime, n: int) -> Tuple[str, str]:
    """Trailing window ending today, mirroring _next_n_days."""
    end = local_now.date()
    start = end - timedelta(days=max(n - 1, 0))
    return start.isoformat(), end.isoformat()


def _month_bounds(year: int, month: int) -> Tuple[str, str]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


_FIXED_WORDS = {
    "next_seven_days": ("next", 7, "days"),
    "next_2_weeks": ("next", 14, "days"),
    "last_seven_days": ("last", 7, "days"),
    "last_2_weeks": ("last", 14, "days"),
}

_PAST_PREFIXES = ("last", "past", "previous", "prior", "trailing")

_WEEKDAY_NAMES = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_WEEKDAY_MODIFIERS = {
    "this",
    "next",
    "last",
    "past",
    "previous",
    "prior",
    "upcoming",
    "coming",
}


from analytics.session_context import normalize_timeframe_expression


def _weekday_in_current_week(local_now: datetime, target_weekday: int) -> date:
    """Monday-start week containing local_now."""
    week_start = (local_now - timedelta(days=local_now.weekday())).date()
    return week_start + timedelta(days=target_weekday)


def _next_named_weekday(local_now: datetime, target_weekday: int) -> date:
    today = local_now.date()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _last_named_weekday(local_now: datetime, target_weekday: int) -> date:
    today = local_now.date()
    days_back = (today.weekday() - target_weekday) % 7
    if days_back == 0:
        days_back = 7
    return today - timedelta(days=days_back)


def _named_weekday_bounds(
    local_now: datetime,
    modifier: str,
    target_weekday: int,
) -> Tuple[str, str]:
    mod = modifier.lower()
    if mod in ("next", "upcoming", "coming"):
        day = _next_named_weekday(local_now, target_weekday)
    elif mod in ("last", "past", "previous", "prior"):
        day = _last_named_weekday(local_now, target_weekday)
    else:
        day = _weekday_in_current_week(local_now, target_weekday)
    iso = day.isoformat()
    return iso, iso


def _parse_weekday_expression(expr: str) -> Optional[Tuple[str, int]]:
    """Parse this_wednesday / next_mon / wednesday style expressions."""
    parts = [p for p in expr.split("_") if p]
    if not parts:
        return None

    if len(parts) == 1:
        weekday = _WEEKDAY_NAMES.get(parts[0])
        if weekday is not None:
            return ("this", weekday)
        return None

    if parts[0] in _WEEKDAY_MODIFIERS:
        weekday = _WEEKDAY_NAMES.get(parts[1])
        if weekday is not None:
            modifier = "next" if parts[0] in ("upcoming", "coming") else parts[0]
            if modifier in ("previous", "prior", "past"):
                modifier = "last"
            return (modifier, weekday)

    return None


def _resolve_relative(expression: str, local_now: datetime) -> Optional[Tuple[str, str]]:
    expr = normalize_timeframe_expression(expression or "")
    expr = (expr or "").strip().lower().replace("-", "_").replace(" ", "_")
    expr = expr.strip("_")
    if not expr:
        return None

    today = local_now.date()

    if expr in ("today", "current_day"):
        return today.isoformat(), today.isoformat()
    if expr == "tomorrow":
        d = (today + timedelta(days=1)).isoformat()
        return d, d
    if expr == "yesterday":
        d = (today - timedelta(days=1)).isoformat()
        return d, d
    if expr in ("next_week", "nextweek", "following_week"):
        return _next_week_bounds(local_now)
    if expr in ("this_week", "thisweek", "current_week", "week_to_date"):
        return _this_week_bounds(local_now)
    if expr in ("last_week", "lastweek", "previous_week", "past_week", "prior_week"):
        return _last_week_bounds(local_now)
    if expr in ("this_month", "current_month", "month_to_date", "mtd"):
        return _month_bounds(today.year, today.month)
    if expr in ("last_month", "previous_month", "past_month", "prior_month"):
        year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        return _month_bounds(year, month)
    if expr in ("this_year", "current_year", "year_to_date", "ytd"):
        return date(today.year, 1, 1).isoformat(), today.isoformat()
    if expr in ("last_year", "previous_year", "past_year", "prior_year"):
        return date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()

    weekday_expr = _parse_weekday_expression(expr)
    if weekday_expr is not None:
        modifier, weekday = weekday_expr
        return _named_weekday_bounds(local_now, modifier, weekday)

    direction, count, unit = _parse_counted(_FIXED_WORDS.get(expr) or expr)
    if direction is None:
        return None
    days = {"day": 1, "days": 1, "week": 7, "weeks": 7}.get(unit)
    if days is not None:
        n = count * days
        return _next_n_days(local_now, n) if direction == "next" else _last_n_days(local_now, n)
    if unit in ("month", "months"):
        n = count * 30
        return _next_n_days(local_now, n) if direction == "next" else _last_n_days(local_now, n)
    return None


def _parse_counted(expr) -> Tuple[Optional[str], int, str]:
    """Parse `next_7_days` / `last_30_days` / `past_3_weeks` style expressions."""
    if isinstance(expr, tuple):
        prefix, count, unit = expr
        return ("next" if prefix == "next" else "last"), count, unit
    parts = str(expr).split("_")
    if len(parts) != 3:
        return None, 0, ""
    prefix, count_raw, unit = parts
    if not count_raw.isdigit():
        return None, 0, ""
    if prefix == "next":
        return "next", int(count_raw), unit
    if prefix in _PAST_PREFIXES:
        return "last", int(count_raw), unit
    return None, 0, ""


def _is_historical(ctx: ResolverContext) -> bool:
    return normalize_intent(ctx.aep.query.intent) in ("historical", "history")


def _reject_future_history(ctx: ResolverContext, start: str, local_now: datetime) -> bool:
    """Historical analysis cannot start in the future — ask instead of querying nothing."""
    if not _is_historical(ctx) or not start:
        return False
    try:
        start_date = date.fromisoformat(start[:10])
    except ValueError:
        return False
    if start_date <= local_now.date():
        return False
    ctx.errors.append(
        f"Historical analysis needs a past period, but {start} is in the future. "
        "Did you mean a forecast, or a different date range?"
    )
    ctx.unresolved.add("timeframe")
    return True


def resolve(ctx: ResolverContext) -> ResolverContext:
    tf = ctx.aep.query.timeframe
    mode = (tf.mode or "explicit").lower()
    local_now = _local_now(ctx)
    intent = normalize_intent(ctx.aep.query.intent)

    if is_awareness(intent) or is_metadata(intent) or mode == "none":
        ctx.timeframe = ResolvedTimeframe(start="", end="", mode="none")
        return ctx

    if mode == "dimension":
        # Forecast evolution / comparison across time — keep target if present
        if tf.target:
            ctx.timeframe = ResolvedTimeframe(
                start=tf.target,
                end=tf.target,
                mode="dimension",
                expression=tf.expression,
                target=tf.target,
            )
            return ctx
        if tf.start and tf.end:
            ctx.timeframe = ResolvedTimeframe(
                start=str(tf.start)[:10],
                end=str(tf.end)[:10],
                mode="dimension",
                expression=tf.expression,
                target=tf.target,
            )
            return ctx
        ctx.errors.append(
            "For a time-series comparison across dates, "
            "what target date or start/end range should I use?"
        )
        ctx.unresolved.add("timeframe")
        return ctx

    if mode == "relative" or (tf.expression and not (tf.start and tf.end)):
        expr = tf.expression
        bounds = _resolve_relative(str(expr or ""), local_now)
        if not bounds:
            ctx.errors.append(
                f"I couldn't resolve the relative timeframe '{expr}'. "
                "Could you give explicit dates, or say something like next week / next 7 days?"
            )
            ctx.unresolved.add("timeframe")
            return ctx
        start, end = bounds
        if _reject_future_history(ctx, start, local_now):
            return ctx
        ctx.timeframe = ResolvedTimeframe(
            start=start,
            end=end,
            mode="relative",
            expression=str(expr),
        )
        return ctx

    if tf.start and tf.end:
        start, end = str(tf.start)[:10], str(tf.end)[:10]
        if start > end:
            ctx.errors.append(
                f"The range you gave starts after it ends ({start} → {end}). "
                "Which start and end dates did you mean?"
            )
            ctx.unresolved.add("timeframe")
            return ctx
        if _reject_future_history(ctx, start, local_now):
            return ctx
        ctx.timeframe = ResolvedTimeframe(
            start=start,
            end=end,
            mode="explicit",
            expression=tf.expression,
            target=tf.target,
        )
        return ctx

    if tf.start or tf.end:
        given = "start date" if tf.start else "end date"
        missing = "end date" if tf.start else "start date"
        ctx.errors.append(
            f"You gave a {given} ({tf.start or tf.end}) but no {missing}. "
            f"What {missing} should I use?"
        )
        ctx.unresolved.add("timeframe")
        return ctx

    # Default for forecast: next 7 days when unspecified but intent is forecast
    if intent in ("forecast", "probability", "comparison"):
        start, end = _next_n_days(local_now, 7)
        ctx.timeframe = ResolvedTimeframe(
            start=start,
            end=end,
            mode="relative",
            expression="next_7_days",
        )
        return ctx

    if _is_historical(ctx):
        ctx.errors.append(
            "For historical analysis I need a clear time range — "
            "for example last week, or specific start and end dates."
        )
        ctx.unresolved.add("timeframe")
        return ctx

    ctx.errors.append(
        "What time period should we cover? "
        "You can say next week, next 7 days, or give explicit dates."
    )
    ctx.unresolved.add("timeframe")
    return ctx
