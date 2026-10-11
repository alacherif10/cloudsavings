"""Tests for src.cost_analysis.

Focus on the calculation rules, not on the shape of any particular
dataset. Inputs are small hand-built DataFrames; the numbers are
chosen so that the expected outputs can be checked by hand.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.cost_analysis import (
    daily_totals,
    month_over_month_changes,
    monthly_totals,
    service_breakdown,
    service_monthly_trend,
    summary_stats,
)


# ---- Helpers --------------------------------------------------------------

def _row(
    day: str,
    service: str,
    cost: float,
    *,
    region: str = "eu-west-1",
    usage_type: str = "BoxUsage",
    amount: float = 1.0,
    unit: str = "Hrs",
    currency: str = "USD",
) -> dict[str, object]:
    return {
        "usage_date": pd.Timestamp(day),
        "service": service,
        "region": region,
        "usage_type": usage_type,
        "usage_amount": amount,
        "unit": unit,
        "unblended_cost": cost,
        "currency": currency,
    }


def _df(rows: list[dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["usage_date"] = pd.to_datetime(df["usage_date"])
    return df


@pytest.fixture
def two_months_df() -> pd.DataFrame:
    """Four rows spanning April and May 2026.

    May has one EC2 row at 30 and one S3 row at 10 → 40 total.
    April has one EC2 row at 20 and one S3 row at 5  → 25 total.
    """
    return _df([
        _row("2026-04-01", "EC2", 20.0),
        _row("2026-04-15", "S3",  5.0),
        _row("2026-05-01", "EC2", 30.0),
        _row("2026-05-20", "S3",  10.0),
    ])


# ---- monthly_totals -------------------------------------------------------

def test_monthly_totals_counts_and_sums(two_months_df: pd.DataFrame) -> None:
    result = monthly_totals(two_months_df)
    assert list(result.columns) == ["month", "total_cost"]
    assert len(result) == 2
    assert result["total_cost"].tolist() == [25.0, 40.0]


def test_monthly_totals_is_sorted_ascending(two_months_df: pd.DataFrame) -> None:
    result = monthly_totals(two_months_df)
    months = result["month"].tolist()
    assert months == sorted(months)


def test_monthly_totals_empty_input() -> None:
    result = monthly_totals(pd.DataFrame())
    assert list(result.columns) == ["month", "total_cost"]
    assert len(result) == 0


def test_monthly_totals_none_input() -> None:
    result = monthly_totals(None)  # type: ignore[arg-type]
    assert len(result) == 0


def test_monthly_totals_rounds_to_two_decimals() -> None:
    df = _df([_row("2026-04-01", "EC2", 1.005), _row("2026-04-02", "EC2", 1.005)])
    result = monthly_totals(df)
    # 1.005 + 1.005 = 2.01 (round-half-to-even at the second decimal)
    assert result["total_cost"].iloc[0] == pytest.approx(2.01, abs=0.01)


def test_monthly_totals_does_not_mutate_input(two_months_df: pd.DataFrame) -> None:
    snapshot = two_months_df.copy(deep=True)
    monthly_totals(two_months_df)
    pd.testing.assert_frame_equal(two_months_df, snapshot)


# ---- service_breakdown ----------------------------------------------------

def test_service_breakdown_sums_per_service(two_months_df: pd.DataFrame) -> None:
    result = service_breakdown(two_months_df)
    assert list(result.columns) == ["service", "total_cost", "share_pct"]
    totals = dict(zip(result["service"], result["total_cost"]))
    assert totals["EC2"] == 50.0
    assert totals["S3"] == 15.0


def test_service_breakdown_sorted_by_cost_desc(two_months_df: pd.DataFrame) -> None:
    result = service_breakdown(two_months_df)
    costs = result["total_cost"].tolist()
    assert costs == sorted(costs, reverse=True)


def test_service_breakdown_share_pct_sums_to_100(two_months_df: pd.DataFrame) -> None:
    result = service_breakdown(two_months_df)
    assert result["share_pct"].sum() == pytest.approx(100.0, abs=0.01)


def test_service_breakdown_share_pct_correct(two_months_df: pd.DataFrame) -> None:
    # Total = 65. EC2 = 50 → 76.92 %. S3 = 15 → 23.08 %.
    result = service_breakdown(two_months_df)
    shares = dict(zip(result["service"], result["share_pct"]))
    assert shares["EC2"] == pytest.approx(76.92, abs=0.01)
    assert shares["S3"] == pytest.approx(23.08, abs=0.01)


def test_service_breakdown_empty_input() -> None:
    result = service_breakdown(pd.DataFrame())
    assert list(result.columns) == ["service", "total_cost", "share_pct"]
    assert len(result) == 0


def test_service_breakdown_zero_cost_gives_zero_share() -> None:
    df = _df([_row("2026-04-01", "EC2", 0.0), _row("2026-04-01", "S3", 0.0)])
    result = service_breakdown(df)
    assert (result["share_pct"] == 0.0).all()


# ---- month_over_month_changes ---------------------------------------------

def test_mom_first_row_has_nan(two_months_df: pd.DataFrame) -> None:
    result = month_over_month_changes(two_months_df)
    first = result.iloc[0]
    assert pd.isna(first["previous_cost"])
    assert pd.isna(first["change_abs"])
    assert pd.isna(first["change_pct"])


def test_mom_second_row_computes_change(two_months_df: pd.DataFrame) -> None:
    result = month_over_month_changes(two_months_df)
    second = result.iloc[1]
    # April = 25, May = 40 → +15 abs, +60 % pct
    assert second["previous_cost"] == pytest.approx(25.0)
    assert second["change_abs"] == pytest.approx(15.0)
    assert second["change_pct"] == pytest.approx(60.0, abs=0.01)


def test_mom_empty_input() -> None:
    result = month_over_month_changes(pd.DataFrame())
    assert len(result) == 0


# ---- daily_totals ---------------------------------------------------------

def test_daily_totals_groups_by_day(two_months_df: pd.DataFrame) -> None:
    # May 1 has one row (30), April 1 has one (20), etc. All 4 days distinct.
    result = daily_totals(two_months_df)
    assert list(result.columns) == ["date", "total_cost"]
    assert len(result) == 4


def test_daily_totals_sums_multiple_rows_same_day() -> None:
    df = _df([
        _row("2026-04-01", "EC2", 1.0),
        _row("2026-04-01", "S3", 2.0),
        _row("2026-04-01", "Lambda", 3.0),
    ])
    result = daily_totals(df)
    assert len(result) == 1
    assert result["total_cost"].iloc[0] == pytest.approx(6.0)


def test_daily_totals_sorted_by_date() -> None:
    df = _df([
        _row("2026-05-01", "EC2", 1.0),
        _row("2026-04-01", "EC2", 2.0),
        _row("2026-06-01", "EC2", 3.0),
    ])
    result = daily_totals(df)
    dates = result["date"].tolist()
    assert dates == sorted(dates)


def test_daily_totals_empty_input() -> None:
    result = daily_totals(pd.DataFrame())
    assert list(result.columns) == ["date", "total_cost"]
    assert len(result) == 0


# ---- service_monthly_trend ------------------------------------------------

def test_service_monthly_trend_shape(two_months_df: pd.DataFrame) -> None:
    result = service_monthly_trend(two_months_df)
    assert len(result) == 2  # two months
    assert set(result.columns) == {"EC2", "S3"}


def test_service_monthly_trend_values(two_months_df: pd.DataFrame) -> None:
    result = service_monthly_trend(two_months_df)
    assert result.loc[pd.Period("2026-04", "M"), "EC2"] == pytest.approx(20.0)
    assert result.loc[pd.Period("2026-05", "M"), "EC2"] == pytest.approx(30.0)
    assert result.loc[pd.Period("2026-04", "M"), "S3"] == pytest.approx(5.0)


def test_service_monthly_trend_missing_combinations_are_zero() -> None:
    # EC2 appears only in April; S3 only in May.
    df = _df([
        _row("2026-04-01", "EC2", 10.0),
        _row("2026-05-01", "S3", 20.0),
    ])
    result = service_monthly_trend(df)
    assert result.loc[pd.Period("2026-04", "M"), "S3"] == 0.0
    assert result.loc[pd.Period("2026-05", "M"), "EC2"] == 0.0


def test_service_monthly_trend_empty_input() -> None:
    result = service_monthly_trend(pd.DataFrame())
    assert len(result) == 0


# ---- summary_stats --------------------------------------------------------

def test_summary_stats_contains_all_keys(two_months_df: pd.DataFrame) -> None:
    result = summary_stats(two_months_df)
    expected_keys = {
        "total_cost", "mean_monthly_cost", "min_month_cost", "max_month_cost",
        "num_services", "num_days", "period_start", "period_end",
    }
    assert set(result.keys()) == expected_keys


def test_summary_stats_values(two_months_df: pd.DataFrame) -> None:
    result = summary_stats(two_months_df)
    # Total = 65. Two months: April = 25, May = 40. Mean = 32.5.
    assert result["total_cost"] == pytest.approx(65.0)
    assert result["min_month_cost"] == pytest.approx(25.0)
    assert result["max_month_cost"] == pytest.approx(40.0)
    assert result["mean_monthly_cost"] == pytest.approx(32.5)
    assert result["num_services"] == 2
    assert result["num_days"] == 4
    assert result["period_start"] == "2026-04-01"
    assert result["period_end"] == "2026-05-20"


def test_summary_stats_empty_input() -> None:
    result = summary_stats(pd.DataFrame())
    assert result["total_cost"] == 0.0
    assert result["num_services"] == 0
    assert result["num_days"] == 0
    assert result["period_start"] is None
    assert result["period_end"] is None


def test_summary_stats_missing_columns_raises() -> None:
    bad = pd.DataFrame({"foo": [1, 2, 3]})
    with pytest.raises(ValueError, match="missing required columns"):
        summary_stats(bad)


# ---- integration with the demo file ---------------------------------------

def test_demo_file_produces_expected_totals() -> None:
    """Sanity check against the dataset generated in Phase 2.

    The exact numbers depend on the generator's seed, but the shape and
    the total must remain stable. If this test breaks, either the
    generator or the aggregation logic changed — both are worth a look.
    """
    from src.csv_import import import_csv_from_bytes

    df = import_csv_from_bytes(
        open("data/demo_costs.csv", "rb").read(), "demo.csv"
    ).df
    assert df is not None
    stats = summary_stats(df)
    assert stats["num_services"] == 8
    assert stats["num_days"] == 183
    assert stats["period_start"] == "2026-04-01"
    assert stats["period_end"] == "2026-09-30"
    assert stats["total_cost"] == pytest.approx(20840.52, abs=0.5)