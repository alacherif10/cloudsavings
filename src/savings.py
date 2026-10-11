"""Transparent savings measurement for CloudSavings.

Separates four distinct notions that are too often conflated:

    1. Potential savings   — estimates from the recommendation engine.
                             Not proven, not billed.
    2. Observed savings    — cost reduction between baseline and after.
                             Real, but not attributable on its own.
    3. Verified savings    — observed reduction *attributable* to an
                             intervention approved by the customer,
                             after adjusting for legitimate workload
                             changes.
    4. Eligible for billing — subset of verified savings that matches
                             the contract's rules (period, service scope,
                             caps).

The module never assumes that a cost drop was caused by us. Attribution
requires (a) an approved intervention with a date, (b) the intervention
being recorded on the affected service, and (c) a cap expressed by the
customer as `expected_monthly_saving_usd`. Savings above the cap are
kept as `observed` but not claimed as `verified`.

Public entry point:

    measure_savings(baseline, after, interventions) -> SavingsMeasurement
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import pandas as pd

# By default a month is treated as 30 days. All per-month figures below
# are derived from this constant so they can be re-tuned in one place.
DAYS_PER_MONTH: float = 30.0

# If the number of days in a window is smaller than this, we refuse to
# produce a measurement — the sample would be too noisy.
MIN_WINDOW_DAYS: int = 14


# ---- Data structures ------------------------------------------------------

@dataclass(frozen=True)
class ApprovedIntervention:
    """An optimization the customer has explicitly approved.

    `expected_monthly_saving_usd` is the ceiling the customer accepts
    as the maximum attributable saving. Anything beyond is kept as
    observed but not claimed as verified.
    """
    id: str
    date: date
    service: str
    description: str
    approved_by: str
    expected_monthly_saving_usd: float


@dataclass
class SavingsMeasurement:
    """Full result of a savings measurement.

    All monetary values are expressed as **monthly** figures, in the
    same currency as the input. `notes` explains how each figure was
    derived and what assumptions were made.
    """
    baseline_days: int
    after_days: int
    baseline_monthly_cost: float
    after_monthly_cost: float

    # The three "savings" tiers.
    potential_monthly_saving: float
    observed_monthly_saving: float
    verified_monthly_saving: float
    eligible_for_billing_monthly: float

    interventions: tuple[ApprovedIntervention, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        lines = [
            "Savings measurement",
            "-------------------",
            f"Baseline window: {self.baseline_days} days  |  "
            f"After window: {self.after_days} days",
            f"Baseline monthly cost : ${self.baseline_monthly_cost:,.2f}",
            f"After monthly cost    : ${self.after_monthly_cost:,.2f}",
            "",
            f"Potential saving (estimates only) : ${self.potential_monthly_saving:,.2f}/month",
            f"Observed saving  (cost drop)      : ${self.observed_monthly_saving:,.2f}/month",
            f"Verified saving  (attributable)   : ${self.verified_monthly_saving:,.2f}/month",
            f"Eligible for billing              : ${self.eligible_for_billing_monthly:,.2f}/month",
        ]
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            for n in self.notes:
                lines.append(f"  - {n}")
        return "\n".join(lines)


# ---- Helpers --------------------------------------------------------------

_REQUIRED_COLS = frozenset({"usage_date", "service", "unblended_cost"})


def _check_columns(df: pd.DataFrame, label: str) -> None:
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"{label} DataFrame is missing required columns: {sorted(missing)}"
        )


def _window_days(df: pd.DataFrame) -> int:
    if len(df) == 0:
        return 0
    return int(
        (df["usage_date"].max().normalize() - df["usage_date"].min().normalize()).days
    ) + 1


def _monthly_cost(df: pd.DataFrame) -> float:
    """Total cost normalised to a 30-day month."""
    days = _window_days(df)
    if days <= 0:
        return 0.0
    return float(df["unblended_cost"].sum()) / days * DAYS_PER_MONTH


def _monthly_cost_for_service(df: pd.DataFrame, service: str) -> float:
    subset = df[df["service"] == service]
    return _monthly_cost(subset)


# ---- Public API -----------------------------------------------------------

def measure_savings(
    baseline: pd.DataFrame,
    after: pd.DataFrame,
    interventions: list[ApprovedIntervention] | None = None,
    *,
    potential_saving_monthly: float = 0.0,
) -> SavingsMeasurement:
    """Measure savings between a baseline and an after window.

    Parameters
    ----------
    baseline:
        Cost data for the reference period (before the interventions).
    after:
        Cost data for the measurement period (after the interventions).
    interventions:
        Approved interventions. Only these can generate *verified*
        savings. If empty or None, verified saving is 0.
    potential_saving_monthly:
        Optional estimate produced by the recommendation engine, kept
        separate so it is never confused with observed or verified.

    Returns
    -------
    SavingsMeasurement
    """
    _check_columns(baseline, "baseline")
    _check_columns(after, "after")

    notes: list[str] = []

    baseline_days = _window_days(baseline)
    after_days = _window_days(after)

    if baseline_days < MIN_WINDOW_DAYS:
        raise ValueError(
            f"Baseline window is too short ({baseline_days} days). "
            f"Need at least {MIN_WINDOW_DAYS} days."
        )
    if after_days < MIN_WINDOW_DAYS:
        raise ValueError(
            f"After window is too short ({after_days} days). "
            f"Need at least {MIN_WINDOW_DAYS} days."
        )

    baseline_monthly = _monthly_cost(baseline)
    after_monthly = _monthly_cost(after)
    observed = baseline_monthly - after_monthly

    if observed <= 0:
        notes.append(
            "No cost reduction was observed between the two windows. "
            "Verified saving is 0."
        )
        observed = 0.0

    interventions_tuple = tuple(interventions or ())

    if not interventions_tuple:
        notes.append(
            "No approved interventions provided. Any observed reduction "
            "is reported as *observed*, not *verified*: cost alone cannot "
            "prove causation."
        )
        verified = 0.0
    else:
        verified = _attribute_verified_saving(
            baseline=baseline,
            after=after,
            interventions=interventions_tuple,
            observed_monthly=observed,
            notes=notes,
        )

    # MVP rule: eligible for billing = verified. A future version will
    # add contractual filters (per-service caps, excluded services,
    # minimum thresholds) here.
    eligible = verified

    return SavingsMeasurement(
        baseline_days=baseline_days,
        after_days=after_days,
        baseline_monthly_cost=round(baseline_monthly, 2),
        after_monthly_cost=round(after_monthly, 2),
        potential_monthly_saving=round(potential_saving_monthly, 2),
        observed_monthly_saving=round(observed, 2),
        verified_monthly_saving=round(verified, 2),
        eligible_for_billing_monthly=round(eligible, 2),
        interventions=interventions_tuple,
        notes=tuple(notes),
    )


def _attribute_verified_saving(
    baseline: pd.DataFrame,
    after: pd.DataFrame,
    interventions: tuple[ApprovedIntervention, ...],
    observed_monthly: float,
    notes: list[str],
) -> float:
    """Compute the *verified* part of the observed saving.

    Rules:

    - For each intervention, look at its service.
    - Compute the drop (or rise) on that service between baseline and
      after.
    - Cap the attributable saving at the customer's declared
      `expected_monthly_saving_usd`.
    - Sum across interventions; the total cannot exceed the overall
      observed saving.
    """
    total_verified = 0.0

    for iv in interventions:
        before_cost = _monthly_cost_for_service(baseline, iv.service)
        after_cost = _monthly_cost_for_service(after, iv.service)
        drop = before_cost - after_cost

        if drop <= 0:
            notes.append(
                f"Intervention {iv.id} on {iv.service}: no cost drop "
                f"detected for that service. Contribution = $0.00."
            )
            continue

        capped = min(drop, iv.expected_monthly_saving_usd)
        total_verified += capped

        if drop > iv.expected_monthly_saving_usd:
            notes.append(
                f"Intervention {iv.id} on {iv.service}: observed drop "
                f"(${drop:,.2f}) exceeds declared expectation "
                f"(${iv.expected_monthly_saving_usd:,.2f}). "
                f"Only the declared amount counts as verified."
            )
        else:
            notes.append(
                f"Intervention {iv.id} on {iv.service}: "
                f"verified ${capped:,.2f}/month."
            )

    if total_verified > observed_monthly:
        notes.append(
            f"Sum of per-intervention verified savings "
            f"(${total_verified:,.2f}) exceeded the overall observed "
            f"saving (${observed_monthly:,.2f}). Capped to the observed "
            f"total — we do not claim more than what the bill shows."
        )
        total_verified = observed_monthly

    return total_verified