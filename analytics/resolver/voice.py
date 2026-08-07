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


def compose_confirm_message(
    summary: ConfirmationSummary,
    rep: Optional[ResolvedExecutionPlan] = None,
) -> str:
    """Natural confirmation ask from a resolved summary (not a field dump)."""
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
        return (
            f"Just to confirm before I look it up — you want **{target}** "
            f"for **{entity}**. Does that look right?"
        )

    representation = summary.forecast_representation or "the requested representation"
    horizon = _horizon_phrase(summary)
    init_label = summary.initialization or "the latest initialization"
    init_resolved = summary.initialization_resolved or ""
    init_bit = init_label
    if init_resolved and init_resolved.upper() != "N/A":
        init_bit = f"{init_label} (resolved to {_friendly_date(init_resolved)})"

    var_phrase = f"{variable} " if variable and variable != "N/A" else ""
    sentence = (
        f"Here's what I'll set up — a **{representation}** {var_phrase}"
        f"forecast for **{entity}**"
    )
    if locations not in ("N/A", ""):
        sentence += f" at **{locations}**"
    if horizon:
        sentence += f", {horizon}"
    sentence += f", using {init_bit}."
    sentence += " Does this look right to proceed?"
    return _clean(sentence).replace(" ,", ",")


def prefer_human_confirm_message(
    llm1_message: Optional[str],
    summary: ConfirmationSummary,
    rep: Optional[ResolvedExecutionPlan] = None,
) -> str:
    """Prefer a natural resolver narrative over mechanical LLM1 'Confirmed:' lines."""
    narrative = compose_confirm_message(summary, rep)
    prior = _clean(llm1_message or "")
    if not prior:
        return narrative
    mechanical_prefixes = (
        "confirmed:",
        "retrieving ",
        "i'll look up",
        "i will look up",
        "resolved plan",
        "here is the resolved",
    )
    if prior.lower().startswith(mechanical_prefixes):
        return narrative
    # Keep a warm LLM1 lead only if it already sounds like a question/confirm
    if "?" in prior and len(prior) < 280:
        return prior
    return narrative
