"""TimeResolver — relative expressions → concrete start/end dates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

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


def _next_n_days(local_now: datetime, n: int) -> Tuple[str, str]:
    start = local_now.date()
    end = start + timedelta(days=max(n - 1, 0))
    return start.isoformat(), end.isoformat()


def _resolve_relative(expression: str, local_now: datetime) -> Optional[Tuple[str, str]]:
    expr = (expression or "").strip().lower().replace("-", "_").replace(" ", "_")
    if expr in ("next_week", "nextweek"):
        return _next_week_bounds(local_now)
    if expr in ("this_week", "thisweek"):
        return _this_week_bounds(local_now)
    if expr in ("tomorrow",):
        d = (local_now + timedelta(days=1)).date().isoformat()
        return d, d
    if expr in ("today",):
        d = local_now.date().isoformat()
        return d, d
    for prefix, n in (
        ("next_7_days", 7),
        ("next_seven_days", 7),
        ("next_5_days", 5),
        ("next_3_days", 3),
        ("next_14_days", 14),
        ("next_2_weeks", 14),
    ):
        if expr == prefix:
            return _next_n_days(local_now, n)
    # next_N_days pattern
    if expr.startswith("next_") and expr.endswith("_days"):
        mid = expr[len("next_") : -len("_days")]
        if mid.isdigit():
            return _next_n_days(local_now, int(mid))
    return None


def resolve(ctx: ResolverContext) -> ResolverContext:
    tf = ctx.aep.query.timeframe
    mode = (tf.mode or "explicit").lower()
    local_now = _local_now(ctx)

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
        ctx.errors.append("Time dimension requires a target or explicit start/end.")
        return ctx

    if mode == "relative" or (tf.expression and not (tf.start and tf.end)):
        expr = tf.expression
        bounds = _resolve_relative(str(expr or ""), local_now)
        if not bounds:
            ctx.errors.append(
                f"Relative timeframe '{expr}' could not be resolved. "
                "Please provide explicit dates or a supported expression (e.g. next_week)."
            )
            return ctx
        start, end = bounds
        ctx.timeframe = ResolvedTimeframe(
            start=start,
            end=end,
            mode="relative",
            expression=str(expr),
        )
        return ctx

    if tf.start and tf.end:
        ctx.timeframe = ResolvedTimeframe(
            start=str(tf.start)[:10],
            end=str(tf.end)[:10],
            mode="explicit",
            expression=tf.expression,
            target=tf.target,
        )
        return ctx

    # Default for forecast: next 7 days when unspecified but intent is forecast
    intent = (ctx.aep.query.intent or "").lower()
    if intent in ("forecast", "probability", "comparison"):
        start, end = _next_n_days(local_now, 7)
        ctx.timeframe = ResolvedTimeframe(
            start=start,
            end=end,
            mode="relative",
            expression="next_7_days",
        )
        return ctx

    if intent in ("historical",):
        ctx.errors.append("Historical queries require an explicit timeframe.")
        return ctx

    if intent in ("metadata", "awareness", "metadata_lookup"):
        ctx.timeframe = ResolvedTimeframe(start="", end="", mode="none")
        return ctx

    ctx.errors.append("Timeframe could not be resolved.")
    return ctx
