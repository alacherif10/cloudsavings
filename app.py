"""CloudSavings — Streamlit dashboard.

This is the entry point of the local MVP. It loads the synthetic demo
dataset (data/demo_costs.csv) and renders the cost dashboard.

Run locally with:

    streamlit run app.py

Design rules:

    - All heavy lifting (parsing, validation, aggregation) lives in src/.
      This file only orchestrates display.
    - No network calls, no AWS, no paid services.
    - Synthetic data is clearly labelled at the top of every page.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.cost_analysis import (
    daily_totals,
    month_over_month_changes,
    monthly_totals,
    service_breakdown,
    summary_stats,
)
from src.csv_import import import_csv_from_bytes

# ---- Configuration --------------------------------------------------------

st.set_page_config(
    page_title="CloudSavings",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEMO_DATA_PATH = PROJECT_ROOT / "data" / "demo_costs.csv"


# ---- Data loading (cached) ------------------------------------------------

@st.cache_data(show_spinner="Loading demo data...")
def _load_demo_data() -> pd.DataFrame:
    """Read the synthetic demo CSV through our own import pipeline.

    Reusing the exact same code path as a real upload means that what
    we see in the demo is what a user would see with a real file.
    """
    data = DEMO_DATA_PATH.read_bytes()
    result = import_csv_from_bytes(data, "demo_costs.csv")
    if not result.is_success or result.df is None:
        # The bundled demo file is our responsibility — if it fails,
        # we want the developer to see why, not the end user.
        raise RuntimeError(
            f"Failed to load bundled demo data: "
            f"{result.fatal_error or [str(e) for e in result.validation.errors]}"
        )
    return result.df


# ---- Layout helpers -------------------------------------------------------

def _render_synthetic_banner() -> None:
    st.warning(
        "**SIMULATED DATA** — This dashboard is currently running on the "
        "synthetic demonstration dataset shipped with the repository. "
        "The figures below are not real AWS costs. "
        "See `data/README.md` for details.",
        icon="⚠️",
    )


def _render_header() -> None:
    st.title("💰 CloudSavings")
    st.caption(
        "Cloud cost optimization, as a service. "
        "Local MVP — not production-ready."
    )


def _render_kpi_row(stats: dict[str, object]) -> None:
    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Total cost",
        f"${stats['total_cost']:,.2f}",
        help="Sum of unblended_cost across all rows.",
    )

    mean_monthly = stats["mean_monthly_cost"]
    col2.metric(
        "Mean monthly cost",
        f"${mean_monthly:,.2f}" if mean_monthly is not None else "—",
        help="Average of the monthly totals.",
    )

    period_start = stats["period_start"]
    period_end = stats["period_end"]
    if period_start and period_end:
        period_display = f"{period_start} → {period_end}"
    else:
        period_display = "—"
    col3.metric("Period covered", period_display, help="Inclusive date range.")

    col4.metric(
        "Services",
        f"{stats['num_services']}",
        help="Distinct AWS services found in the dataset.",
    )


def _render_monthly_chart(df: pd.DataFrame) -> None:
    st.subheader("Monthly spending")
    monthly = monthly_totals(df)
    monthly["month_str"] = monthly["month"].astype(str)

    fig = px.bar(
        monthly,
        x="month_str",
        y="total_cost",
        labels={"month_str": "Month", "total_cost": "Cost (USD)"},
        text="total_cost",
    )
    fig.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
    fig.update_layout(
        yaxis_tickprefix="$",
        yaxis_tickformat=",.0f",
        height=380,
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_daily_chart(df: pd.DataFrame) -> None:
    st.subheader("Daily spending")
    daily = daily_totals(df)

    fig = px.line(
        daily,
        x="date",
        y="total_cost",
        labels={"date": "Date", "total_cost": "Cost (USD)"},
    )
    fig.update_layout(
        yaxis_tickprefix="$",
        yaxis_tickformat=",.0f",
        height=320,
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_service_breakdown(df: pd.DataFrame) -> None:
    st.subheader("Spending by service")
    breakdown = service_breakdown(df)

    left, right = st.columns([1, 1])

    with left:
        fig = px.pie(
            breakdown,
            names="service",
            values="total_cost",
            hole=0.45,
        )
        fig.update_traces(
            textposition="outside",
            textinfo="percent+label",
        )
        fig.update_layout(
            showlegend=False,
            height=380,
            margin=dict(l=10, r=10, t=20, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    with right:
        display = breakdown.copy()
        display["total_cost"] = display["total_cost"].map(lambda x: f"${x:,.2f}")
        display["share_pct"] = display["share_pct"].map(lambda x: f"{x:.2f}%")
        display = display.rename(columns={
            "service": "Service",
            "total_cost": "Total cost",
            "share_pct": "Share",
        })
        st.dataframe(display, use_container_width=True, hide_index=True)


def _render_mom_table(df: pd.DataFrame) -> None:
    st.subheader("Month-over-month changes")
    mom = month_over_month_changes(df)
    if len(mom) == 0:
        st.info("No monthly data to display.")
        return

    display = mom.copy()
    display["month"] = display["month"].astype(str)
    display["total_cost"] = display["total_cost"].map(lambda x: f"${x:,.2f}")
    display["previous_cost"] = display["previous_cost"].map(
        lambda x: f"${x:,.2f}" if pd.notna(x) else "—"
    )
    display["change_abs"] = display["change_abs"].map(
        lambda x: f"{'+' if x >= 0 else ''}${x:,.2f}" if pd.notna(x) else "—"
    )
    display["change_pct"] = display["change_pct"].map(
        lambda x: f"{'+' if x >= 0 else ''}{x:.2f}%" if pd.notna(x) else "—"
    )
    display = display.rename(columns={
        "month": "Month",
        "total_cost": "Total",
        "previous_cost": "Previous",
        "change_abs": "Δ abs",
        "change_pct": "Δ %",
    })
    st.dataframe(display, use_container_width=True, hide_index=True)


def _render_sidebar(stats: dict[str, object]) -> None:
    with st.sidebar:
        st.header("Data source")
        st.markdown(
            "**Synthetic demonstration dataset**  \n"
            "`data/demo_costs.csv`"
        )
        st.markdown(
            "This dataset is generated by "
            "`scripts/generate_demo_data.py` with a fixed seed. "
            "It is **not** real AWS cost data."
        )

        st.divider()
        st.header("Coverage")
        st.markdown(
            f"- **Services**: {stats['num_services']}\n"
            f"- **Days**: {stats['num_days']}\n"
            f"- **From**: {stats['period_start']}\n"
            f"- **To**: {stats['period_end']}"
        )

        st.divider()
        st.header("Roadmap")
        st.markdown(
            "- ✅ Cost dashboard (this screen)\n"
            "- ⬜ CSV upload\n"
            "- ⬜ Recommendations\n"
            "- ⬜ Savings measurement\n"
            "- ⬜ RaaS fee simulator\n"
            "- ⬜ PDF/CSV reports"
        )

        st.divider()
        st.caption(
            "Local MVP. No AWS account connected. "
            "No data leaves this machine."
        )


def _render_footer() -> None:
    st.divider()
    st.caption(
        "CloudSavings MVP · Runs fully offline · "
        "Estimated and verified savings will always be clearly distinguished."
    )


# ---- Main ------------------------------------------------------------------

def main() -> None:
    _render_header()
    _render_synthetic_banner()

    try:
        df = _load_demo_data()
    except RuntimeError as exc:
        st.error(
            "The bundled demo dataset could not be loaded. "
            "This is a developer issue, not a user issue. "
            f"Details: {exc}"
        )
        st.stop()

    stats = summary_stats(df)
    _render_sidebar(stats)
    _render_kpi_row(stats)

    st.divider()
    _render_monthly_chart(df)

    st.divider()
    _render_daily_chart(df)

    st.divider()
    _render_service_breakdown(df)

    st.divider()
    _render_mom_table(df)

    _render_footer()


if __name__ == "__main__":
    main()