"""Tests for src.recommendations.

Each rule is exercised against a hand-built DataFrame so that the
expected behaviour can be checked by hand. A final test runs the
engine on the bundled demo dataset to catch integration regressions.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pandas as pd
import pytest

from src.csv_import import import_csv_from_bytes
from src.recommendations import (
    MAX_UNUSUAL_INCREASE_RESULTS,
    Recommendation,
    generate_recommendations,
)


# ---- Helpers --------------------------------------------------------------

def _row(day: str, service: str, cost: float) -> dict[str, object]:
    return {
        "usage_date": pd.Timestamp(day),
        "service": service,
        "region": "eu-west-1",
        "usage_type": "BoxUsage",
        "usage_amount": 1.0,
        "unit": "Hrs",
        "unblended_cost": cost,
        "currency": "USD",
    }


def _df(rows: list[dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["usage_date"] = pd.to_datetime(df["usage_date"])
    return df


def _monthly_rows(service: str, month_to_cost: dict[str, float]) -> list[dict[str, object]]:
    """One row per (service, month), placed on the 15th of each month."""
    return [_row(f"{month}-15", service, cost) for month, cost in month_to_cost.items()]


def _daily_rows(service: str, start: date, costs: list[float]) -> list[dict[str, object]]:
    """Consecutive daily rows starting from `start`."""
    return [
        _row((start + timedelta(days=i)).isoformat(), service, c)
        for i, c in enumerate(costs)
    ]


def _recs_by_id(recs: list[Recommendation], rule_id: str) -> list[Recommendation]:
    return [r for r in recs if r.id == rule_id]


# ---- Recommendation dataclass ---------------------------------------------

def test_recommendation_is_frozen() -> None:
    rec = Recommendation(
        id="x",
        title="t",
        service="s",
        category="spend_increase",
        reason="r",
        estimated_monthly_savings_usd=1.0,
        confidence="low",
    )
    with pytest.raises(FrozenInstanceError):
        rec.id = "y"  # type: ignore[misc]


def test_recommendation_defaults() -> None:
    rec = Recommendation(
        id="x", title="t", service="s", category="spend_increase",
        reason="r", estimated_monthly_savings_usd=0.0, confidence="low",
    )
    assert rec.required_evidence == ()
    assert rec.risks == ()
    assert rec.next_action == ""


# ---- Dispatcher basics ----------------------------------------------------

def test_generate_recommendations_empty_df_returns_empty() -> None:
    assert generate_recommendations(pd.DataFrame()) == []


def test_generate_recommendations_none_returns_empty() -> None:
    assert generate_recommendations(None) == []  # type: ignore[arg-type]


def test_generate_recommendations_missing_columns_raises() -> None:
    bad = pd.DataFrame({"usage_date": [pd.Timestamp("2026-01-01")]})
    with pytest.raises(ValueError, match="missing required columns"):
        generate_recommendations(bad)


# ---- Rule 1: unusual_increase ---------------------------------------------

def test_unusual_increase_detects_spike() -> None:
    rows = _monthly_rows("EC2", {"2026-01": 100, "2026-02": 100, "2026-03": 250})
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "unusual_increase")
    assert len(hits) == 1
    assert hits[0].service == "EC2"
    # prior mean = 100, current = 250 → delta = 150
    assert hits[0].estimated_monthly_savings_usd == pytest.approx(150.0)


def test_unusual_increase_ignores_small_growth() -> None:
    # Growth of 10% stays below the 1.30 threshold.
    rows = _monthly_rows("EC2", {"2026-01": 100, "2026-02": 100, "2026-03": 110})
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "unusual_increase") == []


def test_unusual_increase_requires_three_months() -> None:
    # Only two months: the loop starts at i=2, no candidate.
    rows = _monthly_rows("EC2", {"2026-01": 100, "2026-02": 500})
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "unusual_increase") == []


def test_unusual_increase_cap_keeps_top_n() -> None:
    # Five services, each with a distinct delta. The cap should keep 3.
    rows: list[dict[str, object]] = []
    for delta in (100, 200, 300, 400, 500):
        rows += _monthly_rows(
            f"Service-{delta}",
            {"2026-01": 100, "2026-02": 100, "2026-03": 100 + delta},
        )
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "unusual_increase")
    assert len(hits) == MAX_UNUSUAL_INCREASE_RESULTS
    # The three kept are the biggest deltas, in descending order.
    deltas = [r.estimated_monthly_savings_usd for r in hits]
    assert deltas == sorted(deltas, reverse=True)
    assert min(deltas) >= 300.0


def test_unusual_increase_zero_prior_mean_does_not_crash() -> None:
    # prior mean = 0 → the rule must skip cleanly, not divide by zero.
    rows = _monthly_rows("EC2", {"2026-01": 0, "2026-02": 0, "2026-03": 100})
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "unusual_increase") == []


# ---- Rule 2: step_change --------------------------------------------------

def test_step_change_detects_clear_step() -> None:
    # 30 days at 5, then 30 days at 50.
    costs = [5.0] * 30 + [50.0] * 30
    rows = _daily_rows("EC2", date(2026, 1, 1), costs)
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "step_change")
    assert len(hits) == 1
    assert hits[0].service == "EC2"
    assert hits[0].estimated_monthly_savings_usd > 0


def test_step_change_requires_enough_days() -> None:
    # 20 days total: less than 2 × STEP_CHANGE_MIN_DAYS (28).
    costs = [5.0] * 10 + [50.0] * 10
    rows = _daily_rows("EC2", date(2026, 1, 1), costs)
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "step_change") == []


def test_step_change_ignores_flat_series() -> None:
    # 60 days at the same value: no step.
    costs = [10.0] * 60
    rows = _daily_rows("EC2", date(2026, 1, 1), costs)
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "step_change") == []


# ---- Rule 3: concentration ------------------------------------------------

def test_concentration_flags_dominant_service() -> None:
    rows = (
        _monthly_rows("EC2", {"2026-01": 1000, "2026-02": 1000})
        + _monthly_rows("S3", {"2026-01": 200, "2026-02": 200})
        + _monthly_rows("RDS", {"2026-01": 200, "2026-02": 200})
    )
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "concentration")
    assert len(hits) == 1
    assert hits[0].service == "EC2"


def test_concentration_ignores_balanced_spend() -> None:
    rows = (
        _monthly_rows("EC2", {"2026-01": 100, "2026-02": 100})
        + _monthly_rows("S3", {"2026-01": 100, "2026-02": 100})
        + _monthly_rows("RDS", {"2026-01": 100, "2026-02": 100})
    )
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "concentration") == []


def test_concentration_savings_is_zero() -> None:
    rows = (
        _monthly_rows("EC2", {"2026-01": 1000, "2026-02": 1000})
        + _monthly_rows("S3", {"2026-01": 10, "2026-02": 10})
    )
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "concentration")
    assert len(hits) == 1
    assert hits[0].estimated_monthly_savings_usd == 0.0


# ---- Rule 4: daily_outlier ------------------------------------------------

def test_daily_outlier_detects_spike() -> None:
    # 29 days at 10, one spike at 100.
    costs = [10.0] * 29 + [100.0]
    rows = _daily_rows("EC2", date(2026, 1, 1), costs)
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "daily_outlier")
    assert len(hits) == 1
    assert hits[0].service == "EC2"


def test_daily_outlier_requires_enough_days() -> None:
    # 20 days only; below the 30-day floor.
    costs = [10.0] * 19 + [100.0]
    rows = _daily_rows("EC2", date(2026, 1, 1), costs)
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "daily_outlier") == []


def test_daily_outlier_ignores_constant_series() -> None:
    # std = 0 → no outlier possible.
    costs = [10.0] * 40
    rows = _daily_rows("EC2", date(2026, 1, 1), costs)
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "daily_outlier") == []


# ---- Rule 5: storage_review -----------------------------------------------

def test_storage_review_flags_s3() -> None:
    rows = _monthly_rows("Amazon Simple Storage Service",
                         {"2026-01": 100, "2026-02": 100})
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "storage_review")
    assert len(hits) == 1
    assert hits[0].service == "Amazon Simple Storage Service"
    # monthly = 100, cap = 15 % → 15.0
    assert hits[0].estimated_monthly_savings_usd == pytest.approx(15.0)


def test_storage_review_flags_ebs() -> None:
    rows = _monthly_rows("Amazon Elastic Block Store",
                         {"2026-01": 100, "2026-02": 100})
    recs = generate_recommendations(_df(rows))
    hits = _recs_by_id(recs, "storage_review")
    assert len(hits) == 1
    assert hits[0].service == "Amazon Elastic Block Store"


def test_storage_review_ignores_non_storage_service() -> None:
    rows = _monthly_rows("Amazon Elastic Compute Cloud - Compute",
                         {"2026-01": 100, "2026-02": 100})
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "storage_review") == []


def test_storage_review_ignores_small_cost() -> None:
    # monthly = 5, below the 10 USD floor.
    rows = _monthly_rows("Amazon Simple Storage Service",
                         {"2026-01": 5, "2026-02": 5})
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "storage_review") == []


def test_storage_review_requires_two_months() -> None:
    rows = _monthly_rows("Amazon Simple Storage Service", {"2026-01": 100})
    recs = generate_recommendations(_df(rows))
    assert _recs_by_id(recs, "storage_review") == []


# ---- Sorting --------------------------------------------------------------

def test_positive_savings_come_before_zero_savings() -> None:
    rows = (
        _monthly_rows("EC2", {"2026-01": 100, "2026-02": 100, "2026-03": 300})
        + _monthly_rows("S3", {"2026-01": 100, "2026-02": 100, "2026-03": 100})
    )
    recs = generate_recommendations(_df(rows))
    positive_indices = [
        i for i, r in enumerate(recs) if r.estimated_monthly_savings_usd > 0
    ]
    zero_indices = [
        i for i, r in enumerate(recs) if r.estimated_monthly_savings_usd == 0
    ]
    if positive_indices and zero_indices:
        assert max(positive_indices) < min(zero_indices)


# ---- Integration on the demo dataset --------------------------------------

def test_demo_file_produces_expected_signals() -> None:
    """The bundled demo dataset must produce the signals we designed
    into it: an EC2 spike, an EBS step, and an EC2 concentration."""
    df = import_csv_from_bytes(
        open("data/demo_costs.csv", "rb").read(), "demo.csv"
    ).df
    assert df is not None

    recs = generate_recommendations(df)
    assert len(recs) >= 5

    ui = _recs_by_id(recs, "unusual_increase")
    assert any(r.service == "Amazon Elastic Compute Cloud - Compute" for r in ui)

    steps = _recs_by_id(recs, "step_change")
    assert any(r.service == "Amazon Elastic Block Store" for r in steps)

    conc = _recs_by_id(recs, "concentration")
    assert len(conc) == 1
    assert conc[0].service == "Amazon Elastic Compute Cloud - Compute"

    storage = _recs_by_id(recs, "storage_review")
    assert len(storage) >= 1