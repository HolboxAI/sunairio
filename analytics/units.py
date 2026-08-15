"""Unit preference, normalization, and threshold conversion for confirm copy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from analytics.models import ResolverContext

_TEMP_NATIVE = frozenset({"°c", "ºc", "c", "celsius"})
_TEMP_F = frozenset({"°f", "ºf", "f", "fahrenheit", "degf", "deg f"})
_TEMP_C = frozenset({"°c", "ºc", "c", "celsius", "degc", "deg c"})


@dataclass(frozen=True)
class UnitConversion:
    from_unit: str
    to_unit: str
    method: str = "linear"

    def to_dict(self) -> Dict[str, str]:
        return {"from": self.from_unit, "to": self.to_unit, "method": self.method}


@dataclass(frozen=True)
class ThresholdContext:
    """Threshold as the user stated it vs how it compares to stored data."""

    display_value: float
    display_unit: str
    native_value: float
    native_unit: str
    conversion_applied: bool
    source: str  # explicit | inferred | catalog

    @property
    def display_text(self) -> str:
        return format_value_unit(self.display_value, self.display_unit)

    @property
    def native_text(self) -> str:
        return format_value_unit(self.native_value, self.native_unit)

    def plan_sentence(self, *, variable_name: str = "") -> str:
        if not self.conversion_applied:
            return ""
        var_bit = f" (`{variable_name}`)" if variable_name else ""
        return (
            f"You specified {self.display_text}; forecast data{var_bit} is stored in "
            f"{self.native_unit}. I'll compare against {self.native_text} "
            f"({self.display_text})."
        )

    def confirm_question(self) -> str:
        if not self.conversion_applied:
            return ""
        return (
            f"You mentioned {self.display_text}; stored forecast values are in "
            f"{self.native_unit}. I'll use {self.native_text} for comparisons — "
            f"is that the threshold you meant?"
        )


def normalize_unit(raw: Any) -> str:
    text = str(raw or "").strip().lower().replace(" ", "")
    if not text:
        return ""
    text = text.replace("degrees", "deg").replace("degree", "deg")
    if text in _TEMP_F or text.endswith("f") and "c" not in text:
        return "°F"
    if text in _TEMP_C or (text.endswith("c") and "f" not in text):
        return "°C"
    if text in ("mw", "megawatts"):
        return "MW"
    if text in ("mwh",):
        return "MWh"
    if text in ("m/s", "mps", "meterspersecond"):
        return "m/s"
    # Preserve catalog symbols when already normalized
    original = str(raw or "").strip()
    if original in ("°C", "°F", "MW", "MWh", "$/MWh", "m/s"):
        return original
    return original


def format_value_unit(value: float, unit: str) -> str:
    if abs(value - round(value)) < 1e-6:
        text = str(int(round(value)))
    else:
        text = f"{value:.1f}".rstrip("0").rstrip(".")
    unit = normalize_unit(unit) or unit
    if unit.startswith("°") or unit.startswith("$"):
        return f"{text}{unit}"
    if unit:
        return f"{text} {unit}"
    return text


def c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


def is_temperature(var_name: str, category: str, unit: str) -> bool:
    name = (var_name or "").lower()
    cat = (category or "").lower()
    u = normalize_unit(unit)
    if "temp" in name or cat == "weather" and u in ("°C", "°F"):
        return True
    return u in ("°C", "°F")


def unit_preference_from_context(ctx: ResolverContext) -> Optional[str]:
    """Preferred display unit from LLM1 criteria, stats params, or user wording."""
    dim = ctx.aep.query.variable
    criteria = dim.criteria if dim else {}
    if isinstance(criteria, dict):
        for key in ("unit_preference", "unit", "preferred_unit"):
            if criteria.get(key):
                return normalize_unit(criteria[key])

    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    for key in ("threshold_unit", "unit", "unit_preference"):
        if params.get(key):
            return normalize_unit(params[key])

    return infer_unit_from_message(getattr(ctx, "user_message", "") or "")


def infer_unit_from_message(message: str) -> Optional[str]:
    lower = (message or "").lower()
    if re.search(r"°?\s*f(?:ahrenheit)?\b|\bdeg(?:ree)?s?\s*f\b|\bfahrenheit\b", lower):
        return "°F"
    if re.search(r"°?\s*c(?:elsius)?\b|\bdeg(?:ree)?s?\s*c\b|\bcelsius\b", lower):
        return "°C"
    return None


def parse_threshold_from_message(message: str) -> Optional[Tuple[float, str]]:
    """Extract (value, unit) when user wrote e.g. 95F or 95°F."""
    text = message or ""
    patterns = (
        r"(\d+(?:\.\d+)?)\s*°?\s*([fF])(?:\b|ahrenheit)",
        r"(\d+(?:\.\d+)?)\s*°?\s*([cC])(?:\b|elsius)",
        r"(\d+(?:\.\d+)?)\s*degrees?\s*([fFcC])\b",
    )
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = normalize_unit(m.group(2))
            return val, unit
    return None


def resolve_display_unit(
    native_unit: str,
    preference: Optional[str],
    *,
    var_name: str = "",
    category: str = "",
) -> Tuple[str, Optional[UnitConversion]]:
    """Return (display_unit, conversion from native→display) if applicable."""
    native = normalize_unit(native_unit) or native_unit
    pref = normalize_unit(preference) if preference else ""

    if not pref or pref == native:
        return native, None

    if is_temperature(var_name, category, native) and native == "°C" and pref == "°F":
        return "°F", UnitConversion(from_unit="°C", to_unit="°F")
    if is_temperature(var_name, category, native) and native == "°F" and pref == "°C":
        return "°C", UnitConversion(from_unit="°F", to_unit="°C")

    # Unknown pair — show preference but no silent conversion
    return pref or native, None


def convert_value(value: float, conversion: UnitConversion) -> float:
    if conversion.from_unit == "°C" and conversion.to_unit == "°F":
        return c_to_f(value)
    if conversion.from_unit == "°F" and conversion.to_unit == "°C":
        return f_to_c(value)
    return value


def resolve_threshold_context(ctx: ResolverContext) -> Optional[ThresholdContext]:
    """Map threshold param + units to display vs native storage units."""
    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    raw_threshold = params.get("threshold")
    if raw_threshold is None or isinstance(raw_threshold, (dict, list)):
        return None

    try:
        threshold_val = float(raw_threshold)
    except (TypeError, ValueError):
        return None

    var = ctx.variable
    if not var:
        return None

    native_unit = normalize_unit(getattr(var, "native_unit", None) or var.unit) or var.unit
    if not is_temperature(var.name, var.category, native_unit):
        return ThresholdContext(
            display_value=threshold_val,
            display_unit=native_unit,
            native_value=threshold_val,
            native_unit=native_unit,
            conversion_applied=False,
            source="catalog",
        )

    pref = unit_preference_from_context(ctx)
    msg_parsed = parse_threshold_from_message(getattr(ctx, "user_message", "") or "")
    source = "catalog"

    if msg_parsed:
        display_value, display_unit = msg_parsed
        source = "explicit"
    elif pref and pref != native_unit:
        display_unit = pref
        display_value = threshold_val
        source = "explicit"
        if native_unit == "°C" and pref == "°F":
            if threshold_val <= 60:
                display_value = c_to_f(threshold_val)
            else:
                source = "inferred"
    elif (
        native_unit == "°C"
        and threshold_val >= 45
        and _looks_like_fahrenheit_threshold(ctx, threshold_val)
    ):
        display_value = threshold_val
        display_unit = "°F"
        source = "inferred"
    else:
        display_value = threshold_val
        display_unit = native_unit

    native_value, conversion_applied = _native_threshold_value(
        display_value,
        display_unit,
        native_unit,
        threshold_val,
    )

    return ThresholdContext(
        display_value=display_value,
        display_unit=display_unit,
        native_value=native_value,
        native_unit=native_unit,
        conversion_applied=conversion_applied,
        source=source,
    )


def _native_threshold_value(
    display_value: float,
    display_unit: str,
    native_unit: str,
    param_value: float,
) -> Tuple[float, bool]:
    if display_unit == native_unit:
        return display_value, False

    if display_unit == "°F" and native_unit == "°C":
        converted = f_to_c(display_value)
        if abs(param_value - converted) <= max(0.5, 0.02 * abs(converted)):
            return param_value, True
        if abs(param_value - display_value) <= 0.01:
            return f_to_c(param_value), True
        return converted, True

    if display_unit == "°C" and native_unit == "°F":
        converted = c_to_f(display_value)
        if abs(param_value - converted) <= max(0.5, 0.02 * abs(converted)):
            return param_value, True
        return converted, True

    return param_value, display_unit != native_unit


def _looks_like_fahrenheit_threshold(ctx: ResolverContext, threshold: float) -> bool:
    """95 on temp in Texas context is almost certainly °F, not °C."""
    if threshold < 45 or threshold > 130:
        return False
    msg = (getattr(ctx, "user_message", "") or "").lower()
    if any(w in msg for w in ("heat", "hot", "warm", "95", "100", "90")):
        return True
    var = ctx.variable
    if var and "temp" in var.name.lower():
        return True
    return False


def enrich_variable_units(
    var: Any,
    ctx: ResolverContext,
) -> Any:
    """Set native_unit, display unit, and conversion on ResolvedVariable."""
    from analytics.models import ResolvedVariable

    if not isinstance(var, ResolvedVariable):
        return var

    native = normalize_unit(var.unit) or var.unit
    pref = unit_preference_from_context(ctx)
    display, conversion = resolve_display_unit(
        native, pref, var_name=var.name, category=var.category
    )
    uc_dict = conversion.to_dict() if conversion else None
    return ResolvedVariable(
        name=var.name,
        display_name=var.display_name,
        unit=display,
        category=var.category,
        native_unit=native,
        unit_conversion=uc_dict,
    )


def enrich_statistics_threshold(ctx: ResolverContext) -> None:
    """Attach normalized threshold fields for downstream SQL and confirm copy."""
    tc = resolve_threshold_context(ctx)
    if not tc:
        return
    stats = dict(ctx.statistics or {})
    params = dict(stats.get("parameters") or {})
    params["threshold_display"] = tc.display_value
    params["threshold_unit"] = tc.display_unit
    if tc.conversion_applied:
        params["threshold_native"] = tc.native_value
        params["threshold_native_unit"] = tc.native_unit
    stats["parameters"] = params
    ctx.statistics = stats
    if ctx.rep is not None:
        ctx.rep.statistics = stats
