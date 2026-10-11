"""Transparent recommendation engine for CloudSavings.

Public entry point:

    generate_recommendations(df) -> list[Recommendation]

Every recommendation is the result of a *transparent* rule: the reason
is spelled out, the required evidence is listed, and the confidence
level reflects how weak or strong the signal is. We never claim that
cost data alone proves a resource is unused, and we never present an
estimate as a verified saving.

Design rules:

    - No Streamlit imports. No I/O. No global state.
    - Never mutate the input DataFrame.
    - Always return a list (possibly empty), never raise on empty input.
    - Money values are floats, rounded to 2 decimals.
    - Every rule function has a stable `id`, so callers can filter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

Confidence = Literal["low", "medium", "high"]
Category = Literal[
    "spend_increase",
    "step_change",
    "concentration",
    "daily_outlier",
    "storage_review",
]

# ---- Thresholds (documented, easy to tune) --------------------------------

# Rule 1 — unusual month-over-month increase.
# Flag a month whose cost is at least this ratio above the mean of the
# preceding months, with at least two preceding months available.
UNUSUAL_INCREASE_RATIO: float = 1.30
MAX_UNUSUAL_INCREASE_RESULTS: int = 3


# Rule 2 — sustained step change.
# Compare the mean daily cost before vs after a candidate change date.
# A candidate is confirmed if the after/before ratio exceeds this value
# and both windows have at least this many days.
STEP_CHANGE_RATIO: float = 1.35
STEP_CHANGE_MIN_DAYS: int = 14

# Rule 3 — spend concentration.
# Flag a service whose share of the total exceeds this threshold.
CONCENTRATION_SHARE_PCT: float = 40.0

# Rule 4 — daily outlier.
# A day is an outlier if it is above mean + k * stddev of daily totals.
DAILY_OUTLIER_K: float = 3.0
DAILY_OUTLIER_MIN_DAYS: int = 30

# Rule 5 — storage family review.
# Services whose name contains these substrings are considered storage.
STORAGE_SERVICE_HINTS: tuple[str, ...] = (
    "Simple Storage Service",
    "Elastic Block Store",
    "Glacier",
    "Elastic File System",
)

# Conservative fraction of a service's monthly cost that could plausibly
# be saved by optimising storage class or cleanup. This is a *cap*, not
# a promise. Real savings require evidence.
STORAGE_SAVING_CAP_FRACTION: float = 0.15


# ---- Data structures ------------------------------------------------------

@dataclass(frozen=True)
class Recommendation:
    """A single, transparent recommendation.

    `estimated_monthly_savings_usd` is an *estimate* — an upper bound
    that a human still needs to validate. It must never be presented as
    a verified saving.
    """
    id: str                        # stable identifier of the rule
    title: str                     # short human-readable title
    service: str                   # AWS service the recommendation targets
    category: Category
    reason: str                    # why we are surfacing this
    estimated_monthly_savings_usd: float
    confidence: Confidence
    required_evidence: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    next_action: str = ""

    def __str__(self) -> str:
        return (
            f"[{self.confidence}] {self.title} "
            f"({self.service}) — ~${self.estimated_monthly_savings_usd:,.2f}/month"
        )


# ---- Helpers --------------------------------------------------------------

def _monthly_by_service(df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame indexed by (month, service) with total cost."""
    work = df.loc[:, ["usage_date", "service", "unblended_cost"]].copy()
    work["month"] = work["usage_date"].dt.to_period("M")
    return work.groupby(["service", "month"])["unblended_cost"].sum()


def _daily_by_service(df: pd.DataFrame) -> pd.DataFrame:
    work = df.loc[:, ["usage_date", "service", "unblended_cost"]].copy()
    work["date"] = work["usage_date"].dt.normalize()
    return work.groupby(["service", "date"])["unblended_cost"].sum()


# ---- Rule 1: unusual month-over-month increase ----------------------------

def _rule_unusual_increase(df: pd.DataFrame) -> list[Recommendation]:
    """Flag a service whose cost in a month is ≥ 1.30× the mean of
    the preceding months. Requires at least two preceding months.

    Collects every candidate, then keeps the top N by absolute delta.
    This avoids flooding the user when the dataset has monotonic growth.
    """
    candidates: list[Recommendation] = []
    monthly = _monthly_by_service(df)

    for service in monthly.index.get_level_values("service").unique():
        series = monthly.loc[service].sort_index()
        if len(series) < 3:
            continue

        for i in range(2, len(series)):
            prior = series.iloc[:i]
            current = series.iloc[i]
            prior_mean = prior.mean()
            if prior_mean <= 0:
                continue
            ratio = current / prior_mean
            if ratio < UNUSUAL_INCREASE_RATIO:
                continue

            delta = current - prior_mean
            month_label = str(series.index[i])

            candidates.append(Recommendation(
                id="unusual_increase",
                title=f"Unusual cost increase in {month_label}",
                service=service,
                category="spend_increase",
                reason=(
                    f"{service} cost in {month_label} was "
                    f"${current:,.2f}, which is {ratio:.2f}× the mean of "
                    f"the preceding months (${prior_mean:,.2f}). "
                    f"This is a signal worth investigating, not proof of "
                    f"waste."
                ),
                estimated_monthly_savings_usd=round(delta, 2),
                confidence="low",
                required_evidence=(
                    "Resource-level inventory for the affected month.",
                    "Usage metrics (CPU, memory, storage IOPS) for the same period.",
                    "Change log or deployment history around the month boundary.",
                ),
                risks=(
                    "The increase may be legitimate growth in workload.",
                    "Some of the increase may be seasonal or one-off.",
                ),
                next_action=(
                    "Compare the affected month's resource inventory against "
                    "the baseline months. Check for new instances, new buckets, "
                    "or a change in deployment."
                ),
            ))

    candidates.sort(key=lambda r: -r.estimated_monthly_savings_usd)
    return candidates[:MAX_UNUSUAL_INCREASE_RESULTS]

# ---- Rule 2: sustained step change ----------------------------------------

def _rule_step_change(df: pd.DataFrame) -> list[Recommendation]:
    """Detect a sustained step change in a service's daily cost.

    Scans each service for a candidate day where the mean of the
    following N days is ≥ 1.35× the mean of the preceding N days.
    Reports the earliest such day per service.
    """
    results: list[Recommendation] = []
    daily = _daily_by_service(df)

    for service in daily.index.get_level_values("service").unique():
        series = daily.loc[service].sort_index()
        if len(series) < STEP_CHANGE_MIN_DAYS * 2:
            continue

        values = series.to_numpy()
        n = STEP_CHANGE_MIN_DAYS
        found_at: int | None = None

        for i in range(n, len(values) - n + 1):
            before = values[i - n:i].mean()
            after = values[i:i + n].mean()
            if before <= 0:
                continue
            if after / before >= STEP_CHANGE_RATIO:
                found_at = i
                break

        if found_at is None:
            continue

        before_mean = values[found_at - n:found_at].mean()
        after_mean = values[found_at:found_at + n].mean()
        daily_delta = after_mean - before_mean
        # Express the delta as a monthly figure (×30), clearly marked
        # as an estimate.
        monthly_delta = daily_delta * 30
        change_date = series.index[found_at].date().isoformat()

        results.append(Recommendation(
            id="step_change",
            title=f"Sustained cost step around {change_date}",
            service=service,
            category="step_change",
            reason=(
                f"{service} daily cost jumped from an average of "
                f"${before_mean:,.2f} to ${after_mean:,.2f} around "
                f"{change_date} and stayed there. "
                f"This looks like a persistent change, not noise."
            ),
            estimated_monthly_savings_usd=round(monthly_delta, 2),
            confidence="medium",
            required_evidence=(
                "Inventory snapshot before and after the change date.",
                "Deployment or change-management log for that window.",
                "Confirmation that the new baseline is intentional.",
            ),
            risks=(
                "The new baseline may be required by a legitimate workload change.",
                "Reducing cost may require a change that affects availability.",
            ),
            next_action=(
                f"Review what changed around {change_date}. "
                f"If the new baseline is intentional, no action. "
                f"If not, investigate and consider reverting or resizing."
            ),
        ))
    return results


# ---- Rule 3: spend concentration ------------------------------------------

def _rule_concentration(df: pd.DataFrame) -> list[Recommendation]:
    """Flag a service that represents ≥ 40% of total spend."""
    results: list[Recommendation] = []
    total = df["unblended_cost"].sum()
    if total <= 0:
        return results

    per_service = df.groupby("service")["unblended_cost"].sum().sort_values(
        ascending=False
    )
    top_service = per_service.index[0]
    top_cost = per_service.iloc[0]
    share = top_cost / total * 100

    if share < CONCENTRATION_SHARE_PCT:
        return results

    results.append(Recommendation(
        id="concentration",
        title=f"{top_service} dominates total spend ({share:.1f}%)",
        service=top_service,
        category="concentration",
        reason=(
            f"{top_service} accounts for ${top_cost:,.2f} of "
            f"${total:,.2f} total spend over the period ({share:.1f}%). "
            f"A single service this dominant is worth a dedicated review."
        ),
        # We do not attribute a savings figure to concentration alone —
        # it is a signal, not a specific opportunity.
        estimated_monthly_savings_usd=0.0,
        confidence="low",
        required_evidence=(
            "Breakdown of the service's cost by usage type and region.",
            "Utilisation metrics to identify idle or underused resources.",
        ),
        risks=(
            "High share is not itself a problem — it may reflect the "
            "actual business need.",
        ),
        next_action=(
            f"Drill into {top_service}'s cost by usage type. "
            f"Look for the top 3 usage types by cost, then examine "
            f"the corresponding resources."
        ),
    ))
    return results


# ---- Rule 4: daily outlier ------------------------------------------------

def _rule_daily_outlier(df: pd.DataFrame) -> list[Recommendation]:
    """Flag the service with the largest single-day cost outlier."""
    results: list[Recommendation] = []
    daily = _daily_by_service(df)

    best: tuple[float, str, pd.Timestamp, float, float] | None = None

    for service in daily.index.get_level_values("service").unique():
        series = daily.loc[service].sort_index()
        if len(series) < DAILY_OUTLIER_MIN_DAYS:
            continue
        mean = series.mean()
        std = series.std(ddof=0)
        if std <= 0:
            continue
        threshold = mean + DAILY_OUTLIER_K * std
        above = series[series > threshold]
        if len(above) == 0:
            continue
        peak_date = above.idxmax()
        peak_value = series.loc[peak_date]
        # Rank candidates by how far above the threshold they are.
        excess = peak_value - threshold
        if best is None or excess > best[0]:
            best = (excess, service, peak_date, peak_value, mean)

    if best is None:
        return results

    _, service, peak_date, peak_value, mean = best
    date_label = peak_date.date().isoformat()
    # The plausible savings figure is the excess over the *mean*, not
    # over the standard deviation threshold. We report it conservatively.
    excess_over_mean = peak_value - mean

    results.append(Recommendation(
        id="daily_outlier",
        title=f"Single-day cost outlier on {date_label}",
        service=service,
        category="daily_outlier",
        reason=(
            f"{service} cost on {date_label} was ${peak_value:,.2f}, "
            f"well above its daily average of ${mean:,.2f}. "
            f"A single spike this large usually has an identifiable cause."
        ),
        estimated_monthly_savings_usd=round(excess_over_mean, 2),
        confidence="low",
        required_evidence=(
            "Billing line items for the specific day.",
            "Change log or deployment activity on that day.",
            "Confirmation whether the spike was a one-off or recurring.",
        ),
        risks=(
            "One-off spikes are often non-recurring and may not represent "
            "recoverable spend.",
        ),
        next_action=(
            f"Look at {service} line items for {date_label}. "
            f"If the spike was caused by a job, batch, or anomaly, "
            f"consider scheduling or capping it."
        ),
    ))
    return results


# ---- Rule 5: storage family review ----------------------------------------

def _rule_storage_review(df: pd.DataFrame) -> list[Recommendation]:
    """Suggest a storage-class review for storage-family services.

    Uses a conservative cap (15% of monthly cost) as a *potential*
    upper bound. Real savings require inspecting object access patterns
    and lifecycle configuration, which we do not have."""
    results: list[Recommendation] = []

    storage_services = [
        s for s in df["service"].unique()
        if any(hint in s for hint in STORAGE_SERVICE_HINTS)
    ]
    if not storage_services:
        return results

    # Work in whole months so the per-month figure is comparable.
    n_months = df["usage_date"].dt.to_period("M").nunique()
    if n_months < 2:
        return results

    for service in storage_services:
        subset = df[df["service"] == service]
        total = subset["unblended_cost"].sum()
        monthly = total / n_months
        potential = monthly * STORAGE_SAVING_CAP_FRACTION

        if monthly < 10:  # too small to be worth a review
            continue

        results.append(Recommendation(
            id="storage_review",
            title=f"Review storage class and lifecycle for {service}",
            service=service,
            category="storage_review",
            reason=(
                f"{service} costs about ${monthly:,.2f}/month on average. "
                f"Storage services often have 10–30% of cost tied up in "
                f"data that could move to a colder tier or be expired. "
                f"We cap the potential at "
                f"{int(STORAGE_SAVING_CAP_FRACTION * 100)}% — a conservative "
                f"upper bound, not a promise."
            ),
            estimated_monthly_savings_usd=round(potential, 2),
            confidence="low",
            required_evidence=(
                "Object count, size distribution, and access patterns.",
                "Current lifecycle policy configuration.",
                "Retention requirements from the business.",
            ),
            risks=(
                "Moving data to a colder tier can increase retrieval cost.",
                "Aggressive lifecycle rules can delete data that is still needed.",
            ),
            next_action=(
                f"Export the access-pattern report for {service} and identify "
                f"objects not accessed in the last 90 days. "
                f"Discuss a lifecycle policy with the data owner before applying."
            ),
        ))
    return results


# ---- Dispatcher -----------------------------------------------------------

_RULES = (
    _rule_unusual_increase,
    _rule_step_change,
    _rule_concentration,
    _rule_daily_outlier,
    _rule_storage_review,
)


def generate_recommendations(df: pd.DataFrame) -> list[Recommendation]:
    """Run every rule against `df` and return the combined list.

    Order: rules are run in a stable order, and the results are sorted
    at the end so that:
        - recommendations with a non-zero potential saving come first,
        - then by potential saving descending,
        - then by service name (stable tiebreaker).
    """
    if df is None or len(df) == 0:
        return []

    required = {"usage_date", "service", "unblended_cost"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"DataFrame is missing required columns: {sorted(missing)}"
        )

    results: list[Recommendation] = []
    for rule in _RULES:
        results.extend(rule(df))

    results.sort(
        key=lambda r: (
            0 if r.estimated_monthly_savings_usd > 0 else 1,
            -r.estimated_monthly_savings_usd,
            r.service,
        )
    )
    return results