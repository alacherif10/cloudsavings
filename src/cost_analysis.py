"""Pure cost analysis functions used by the dashboard.

Every function in this module takes a clean, typed DataFrame (as
produced by src.csv_import.import_csv_from_bytes) and returns a
DataFrame or dictionary ready to be displayed.

Design rules:

    - No Streamlit imports. No I/O. No global state.
    - Never mutate the input DataFrame.
    - Never raise on empty input; return an empty result instead.
    - All monetary results are floats, rounded to 2 decimals.
"""
from __future__ import annotations

import pandas as pd

# Number of decimal places we round money to.
_MONEY_DECIMALS: int = 2


# ---- Input validation (private) -------------------------------------------

_REQUIRED_COLS: frozenset[str] = frozenset({
    "usage_date", "service", "region", "usage_type",
    "usage_amount", "unit", "unblended_cost", "currency",
})


def _check_columns(df: pd.DataFrame) -> None:
    """Raise a clear error if the DataFrame is missing required columns."""
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"DataFrame is missing required columns: {sorted(missing)}"
        )


def _empty_cost_df() -> pd.DataFrame:
    """A zero-row DataFrame with the columns the dashboard expects."""
    return pd.DataFrame(columns=["month", "total_cost"])


# ---- Public API -----------------------------------------------------------

def monthly_totals(df: pd.DataFrame) -> pd.DataFrame:
    """Total cost per calendar month.

    Returns a DataFrame with columns `month` (Period[M]) and
    `total_cost` (float), sorted by month ascending.
    """
    if df is None or len(df) == 0:
        return _empty_cost_df()
    _check_columns(df)

    work = df.loc[:, ["usage_date", "unblended_cost"]].copy()
    work["month"] = work["usage_date"].dt.to_period("M")

    out = (
        work.groupby("month", as_index=False)["unblended_cost"]
        .sum()
        .rename(columns={"unblended_cost": "total_cost"})
        .sort_values("month")
        .reset_index(drop=True)
    )
    out["total_cost"] = out["total_cost"].round(_MONEY_DECIMALS)
    return out


def service_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Total cost per service, with percentage share of the grand total.

    Returns a DataFrame with columns `service`, `total_cost`,
    `share_pct`, sorted by `total_cost` descending.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["service", "total_cost", "share_pct"])
    _check_columns(df)

    work = df.loc[:, ["service", "unblended_cost"]].copy()

    out = (
        work.groupby("service", as_index=False)["unblended_cost"]
        .sum()
        .rename(columns={"unblended_cost": "total_cost"})
    )

    grand_total = out["total_cost"].sum()
    if grand_total > 0:
        out["share_pct"] = (out["total_cost"] / grand_total * 100).round(2)
    else:
        out["share_pct"] = 0.0

    out["total_cost"] = out["total_cost"].round(_MONEY_DECIMALS)
    return out.sort_values("total_cost", ascending=False).reset_index(drop=True)


def month_over_month_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Month-over-month change in total cost.

    Returns a DataFrame with columns `month`, `total_cost`,
    `previous_cost`, `change_abs`, `change_pct`. The first row has
    NaN for `previous_cost`, `change_abs` and `change_pct`.
    """
    monthly = monthly_totals(df)
    if len(monthly) == 0:
        return pd.DataFrame(columns=[
            "month", "total_cost", "previous_cost", "change_abs", "change_pct",
        ])

    monthly = monthly.copy()
    monthly["previous_cost"] = monthly["total_cost"].shift(1)
    monthly["change_abs"] = (monthly["total_cost"] - monthly["previous_cost"]).round(_MONEY_DECIMALS)
    monthly["change_pct"] = (
        (monthly["total_cost"] / monthly["previous_cost"] - 1) * 100
    ).round(2)
    return monthly


def daily_totals(df: pd.DataFrame) -> pd.DataFrame:
    """Total cost per calendar day.

    Returns a DataFrame with columns `date` (datetime64) and
    `total_cost` (float), sorted by date ascending.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["date", "total_cost"])
    _check_columns(df)

    work = df.loc[:, ["usage_date", "unblended_cost"]].copy()
    work["date"] = work["usage_date"].dt.normalize()

    out = (
        work.groupby("date", as_index=False)["unblended_cost"]
        .sum()
        .rename(columns={"unblended_cost": "total_cost"})
        .sort_values("date")
        .reset_index(drop=True)
    )
    out["total_cost"] = out["total_cost"].round(_MONEY_DECIMALS)
    return out


def service_monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot table of cost by (month, service) for stacked charts.

    Returns a wide DataFrame indexed by `month` (Period[M]), with one
    column per service. Missing (month, service) combinations are 0.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    _check_columns(df)

    work = df.loc[:, ["usage_date", "service", "unblended_cost"]].copy()
    work["month"] = work["usage_date"].dt.to_period("M")

    pivot = (
        work.groupby(["month", "service"])["unblended_cost"]
        .sum()
        .unstack(fill_value=0.0)
        .sort_index()
    )
    return pivot.round(_MONEY_DECIMALS)


def summary_stats(df: pd.DataFrame) -> dict[str, object]:
    """High-level numbers displayed in the dashboard's top row.

    Keys:
        total_cost          float
        mean_monthly_cost   float | None (None if fewer than one month)
        min_month_cost      float | None
        max_month_cost      float | None
        num_services        int
        num_days            int
        period_start        str | None (ISO date)
        period_end          str | None (ISO date)
    """
    empty: dict[str, object] = {
        "total_cost": 0.0,
        "mean_monthly_cost": None,
        "min_month_cost": None,
        "max_month_cost": None,
        "num_services": 0,
        "num_days": 0,
        "period_start": None,
        "period_end": None,
    }
    if df is None or len(df) == 0:
        return empty
    _check_columns(df)

    monthly = monthly_totals(df)

    return {
        "total_cost": float(round(df["unblended_cost"].sum(), _MONEY_DECIMALS)),
        "mean_monthly_cost": float(round(monthly["total_cost"].mean(), _MONEY_DECIMALS)),
        "min_month_cost": float(round(monthly["total_cost"].min(), _MONEY_DECIMALS)),
        "max_month_cost": float(round(monthly["total_cost"].max(), _MONEY_DECIMALS)),
        "num_services": int(df["service"].nunique()),
        "num_days": int(df["usage_date"].dt.normalize().nunique()),
        "period_start": df["usage_date"].min().date().isoformat(),
        "period_end": df["usage_date"].max().date().isoformat(),
    }