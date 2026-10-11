"""CloudSavings — Streamlit dashboard.

This is the entry point of the local MVP. It supports two data
sources:

    - The bundled synthetic demo dataset (data/demo_costs.csv),
      loaded by default. Clearly labelled as simulated.
    - A user-uploaded CSV file, following the format documented in
      docs/csv_format.md. Always previewed before being applied.

Run locally with:

    streamlit run app.py

Design rules:

    - All heavy lifting (parsing, validation, aggregation) lives in src/.
      This file only orchestrates display.
    - No network calls, no AWS, no paid services.
    - Synthetic data is clearly labelled at the top of every page.
    - Uploaded files are never written to disk.
    - Estimated savings and verified savings are never conflated.
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
from src.csv_import import ImportResult, import_csv_from_bytes, preview_rows
from src.recommendations import Recommendation, generate_recommendations

# ---- Configuration --------------------------------------------------------

st.set_page_config(
    page_title="CloudSavings",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEMO_DATA_PATH = PROJECT_ROOT / "data" / "demo_costs.csv"
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "sample_costs.csv"

MAX_ERRORS_DISPLAYED = 100
MAX_PREVIEW_ROWS = 20
MAX_RECOMMENDATIONS_DISPLAYED = 20

# Confidence badge colours (Streamlit uses markdown colour syntax).
_CONFIDENCE_BADGE = {
    "low":    "🟡 low",
    "medium": "🟠 medium",
    "high":   "🟢 high",
}

# Human-readable label per recommendation category.
_CATEGORY_LABEL = {
    "spend_increase":  "Spend increase",
    "step_change":     "Step change",
    "concentration":   "Concentration",
    "daily_outlier":   "Daily outlier",
    "storage_review":  "Storage review",
}


# ---- Demo data (cached) ---------------------------------------------------

@st.cache_data(show_spinner="Loading demo data...")
def _load_demo_data() -> pd.DataFrame:
    """Read the bundled synthetic demo CSV through our own pipeline."""
    data = DEMO_DATA_PATH.read_bytes()
    result = import_csv_from_bytes(data, "demo_costs.csv")
    if not result.is_success or result.df is None:
        raise RuntimeError(
            f"Failed to load bundled demo data: "
            f"{result.fatal_error or [str(e) for e in result.validation.errors]}"
        )
    return result.df


@st.cache_data(show_spinner=False)
def _cached_recommendations(df: pd.DataFrame) -> tuple[Recommendation, ...]:
    """Cache the recommendation list so we do not recompute on every rerun.

    Streamlit's cache hashes the DataFrame; the result is a tuple because
    cached values must be hashable in some Streamlit versions, and tuples
    are safe. Callers convert back to list.
    """
    return tuple(generate_recommendations(df))


# ---- Session state --------------------------------------------------------

def _init_session_state() -> None:
    if "active_name" not in st.session_state:
        st.session_state.active_name = None
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0


# ---- Sidebar --------------------------------------------------------------

def _render_sidebar_upload() -> tuple[bytes | None, str | None]:
    with st.sidebar:
        st.header("Data source")
        st.caption("Demo dataset is loaded by default.")

        uploaded = st.file_uploader(
            "Upload your own AWS cost CSV",
            type=["csv"],
            key=f"uploader_{st.session_state.uploader_key}",
            help="Required format: see docs/csv_format.md",
        )

        on_demo = st.session_state.active_name is None

        if st.button(
            "↩️ Back to demo data",
            disabled=on_demo,
            use_container_width=True,
        ):
            st.session_state.active_name = None
            st.session_state.uploader_key += 1
            st.rerun()

        if SAMPLE_DATA_PATH.exists():
            st.download_button(
                "📥 Download sample CSV",
                data=SAMPLE_DATA_PATH.read_bytes(),
                file_name="sample_costs.csv",
                mime="text/csv",
                use_container_width=True,
                help="A minimal valid file you can use to test the upload.",
            )

    if uploaded is None:
        return None, None
    return uploaded.getvalue(), uploaded.name


def _render_sidebar_info(stats: dict[str, object], source_name: str) -> None:
    with st.sidebar:
        st.divider()
        st.header("Current source")
        st.markdown(f"`{source_name}`")

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
            "- ✅ Cost dashboard\n"
            "- ✅ CSV upload\n"
            "- ✅ Recommendations\n"
            "- ⬜ Savings measurement\n"
            "- ⬜ RaaS fee simulator\n"
            "- ⬜ PDF/CSV reports"
        )

        st.divider()
        st.caption(
            "Local MVP. No AWS account connected. "
            "No data leaves this machine."
        )


# ---- Banners --------------------------------------------------------------

def _render_synthetic_banner() -> None:
    st.warning(
        "**SIMULATED DATA** — This dashboard is currently running on the "
        "synthetic demonstration dataset shipped with the repository. "
        "The figures below are not real AWS costs. "
        "See `data/README.md` for details.",
        icon="⚠️",
    )


def _render_uploaded_banner(source_name: str, num_rows: int) -> None:
    st.success(
        f"**UPLOADED DATA** — Showing figures computed from "
        f"`{source_name}` ({num_rows:,} rows). "
        f"No data has left this machine.",
        icon="✅",
    )


# ---- Upload preview and errors --------------------------------------------

def _render_import_errors(result: ImportResult, filename: str) -> None:
    st.error(f"### Could not import `{filename}`")

    if result.fatal_error:
        st.error(result.fatal_error)
        st.info(
            "Nothing was imported. Fix the file and try again. "
            "See `docs/csv_format.md` for the required format, "
            "or download a valid sample from the sidebar."
        )
        return

    errors = result.validation.errors
    st.warning(
        f"**{len(errors)} problem(s) found.** "
        f"The file was not imported — no partial data was applied. "
        f"Fix the issues below and re-upload."
    )

    shown = errors[:MAX_ERRORS_DISPLAYED]
    rows = [
        {"Row": e.row, "Column": e.column, "Message": e.message, "Value": e.value}
        for e in shown
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if len(errors) > MAX_ERRORS_DISPLAYED:
        st.caption(
            f"Showing the first {MAX_ERRORS_DISPLAYED} of {len(errors)} "
            f"errors. Fix these and re-upload to see the rest."
        )

    st.info("Download a valid sample from the sidebar to compare formats.")


def _render_upload_preview(result: ImportResult, filename: str) -> None:
    assert result.df is not None
    df = result.df

    st.subheader("Preview of uploaded file")
    st.caption(
        f"File: `{filename}` — {len(df):,} rows, "
        f"{df['usage_date'].min().date()} to "
        f"{df['usage_date'].max().date()}"
    )

    try:
        preview_stats = summary_stats(df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total cost", f"${preview_stats['total_cost']:,.2f}")
        c2.metric("Services", f"{preview_stats['num_services']}")
        c3.metric("Days", f"{preview_stats['num_days']}")
        mean_monthly = preview_stats["mean_monthly_cost"]
        c4.metric(
            "Mean / month",
            f"${mean_monthly:,.2f}" if mean_monthly is not None else "—",
        )
    except ValueError:
        pass

    st.markdown(f"**First {MAX_PREVIEW_ROWS} rows:**")
    st.dataframe(
        preview_rows(df, max_rows=MAX_PREVIEW_ROWS),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button(
            "✅ Use this data",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.active_name = filename
            st.rerun()
    with c2:
        st.caption(
            "Nothing has been applied yet. "
            "Click **Use this data** to load it into the dashboard, "
            "or upload a different file."
        )


# ---- Dashboard renderers --------------------------------------------------

def _render_header() -> None:
    st.title("💰 CloudSavings")
    st.caption(
        "Cloud cost optimization, as a service. "
        "Local MVP — not production-ready."
    )


def _render_kpi_row(stats: dict[str, object]) -> None:
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total cost", f"${stats['total_cost']:,.2f}",
                help="Sum of unblended_cost across all rows.")

    mean_monthly = stats["mean_monthly_cost"]
    col2.metric(
        "Mean monthly cost",
        f"${mean_monthly:,.2f}" if mean_monthly is not None else "—",
        help="Average of the monthly totals.",
    )

    period_start = stats["period_start"]
    period_end = stats["period_end"]
    period_display = (
        f"{period_start} → {period_end}" if period_start and period_end else "—"
    )
    col3.metric("Period covered", period_display, help="Inclusive date range.")

    col4.metric("Services", f"{stats['num_services']}",
                help="Distinct AWS services found in the dataset.")


def _render_recommendations_kpi(recommendations: list[Recommendation]) -> None:
    """Fifth KPI row: total estimated monthly savings from recommendations."""
    total = sum(r.estimated_monthly_savings_usd for r in recommendations)
    count = len(recommendations)
    with_positive = sum(
        1 for r in recommendations if r.estimated_monthly_savings_usd > 0
    )

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Potential monthly savings",
        f"${total:,.2f}",
        help="Sum of estimated monthly savings across all recommendations. "
             "These are ESTIMATES, not verified savings.",
    )
    c2.metric(
        "Recommendations",
        f"{count}",
        help="Total number of recommendations produced by the engine.",
    )
    c3.metric(
        "With estimated savings",
        f"{with_positive}",
        help="Recommendations that carry a non-zero estimated saving.",
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
        fig = px.pie(breakdown, names="service", values="total_cost", hole=0.45)
        fig.update_traces(textposition="outside", textinfo="percent+label")
        fig.update_layout(
            showlegend=False, height=380,
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


# ---- Recommendations section ----------------------------------------------

def _render_recommendations_banner() -> None:
    st.info(
        "These recommendations are **automated estimates** produced from "
        "cost data alone. They are **not** verified savings, and they are "
        "**not** proof that any resource is unused or wasted. Each one "
        "lists the additional evidence required before any action is "
        "taken. No cloud resource is ever modified automatically.",
        icon="ℹ️",
    )


def _render_recommendation_card(rec: Recommendation, index: int) -> None:
    """Render one recommendation as an expandable card."""
    badge = _CONFIDENCE_BADGE.get(rec.confidence, rec.confidence)
    category_label = _CATEGORY_LABEL.get(rec.category, rec.category)
    saving = rec.estimated_monthly_savings_usd

    header = (
        f"**{index}. {rec.title}**  ·  `{category_label}`  ·  "
        f"confidence: {badge}"
    )
    if saving > 0:
        header += f"  ·  est. **${saving:,.2f}/month**"
    else:
        header += "  ·  no direct saving estimate"

    with st.expander(header, expanded=(index <= 3)):
        st.markdown(f"**Service:** `{rec.service}`")
        st.markdown("**Why we are surfacing this:**")
        st.markdown(rec.reason)

        if saving > 0:
            st.caption(
                f"Estimated potential saving: **${saving:,.2f}/month**. "
                f"This is an estimate — a ceiling, not a promise."
            )

        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**Evidence required before acting**")
            if rec.required_evidence:
                for item in rec.required_evidence:
                    st.markdown(f"- {item}")
            else:
                st.markdown("_None specified._")

            st.markdown("**Risks / prerequisites**")
            if rec.risks:
                for item in rec.risks:
                    st.markdown(f"- {item}")
            else:
                st.markdown("_None specified._")

        with col_b:
            st.markdown("**Suggested next action**")
            if rec.next_action:
                st.markdown(rec.next_action)
            else:
                st.markdown("_None specified._")

            st.markdown("**Rule id**")
            st.code(rec.id, language="text")


def _render_recommendations(recommendations: list[Recommendation]) -> None:
    st.subheader("Recommendations")

    if not recommendations:
        st.success(
            "No recommendations were produced for this dataset. "
            "That is not a guarantee that the account is optimal — "
            "only that our current rules found nothing to flag.",
            icon="✅",
        )
        return

    _render_recommendations_banner()

    if len(recommendations) > MAX_RECOMMENDATIONS_DISPLAYED:
        st.caption(
            f"Showing the first {MAX_RECOMMENDATIONS_DISPLAYED} of "
            f"{len(recommendations)} recommendations."
        )

    for i, rec in enumerate(
        recommendations[:MAX_RECOMMENDATIONS_DISPLAYED], start=1
    ):
        _render_recommendation_card(rec, i)


def _render_footer() -> None:
    st.divider()
    st.caption(
        "CloudSavings MVP · Runs fully offline · "
        "Estimated and verified savings will always be clearly distinguished."
    )


# ---- Main ------------------------------------------------------------------

def main() -> None:
    _init_session_state()
    _render_header()

    file_bytes, filename = _render_sidebar_upload()

    if file_bytes is None:
        st.session_state.active_name = None
        df = _load_demo_data()
        source_name = "Demo dataset (synthetic)"
        is_synthetic = True
    else:
        result = import_csv_from_bytes(file_bytes, filename)

        if not result.is_success:
            _render_import_errors(result, filename)
            st.stop()

        if st.session_state.active_name != filename:
            _render_upload_preview(result, filename)
            st.stop()

        assert result.df is not None
        df = result.df
        source_name = filename
        is_synthetic = False

    if is_synthetic:
        _render_synthetic_banner()
    else:
        _render_uploaded_banner(source_name, len(df))

    stats = summary_stats(df)
    _render_sidebar_info(stats, source_name)

    _render_kpi_row(stats)

    recommendations = list(_cached_recommendations(df))
    st.divider()
    _render_recommendations_kpi(recommendations)

    st.divider()
    _render_monthly_chart(df)

    st.divider()
    _render_daily_chart(df)

    st.divider()
    _render_service_breakdown(df)

    st.divider()
    _render_mom_table(df)

    st.divider()
    _render_recommendations(recommendations)

    _render_footer()


if __name__ == "__main__":
    main()