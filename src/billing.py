"""RaaS fee simulator for CloudSavings.

This module computes an illustrative fee from an *eligible* savings
figure and a negotiated percentage. It is a simulator only:

    - No payments are initiated.
    - No invoices are generated.
    - No external services are contacted.
    - No legal or tax advice is embedded.

The MVP deliberately separates the *fee simulation* from the *savings
measurement*. The fee is only ever computed on top of the eligible
figure produced by src.savings. Potential and observed savings are
never billed.

Public entry point:

    simulate_fee(eligible_monthly_savings, fee_pct, period_months=1)
        -> FeeSimulation
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Conservative bounds for the fee percentage. A percentage outside this
# range is almost certainly a data-entry mistake; we prefer to reject
# than to silently produce a nonsense figure.
MIN_FEE_PCT: float = 0.0
MAX_FEE_PCT: float = 50.0

# Period bounds, expressed in whole months. Shorter than one month
# would mean daily simulation, which the MVP does not support.
MIN_PERIOD_MONTHS: int = 1
MAX_PERIOD_MONTHS: int = 60


# ---- Data structures ------------------------------------------------------

@dataclass(frozen=True)
class FeeSimulation:
    """Result of a RaaS fee simulation.

    All monetary values are monthly or total-over-period, as indicated
    by the field name. `calculation_steps` is a human-readable trace of
    how the number was derived, so a user can audit it without reading
    the source code.
    """
    eligible_monthly_savings_usd: float
    fee_pct: float
    period_months: int

    eligible_over_period_usd: float
    fee_amount_usd: float

    calculation_steps: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        lines = [
            "RaaS fee simulation (illustrative)",
            "----------------------------------",
            f"Eligible savings (monthly) : ${self.eligible_monthly_savings_usd:,.2f}",
            f"Fee percentage             : {self.fee_pct:.2f}%",
            f"Period                     : {self.period_months} month(s)",
            "",
            f"Eligible savings over period : ${self.eligible_over_period_usd:,.2f}",
            f"Estimated fee                : ${self.fee_amount_usd:,.2f}",
        ]
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


# ---- Public API -----------------------------------------------------------

def simulate_fee(
    eligible_monthly_savings_usd: float,
    fee_pct: float,
    period_months: int = 1,
) -> FeeSimulation:
    """Compute an illustrative RaaS fee.

    Parameters
    ----------
    eligible_monthly_savings_usd:
        The monthly savings amount that the contract considers billable.
        Must be >= 0. Negative figures are rejected: a fee cannot be
        charged on negative savings, and the caller probably passed the
        wrong field.
    fee_pct:
        The negotiated percentage of eligible savings. Must be within
        [MIN_FEE_PCT, MAX_FEE_PCT].
    period_months:
        Length of the billing period, in whole months.

    Returns
    -------
    FeeSimulation

    Raises
    ------
    ValueError
        If any input is outside its documented bounds.
    """
    _validate(eligible_monthly_savings_usd, fee_pct, period_months)

    steps: list[str] = []
    warnings: list[str] = []

    steps.append(
        f"Start: eligible savings = ${eligible_monthly_savings_usd:,.2f}/month"
    )
    steps.append(f"Negotiated fee percentage = {fee_pct:.2f}%")
    steps.append(f"Billing period = {period_months} month(s)")

    eligible_over_period = eligible_monthly_savings_usd * period_months
    steps.append(
        f"Eligible savings over period = "
        f"${eligible_monthly_savings_usd:,.2f} × {period_months} "
        f"= ${eligible_over_period:,.2f}"
    )

    fee_amount = eligible_over_period * (fee_pct / 100.0)
    steps.append(
        f"Fee = ${eligible_over_period:,.2f} × {fee_pct:.2f}% "
        f"= ${fee_amount:,.2f}"
    )

    if eligible_monthly_savings_usd == 0:
        warnings.append(
            "Eligible savings is zero — the fee is zero. "
            "Verify that a savings measurement has been performed and "
            "that the contract actually covers the relevant services."
        )

    if fee_pct > 30:
        warnings.append(
            f"A fee of {fee_pct:.2f}% is above the range typically "
            f"seen in RaaS contracts. Double-check the negotiated value."
        )

    if period_months > 12:
        warnings.append(
            f"A period of {period_months} months is unusually long for "
            f"an initial billing cycle. Confirm it is intentional."
        )

    return FeeSimulation(
        eligible_monthly_savings_usd=round(eligible_monthly_savings_usd, 2),
        fee_pct=round(fee_pct, 4),
        period_months=period_months,
        eligible_over_period_usd=round(eligible_over_period, 2),
        fee_amount_usd=round(fee_amount, 2),
        calculation_steps=tuple(steps),
        warnings=tuple(warnings),
    )


# ---- Internals ------------------------------------------------------------

def _validate(
    eligible_monthly_savings_usd: float,
    fee_pct: float,
    period_months: int,
) -> None:
    if not isinstance(eligible_monthly_savings_usd, (int, float)):
        raise ValueError("eligible_monthly_savings_usd must be a number.")
    if eligible_monthly_savings_usd < 0:
        raise ValueError(
            "eligible_monthly_savings_usd must be non-negative. "
            "A fee cannot be charged on negative savings."
        )
    if not isinstance(fee_pct, (int, float)):
        raise ValueError("fee_pct must be a number.")
    if not (MIN_FEE_PCT <= fee_pct <= MAX_FEE_PCT):
        raise ValueError(
            f"fee_pct must be between {MIN_FEE_PCT} and {MAX_FEE_PCT} "
            f"(got {fee_pct})."
        )
    if not isinstance(period_months, int):
        raise ValueError("period_months must be an integer.")
    if not (MIN_PERIOD_MONTHS <= period_months <= MAX_PERIOD_MONTHS):
        raise ValueError(
            f"period_months must be between {MIN_PERIOD_MONTHS} and "
            f"{MAX_PERIOD_MONTHS} (got {period_months})."
        )