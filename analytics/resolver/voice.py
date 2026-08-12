"""Human-facing voice for resolver clarify / confirm replies.

The resolver stays deterministic; this module only turns structured outcomes
into consultant-style prose. No LLM.
"""

from __future__ import annotations

import re
from typing import List, Optional

from analytics.intent import is_metadata
from analytics.models import ConfirmationSummary, ResolvedExecutionPlan


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def compose_clarify_message(errors: List[str], *, prior_message: Optional[str] = None) -> str:
    """Turn resolver gaps into one natural clarification reply."""
    gaps = [_clean(e) for e in (errors or []) if _clean(e)]
    if not gaps:
        return (
            prior_message
            or "I want to make sure I get this right — could you share a bit more detail?"
        )

    if len(gaps) == 1:
        body = gaps[0]
        if body.endswith("?"):
            lead = "Almost there — "
            # Avoid double-leading if the gap already starts conversationally
            if body.lower().startswith(("which", "what", "could", "i ", "almost")):
                return body
            return lead + body[0].lower() + body[1:]
        return f"Almost there — {body[0].lower() + body[1:] if body else body}"

    lines = ["I can take this further once we pin down a couple of details:"]
    for g in gaps:
        # Only turn a gap into a question when it doesn't already ask one;
        # several gaps end with a trailing statement of what's available.
        if "?" not in g:
            g = g.rstrip(".") + "?"
        lines.append(f"• {g}")
    return "\n".join(lines)


def _friendly_date(iso_like: str) -> str:
    raw = (iso_like or "").strip()
    if not raw or raw.upper() == "N/A":
        return raw or "N/A"
    # 2026-08-10 or 2026-08-05T18:00:00Z
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?",
        raw,
    )
    if not m:
        return raw
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    label = f"{day} {months[month - 1]} {year}"
    if m.group(4) is not None:
        label += f", {m.group(4)}:{m.group(5)} UTC"
    return label


def _horizon_phrase(summary: ConfirmationSummary) -> str:
    h = (summary.forecast_horizon or "").strip()
    if not h or h.upper() == "N/A":
        return ""
    if "→" in h:
        start, end = [p.strip() for p in h.split("→", 1)]
        return f"from {_friendly_date(start)} to {_friendly_date(end)}"
    return f"for {_friendly_date(h)}"


def _representation_gloss(
    representation: str,
    rep: Optional[ResolvedExecutionPlan] = None,
) -> str:
    """Bold label plus a short how-it-is-computed clause for the confirm ask."""
    label = (representation or "").strip() or "the requested representation"
    bold = f"**{label}**"
    stats = (rep.statistics if rep is not None else None) or {}
    op = str(stats.get("operation") or "").lower()
    value = stats.get("value")
    params = stats.get("parameters") or {}
    if value is None:
        for key in ("value", "percentile", "p", "n"):
            if params.get(key) is not None:
                value = params.get(key)
                break

    if "median" in label.lower() or (op in ("percentile", "p", "median", "p50") and str(value) in ("50", "50.0")):
        return f"{bold} (middle of the 1000 ensemble paths)"
    if label.startswith("P") and label[1:].isdigit():
        return f"{bold} ({label[1:]}th percentile across the 1000 ensemble paths)"
    if op in ("percentile", "p") and value is not None:
        return f"{bold} (across the 1000 ensemble paths)"
    if "prediction interval" in label.lower():
        return f"{bold} (percentile band across the 1000 ensemble paths)"
    if "probability" in label.lower() or op == "probability":
        return f"{bold} (share of the 1000 ensemble paths)"
    if op in ("mean", "average") or label.lower() == "mean":
        return f"{bold} (average across the 1000 ensemble paths)"
    if op in ("trimmed_mean", "trim_mean", "winsorized_mean"):
        trim = params.get("trim_pct") or params.get("trim") or 10
        try:
            mid = max(0, 100 - 2 * int(trim))
        except (TypeError, ValueError):
            mid = 80
        return f"{bold} (drop outer paths; average middle ~{mid}% of the 1000 paths)"
    return f"a {bold}"


def compose_confirm_message(
    summary: ConfirmationSummary,
    rep: Optional[ResolvedExecutionPlan] = None,
) -> str:
    """Natural confirmation ask from a resolved summary (not a field dump)."""
    parts: List[str] = []

    echo = (summary.user_intent_echo or "").strip()
    if echo:
        parts.append(f"**What I heard:** {echo}")

    intent = ""
    if rep is not None:
        intent = (rep.intent or "").lower()
    if not intent and summary.analysis:
        intent = summary.analysis.split("(")[0].strip().lower()

    entity = summary.entity or "your entity"
    locations = summary.locations or "the selected locations"
    variable = ""
    if rep is not None and rep.variable and rep.variable.display_name:
        variable = rep.variable.display_name
        if rep.variable.unit:
            variable = f"{variable} ({rep.variable.unit})"

    if is_metadata(intent) or "metadata" in (summary.analysis or "").lower():
        target = locations if locations not in ("N/A", "") else "the requested catalog details"
        sentence = (
            f"Just to confirm before I look it up — you want **{target}** "
            f"for **{entity}**. Does that look right?"
        )
        parts.append(_clean(sentence))
        return "\n\n".join(parts)

    representation = summary.forecast_representation or "the requested representation"
    # Short gloss so the confirm card is not just a label — say what will be
    # computed across the 1000 ensemble paths.
    rep_gloss = _representation_gloss(representation, rep)
    horizon = _horizon_phrase(summary)
    init_label = summary.initialization or "the latest initialization"
    init_resolved = summary.initialization_resolved or ""
    init_bit = init_label
    if init_resolved and init_resolved.upper() != "N/A":
        init_bit = f"{init_label} (resolved to {_friendly_date(init_resolved)})"

    var_phrase = f"{variable} " if variable and variable != "N/A" else ""
    sentence = (
        f"Here's what I'll set up — {rep_gloss} {var_phrase}"
        f"forecast for **{entity}**"
    )
    if locations not in ("N/A", ""):
        sentence += f" at **{locations}**"
    if horizon:
        sentence += f", {horizon}"
    sentence += f", using {init_bit}."
    sentence += " Does this look right to proceed?"
    parts.append(_clean(sentence).replace(" ,", ","))

    comp = (summary.computation_summary or "").strip()
    if comp:
        parts.append(f"**How I'll calculate this:** {comp}")

    shape = (summary.output_shape or "").strip()
    if shape:
        parts.append(f"**Output shape:** {shape}")

    return "\n\n".join(parts)


def confirm_panel_short_message() -> str:
    """One-line chat bubble when the inline confirm panel carries the full plan."""
    return "Review the plan below and confirm to proceed."


def prefer_human_confirm_message(
    llm1_message: Optional[str],
    summary: ConfirmationSummary,
    rep: Optional[ResolvedExecutionPlan] = None,
    *,
    user_message: Optional[str] = None,
) -> str:
    """Chat bubble text for confirm — full plan lives in the inline panel only."""
    return confirm_panel_short_message()
