"""Report generation for CloudSavings.

Public entry point:

    build_markdown_report(...) -> str

The report is a plain string. Writing it to disk, offering it as a
download, or emailing it is the caller's responsibility. This keeps
the module pure and testable.

The report deliberately separates:

    - Potential savings   (estimates from the recommendation engine)
    - Observed savings    (cost reduction between two windows)
    - Verified savings    (attributable to an approved intervention)
    - Eligible for billing (subset of verified, per contract)

It also includes an explicit "Limitations" section so that no reader
mistakes the report for a guarantee or an audit.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pandas as pd

from src.cost_analysis import (
    month_over_month_changes,
    monthly_totals,
    service_breakdown,
    summary_stats,
)
from src.recommendations import Recommendation
from src.savings import SavingsMeasurement

# Sentinel used when we do not want to include a timestamp at all
# (for tests, we pass `include_timestamp=False`).
_UNSET = object()


# ---- Small formatting helpers ---------------------------------------------

def _fmt_usd(x: float | None) -> str:
    if x is None:
        return "—"
    return f"${x:,.2f}"


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:+.2f}%"


def _fmt_date(x) -> str:
    if x is None:
        return "—"
    return str(x)


def _escape_pipes(text: str) -> str:
    """Make a string safe to place inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


# ---- Section builders -----------------------------------------------------

def _section_header(
    source_name: str,
    is_synthetic: bool,
    generated_at: datetime | None,
) -> str:
    lines = ["# CloudSavings — Cost report", ""]
    if generated_at is not None:
        lines.append(f"_Generated on {generated_at.isoformat(timespec='seconds')}_")
    else:
        lines.append("_Generated on <unspecified date>_")
    lines.append("")

    if is_synthetic:
        lines.append(
            "> **SIMULATED DATA.** This report was produced from the "
            "synthetic demonstration dataset bundled with the repository. "
            "It is not real AWS data and must not be presented as such."
        )
    else:
        lines.append(
            f"> **Source file:** `{source_name}`. "
            f"This report was produced from a user-provided CSV. "
            f"No data has left this machine."
        )
    lines.append("")
    return "\n".join(lines)


def _section_summary(df: pd.DataFrame) -> str:
    stats = summary_stats(df)
    lines = ["## 1. Spending summary", ""]
    lines.append(f"- **Total cost**: {_fmt_usd(stats['total_cost'])}")
    lines.append(
        f"- **Mean monthly cost**: {_fmt_usd(stats['mean_monthly_cost'])}"
    )
    lines.append(f"- **Min monthly cost**: {_fmt_usd(stats['min_month_cost'])}")
    lines.append(f"- **Max monthly cost**: {_fmt_usd(stats['max_month_cost'])}")
    lines.append(f"- **Services**: {stats['num_services']}")
    lines.append(f"- **Days covered**: {stats['num_days']}")
    lines.append(
        f"- **Period**: {_fmt_date(stats['period_start'])} → "
        f"{_fmt_date(stats['period_end'])}"
    )
    lines.append("")
    return "\n".join(lines)


def _section_monthly(df: pd.DataFrame) -> str:
    monthly = monthly_totals(df)
    if len(monthly) == 0:
        return ""

    mom = month_over_month_changes(df)
    # Build a lookup from month → change_pct so we can add a column.
    changes = {row["month"]: row["change_pct"] for _, row in mom.iterrows()}

    lines = ["## 2. Monthly breakdown", ""]
    lines.append("| Month | Total cost | Change vs previous |")
    lines.append("|-------|------------|--------------------|")

    for _, row in monthly.iterrows():
        month = row["month"]
        cost = _fmt_usd(row["total_cost"])
        change = changes.get(month)
        change_str = _fmt_pct(change) if change is not None and pd.notna(change) else "—"
        lines.append(f"| {month} | {cost} | {change_str} |")
    lines.append("")
    return "\n".join(lines)


def _section_services(df: pd.DataFrame) -> str:
    breakdown = service_breakdown(df)
    if len(breakdown) == 0:
        return ""

    lines = ["## 3. Spending by service", ""]
    lines.append("| Service | Total cost | Share |")
    lines.append("|---------|------------|-------|")

    for _, row in breakdown.iterrows():
        service = _escape_pipes(str(row["service"]))
        cost = _fmt_usd(row["total_cost"])
        share = f"{row['share_pct']:.2f}%"
        lines.append(f"| {service} | {cost} | {share} |")
    lines.append("")
    return "\n".join(lines)


def _section_recommendations(recommendations: Iterable[Recommendation]) -> str:
    recs = list(recommendations)
    lines = ["## 4. Recommendations", ""]

    if not recs:
        lines.append(
            "No recommendations were produced for this dataset. "
            "This is **not** a guarantee that the account is optimal — "
            "only that our current rules found nothing to flag."
        )
        lines.append("")
        return "\n".join(lines)

    lines.append(
        "The recommendations below are **automated estimates** produced "
        "from cost data alone. They are **not** verified savings, and they "
        "are **not** proof that any resource is unused or wasted."
    )
    lines.append("")

    total_potential = sum(r.estimated_monthly_savings_usd for r in recs)
    lines.append(
        f"**{len(recs)} recommendation(s), total estimated potential: "
        f"{_fmt_usd(total_potential)}/month**"
    )
    lines.append("")

    for i, rec in enumerate(recs, 1):
        lines.append(f"### 4.{i} {rec.title}")
        lines.append("")
        lines.append(f"- **Service**: `{_escape_pipes(rec.service)}`")
        lines.append(f"- **Category**: `{rec.category}`")
        lines.append(f"- **Confidence**: {rec.confidence}")
        lines.append(
            f"- **Estimated potential saving**: "
            f"{_fmt_usd(rec.estimated_monthly_savings_usd)}/month"
        )
        lines.append("")
        lines.append(f"**Why:** {rec.reason}")
        lines.append("")

        if rec.required_evidence:
            lines.append("**Evidence required before acting:**")
            lines.append("")
            for item in rec.required_evidence:
                lines.append(f"- {item}")
            lines.append("")

        if rec.risks:
            lines.append("**Risks / prerequisites:**")
            lines.append("")
            for item in rec.risks:
                lines.append(f"- {item}")
            lines.append("")

        if rec.next_action:
            lines.append(f"**Suggested next action:** {rec.next_action}")
            lines.append("")

    return "\n".join(lines)


def _section_savings(
    measurement: SavingsMeasurement | None,
    baseline_label: str = "",
    after_label: str = "",
) -> str:
    if measurement is None:
        return ""

    lines = ["## 5. Savings measurement", ""]

    if baseline_label or after_label:
        lines.append(
            f"**Baseline window:** {baseline_label}  \n"
            f"**After window:** {after_label}"
        )
        lines.append("")

    lines.append("| Tier | Amount | Meaning |")
    lines.append("|------|--------|---------|")
    lines.append(
        f"| Potential | {_fmt_usd(measurement.potential_monthly_saving)}/mo | "
        f"Sum of estimates from the recommendation engine. Not proven. |"
    )
    lines.append(
        f"| Observed | {_fmt_usd(measurement.observed_monthly_saving)}/mo | "
        f"Cost drop between the two windows. Not necessarily caused by us. |"
    )
    lines.append(
        f"| Verified | {_fmt_usd(measurement.verified_monthly_saving)}/mo | "
        f"Attributable to an approved intervention. |"
    )
    lines.append(
        f"| Eligible for billing | "
        f"{_fmt_usd(measurement.eligible_for_billing_monthly)}/mo | "
        f"Subset of verified that matches the contract rules. |"
    )
    lines.append("")

    lines.append("**Detailed calculation**")
    lines.append("")
    lines.append(
        f"- Baseline window: {measurement.baseline_days} days "
        f"→ {_fmt_usd(measurement.baseline_monthly_cost)}/month"
    )
    lines.append(
        f"- After window: {measurement.after_days} days "
        f"→ {_fmt_usd(measurement.after_monthly_cost)}/month"
    )
    lines.append(
        f"- Observed = baseline − after "
        f"= {_fmt_usd(measurement.observed_monthly_saving)}/month"
    )
    lines.append(
        f"- Verified = attributable, capped at declared expectation "
        f"= {_fmt_usd(measurement.verified_monthly_saving)}/month"
    )
    lines.append(
        f"- Eligible = {_fmt_usd(measurement.eligible_for_billing_monthly)}/month"
    )
    lines.append("")

    if measurement.interventions:
        lines.append("**Declared interventions**")
        lines.append("")
        for iv in measurement.interventions:
            lines.append(
                f"- `{iv.id}` on {iv.date} — {iv.service}: "
                f"{iv.description} "
                f"(approved by {iv.approved_by}, "
                f"cap {_fmt_usd(iv.expected_monthly_saving_usd)}/mo)"
            )
        lines.append("")

    if measurement.notes:
        lines.append("**Notes:**")
        lines.append("")
        for n in measurement.notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)


def _section_assumptions() -> str:
    lines = [
        "## 6. Assumptions and methodology",
        "",
        "- **Currency**: all figures are in the currency of the source file. "
        "No FX conversion is performed.",
        "- **Monthly normalisation**: a month is treated as 30 days. "
        "Windows shorter or longer than 30 days are scaled accordingly.",
        "- **Minimum window**: savings measurement requires at least "
        "14 days per window. Shorter windows are rejected.",
        "- **Attribution**: a cost drop is only attributed to us when an "
        "approved intervention is declared on the affected service. "
        "Otherwise it stays *observed*.",
        "- **Caps**: each intervention carries a declared maximum monthly "
        "saving. Any drop above the cap stays *observed* but is not "
        "*verified*.",
        "- **Global cap**: the sum of verified savings across interventions "
        "cannot exceed the total observed saving.",
        "",
    ]
    return "\n".join(lines)


def _section_limitations() -> str:
    lines = [
        "## 7. Limitations",
        "",
        "- This report is produced from **cost data alone**. It cannot prove "
        "that any resource is unused, idle, or over-provisioned.",
        "- Recommendations are **signals**, not findings. Each one lists the "
        "additional evidence required before any action.",
        "- **No resource is modified automatically.** Every optimization "
        "requires explicit human approval.",
        "- This is a **local MVP**, not a production service. It has not "
        "been reviewed for multi-user, multi-tenant, or regulatory use.",
        "- **No invoice is generated.** The RaaS fee simulator produces an "
        "illustration only; contractual, tax, and invoicing arrangements "
        "are outside the scope of this tool.",
        "",
    ]
    return "\n".join(lines)


def _section_next_steps() -> str:
    lines = [
        "## 8. Suggested next steps",
        "",
        "1. Review the recommendations that carry a positive estimated "
        "saving, starting with the highest confidence.",
        "2. For each one, collect the required evidence listed in the "
        "recommendation before approving any change.",
        "3. Record each approved intervention (date, service, expected "
        "monthly saving) so it can be measured afterwards.",
        "4. Re-run this report on the *after* period to compare observed "
        "vs verified savings.",
        "",
    ]
    return "\n".join(lines)


# ---- Public API -----------------------------------------------------------

def build_markdown_report(
    df: pd.DataFrame,
    recommendations: Iterable[Recommendation] = (),
    measurement: SavingsMeasurement | None = None,
    *,
    source_name: str = "uploaded.csv",
    is_synthetic: bool = False,
    generated_at: datetime | None = None,
    baseline_label: str = "",
    after_label: str = "",
) -> str:
    """Build a complete Markdown report as a string.

    Parameters
    ----------
    df:
        The typed cost DataFrame (post-import).
    recommendations:
        Recommendations to include. If empty, section 4 says so.
    measurement:
        Optional savings measurement. If provided, section 5 is added.
    source_name:
        Filename shown in the header.
    is_synthetic:
        True if the data comes from the bundled synthetic demo file.
    generated_at:
        Timestamp to embed. Pass `None` for a placeholder (useful in
        tests to keep output deterministic).
    baseline_label, after_label:
        Optional human labels for the two savings windows.

    Returns
    -------
    str — the full Markdown document.
    """
    parts = [
        _section_header(source_name, is_synthetic, generated_at),
        _section_summary(df),
        _section_monthly(df),
        _section_services(df),
        _section_recommendations(recommendations),
        _section_savings(measurement, baseline_label, after_label),
        _section_assumptions(),
        _section_limitations(),
        _section_next_steps(),
    ]
    # Drop empty sections cleanly.
    non_empty = [p for p in parts if p and p.strip()]
    return "\n".join(non_empty).rstrip() + "\n"