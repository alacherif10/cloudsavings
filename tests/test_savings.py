"""Tests for src.savings.

Covers the four savings tiers, the two caps (per-intervention and
global), the minimum-window guard, and the honest default behaviour:
without an approved intervention, verified saving is always 0.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pandas as pd
import pytest

from src.savings import (
    DAYS_PER_MONTH,
    MIN_WINDOW_DAYS,
    ApprovedIntervention,
    SavingsMeasurement,
    measure_savings,
)


# ---- Helpers --------------------------------------------------------------

def _window(
    start: date,
    days: int,
    service: str,
    cost_per_day: float,
) -> pd.DataFrame:
    """Build a DataFrame with `days` consecutive rows of constant cost."""
    dates = [start + timedelta(days=i) for i in range(days)]
    return pd.DataFrame({
        "usage_date": pd.to_datetime([d.isoformat() for d in dates]),
        "service": service,
        "unblended_cost": cost_per_day,
    })


def _intervention(
    id_: str = "iv-1",
    day: date = date(2026, 2, 1),
    service: str = "EC2",
    expected: float = 100.0,
) -> ApprovedIntervention:
    return ApprovedIntervention(
        id=id_,
        date=day,
        service=service,
        description="Rightsize",
        approved_by="customer@acme.test",
        expected_monthly_saving_usd=expected,
    )


# ---- Dataclasses ----------------------------------------------------------

def test_intervention_is_frozen() -> None:
    iv = _intervention()
    with pytest.raises(FrozenInstanceError):
        iv.expected_monthly_saving_usd = 200.0  # type: ignore[misc]


def test_savings_measurement_default_fields() -> None:
    m = SavingsMeasurement(
        baseline_days=30, after_days=30,
        baseline_monthly_cost=100.0, after_monthly_cost=100.0,
        potential_monthly_saving=0.0, observed_monthly_saving=0.0,
        verified_monthly_saving=0.0, eligible_for_billing_monthly=0.0,
    )
    assert m.interventions == ()
    assert m.notes == ()


# ---- Input validation -----------------------------------------------------

def test_missing_baseline_columns_raises() -> None:
    bad = pd.DataFrame({"usage_date": [pd.Timestamp("2026-01-01")]})
    after = _window(date(2026, 2, 1), MIN_WINDOW_DAYS, "EC2", 5.0)
    with pytest.raises(ValueError, match="baseline DataFrame is missing"):
        measure_savings(bad, after)


def test_missing_after_columns_raises() -> None:
    baseline = _window(date(2026, 1, 1), MIN_WINDOW_DAYS, "EC2", 5.0)
    bad = pd.DataFrame({"usage_date": [pd.Timestamp("2026-02-01")]})
    with pytest.raises(ValueError, match="after DataFrame is missing"):
        measure_savings(baseline, bad)


def test_baseline_window_too_short_raises() -> None:
    baseline = _window(date(2026, 1, 1), MIN_WINDOW_DAYS - 1, "EC2", 5.0)
    after = _window(date(2026, 2, 1), MIN_WINDOW_DAYS, "EC2", 5.0)
    with pytest.raises(ValueError, match="Baseline window is too short"):
        measure_savings(baseline, after)


def test_after_window_too_short_raises() -> None:
    baseline = _window(date(2026, 1, 1), MIN_WINDOW_DAYS, "EC2", 5.0)
    after = _window(date(2026, 2, 1), MIN_WINDOW_DAYS - 1, "EC2", 5.0)
    with pytest.raises(ValueError, match="After window is too short"):
        measure_savings(baseline, after)


# ---- Normalisation to a 30-day month --------------------------------------

def test_monthly_cost_normalisation() -> None:
    # 20 days at $10/day → $200 over 20 days → $300 over 30 days.
    baseline = _window(date(2026, 1, 1), 20, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 20, "EC2", 10.0)
    m = measure_savings(baseline, after)
    assert m.baseline_monthly_cost == pytest.approx(300.0)
    assert m.after_monthly_cost == pytest.approx(300.0)


def test_monthly_cost_for_thirty_day_window() -> None:
    # 30 days at $10/day → $300.
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 10.0)
    m = measure_savings(baseline, after)
    assert m.baseline_monthly_cost == pytest.approx(300.0)


# ---- The four tiers -------------------------------------------------------

def test_potential_saving_is_passed_through() -> None:
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 10.0)
    m = measure_savings(baseline, after, potential_saving_monthly=250.0)
    assert m.potential_monthly_saving == pytest.approx(250.0)


def test_observed_saving_no_intervention() -> None:
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)  # $300/mo
    after = _window(date(2026, 2, 1), 30, "EC2", 6.0)      # $180/mo
    m = measure_savings(baseline, after)
    assert m.observed_monthly_saving == pytest.approx(120.0)


def test_verified_is_zero_without_intervention() -> None:
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 6.0)
    m = measure_savings(baseline, after)
    assert m.verified_monthly_saving == 0.0
    assert m.eligible_for_billing_monthly == 0.0
    assert any("No approved interventions" in n for n in m.notes)


def test_verified_capped_at_expected() -> None:
    # Drop of $120, but the customer declared $100 expected.
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 6.0)
    iv = _intervention(expected=100.0)
    m = measure_savings(baseline, after, [iv])
    assert m.verified_monthly_saving == pytest.approx(100.0)
    assert any("exceeds declared expectation" in n for n in m.notes)


def test_verified_kept_when_below_expected() -> None:
    # Drop of $60, customer declared $100 expected → verified = 60.
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 8.0)
    iv = _intervention(expected=100.0)
    m = measure_savings(baseline, after, [iv])
    assert m.verified_monthly_saving == pytest.approx(60.0)


# ---- No cost drop ---------------------------------------------------------

def test_no_cost_drop_means_zero_observed() -> None:
    baseline = _window(date(2026, 1, 1), 30, "EC2", 5.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 5.0)
    m = measure_savings(baseline, after)
    assert m.observed_monthly_saving == 0.0
    assert any("No cost reduction" in n for n in m.notes)


def test_cost_increase_means_zero_observed() -> None:
    baseline = _window(date(2026, 1, 1), 30, "EC2", 5.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 8.0)
    m = measure_savings(baseline, after)
    assert m.observed_monthly_saving == 0.0


def test_no_cost_drop_on_service_zero_contribution() -> None:
    # Overall drop, but the intervention's service did not drop.
    baseline = pd.concat([
        _window(date(2026, 1, 1), 30, "EC2", 10.0),
        _window(date(2026, 1, 1), 30, "S3", 5.0),
    ], ignore_index=True)
    after = pd.concat([
        _window(date(2026, 2, 1), 30, "EC2", 10.0),  # unchanged
        _window(date(2026, 2, 1), 30, "S3", 1.0),    # big drop on S3
    ], ignore_index=True)
    iv = _intervention(service="EC2", expected=100.0)
    m = measure_savings(baseline, after, [iv])
    assert m.verified_monthly_saving == 0.0
    assert any("no cost drop detected" in n for n in m.notes)


# ---- Service-specific attribution -----------------------------------------

def test_intervention_targets_correct_service() -> None:
    baseline = pd.concat([
        _window(date(2026, 1, 1), 30, "EC2", 10.0),
        _window(date(2026, 1, 1), 30, "S3", 5.0),
    ], ignore_index=True)
    after = pd.concat([
        _window(date(2026, 2, 1), 30, "EC2", 6.0),   # $180 from $300
        _window(date(2026, 2, 1), 30, "S3", 3.0),    # $90 from $150
    ], ignore_index=True)
    iv_ec2 = _intervention(id_="iv-ec2", service="EC2", expected=1000.0)
    m = measure_savings(baseline, after, [iv_ec2])
    # The EC2 drop is $120/mo; the S3 drop is not attributed.
    assert m.verified_monthly_saving == pytest.approx(120.0)


# ---- Global cap -----------------------------------------------------------

def test_global_cap_when_sum_exceeds_observed() -> None:
    # Overall drop of $120. Two interventions each with expected $100.
    # Sum = $200, but the observed total is $120 → verified capped at 120.
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 6.0)
    ivs = [
        _intervention(id_="iv-a", expected=100.0),
        _intervention(id_="iv-b", expected=100.0),
    ]
    m = measure_savings(baseline, after, ivs)
    assert m.verified_monthly_saving == pytest.approx(120.0)
    assert any("exceeded the overall observed" in n for n in m.notes)


# ---- Eligibility ----------------------------------------------------------

def test_eligible_equals_verified_for_now() -> None:
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 6.0)
    iv = _intervention(expected=100.0)
    m = measure_savings(baseline, after, [iv])
    assert m.eligible_for_billing_monthly == m.verified_monthly_saving


# ---- Summary text ---------------------------------------------------------

def test_summary_contains_all_four_tiers() -> None:
    baseline = _window(date(2026, 1, 1), 30, "EC2", 10.0)
    after = _window(date(2026, 2, 1), 30, "EC2", 6.0)
    iv = _intervention(expected=100.0)
    text = measure_savings(
        baseline, after, [iv], potential_saving_monthly=250.0
    ).summary()

    assert "Potential saving" in text
    assert "Observed saving" in text
    assert "Verified saving" in text
    assert "Eligible for billing" in text


# ---- Integration with the demo dataset ------------------------------------

def test_demo_dataset_supports_a_full_measurement() -> None:
    """Run a realistic scenario: baseline = Apr–May, after = Aug–Sep.

    No intervention is provided, so verified saving must be 0 even
    though the cost grew. This locks the honesty invariant.
    """
    from src.csv_import import import_csv_from_bytes

    df = import_csv_from_bytes(
        open("data/demo_costs.csv", "rb").read(), "demo.csv"
    ).df
    assert df is not None

    df["month"] = df["usage_date"].dt.to_period("M")
    baseline = df[df["month"].isin([pd.Period("2026-04", "M"), pd.Period("2026-05", "M")])]
    after = df[df["month"].isin([pd.Period("2026-08", "M"), pd.Period("2026-09", "M")])]

    m = measure_savings(baseline, after)
    assert m.baseline_days >= MIN_WINDOW_DAYS
    assert m.after_days >= MIN_WINDOW_DAYS
    # The demo dataset grows over time, so no cost drop is expected.
    assert m.observed_monthly_saving == 0.0
    assert m.verified_monthly_saving == 0.0