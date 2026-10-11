"""CloudSavings — Streamlit dashboard.

Two tabs:
    1. Dashboard           — cost analysis + recommendations
    2. Savings simulator   — measurement + RaaS fee illustration

Data sources (chosen in the sidebar):
    - Bundled synthetic demo (data/demo_costs.csv), clearly labelled.
    - A user-uploaded CSV following docs/csv_format.md.

Run locally with:

    streamlit run app.py

Design rules:

    - All heavy lifting lives in src/. This file only orchestrates display.
    - No network calls, no AWS, no paid services.
    - Synthetic data is clearly labelled.
    - Uploaded files are never written to disk.
    - Estimated, observed, verified and billable savings are never conflated.
    - No real invoice is ever produced.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.billing import MAX_FEE_PCT, MIN_FEE_PCT, simulate_fee
from src.cost_analysis import (
    daily_totals,
    month_over_month_changes,
    monthly_totals,
    service_breakdown,
    summary_stats,
)
from src.csv_import import ImportResult, import_csv_from_bytes, preview_rows
from src.recommendations import Recommendation, generate_recommendations
from src.savings import MIN_WINDOW_DAYS, ApprovedIntervention, measure_savings

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
DEFAULT_FEE_PCT = 20.0

_CONFIDENCE_BADGE = {
    "low":    "🟡 low",
    "medium": "🟠 medium",
    "high":   "🟢 high",
}

_CATEGORY_LABEL = {
    "spend_increase": "Spend increase",
    "step_change":    "Step change",
    "concentration":  "Concentration",
    "daily_outlier":  "Daily outlier",
    "storage_review": "Storage review",
}


# ---- Demo data (cached) ---------------------------------------------------

@st.cache_data(show_spinner="Loading demo data...")
def _load_demo_data() -> pd.DataFrame:
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
            "- ✅ Savings measurement\n"
            "- ✅ RaaS fee simulator\n"
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


# ---- Recommendations ------------------------------------------------------

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

    with st.expander(header, expanded=False):
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


# ---- Savings simulator tab ------------------------------------------------

def _slice_by_date(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Return rows whose usage_date falls within [start, end] inclusive."""
    mask = (
        (df["usage_date"].dt.date >= start)
        & (df["usage_date"].dt.date <= end)
    )
    return df.loc[mask].copy()


def _render_savings_tab(
    df: pd.DataFrame,
    recommendations: list[Recommendation],
) -> None:
    st.subheader("Savings measurement")
    st.caption(
        "Compare a **baseline** period (before) to an **after** period "
        "to see whether a cost reduction actually occurred, and whether "
        "it can be attributed to an approved intervention."
    )

    min_date = df["usage_date"].min().date()
    max_date = df["usage_date"].max().date()
    total_days = (max_date - min_date).days

    if total_days < 2 * MIN_WINDOW_DAYS:
        st.warning(
            f"The loaded dataset spans only {total_days} days. "
            f"At least {2 * MIN_WINDOW_DAYS} days are required to "
            f"define two measurement windows. Load a larger dataset "
            f"to use this feature."
        )
        return

    # ---- Period selection --------------------------------------------------
    default_third = total_days // 3
    default_baseline_start = min_date
    default_baseline_end = min_date + timedelta(days=default_third)
    default_after_start = max_date - timedelta(days=default_third)
    default_after_end = max_date

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Baseline period (reference)**")
        baseline_start = st.date_input(
            "Baseline start",
            value=default_baseline_start,
            min_value=min_date,
            max_value=max_date,
            key="savings_baseline_start",
        )
        baseline_end = st.date_input(
            "Baseline end",
            value=default_baseline_end,
            min_value=min_date,
            max_value=max_date,
            key="savings_baseline_end",
        )
    with c2:
        st.markdown("**After period (measurement)**")
        after_start = st.date_input(
            "After start",
            value=default_after_start,
            min_value=min_date,
            max_value=max_date,
            key="savings_after_start",
        )
        after_end = st.date_input(
            "After end",
            value=default_after_end,
            min_value=min_date,
            max_value=max_date,
            key="savings_after_end",
        )

    if baseline_start > baseline_end:
        st.error("Baseline start is after baseline end.")
        return
    if after_start > after_end:
        st.error("After start is after after end.")
        return
    if baseline_end >= after_start:
        st.warning(
            "The baseline and after periods overlap. The measurement "
            "will still be computed, but the result should be interpreted "
            "with caution."
        )

    baseline_df = _slice_by_date(df, baseline_start, baseline_end)
    after_df = _slice_by_date(df, after_start, after_end)

    st.caption(
        f"Baseline: {len(baseline_df):,} rows  ·  "
        f"After: {len(after_df):,} rows"
    )

    # ---- Optional intervention --------------------------------------------
    with st.expander("Declare an approved intervention (optional)"):
        st.caption(
            "Without a declared intervention, no savings will be marked "
            "as **verified**. This is deliberate: cost data alone cannot "
            "prove causation."
        )
        has_iv = st.checkbox(
            "I have an approved intervention to declare",
            key="savings_has_iv",
        )
        intervention: ApprovedIntervention | None = None

        if has_iv:
            services = sorted(df["service"].unique().tolist())
            col_a, col_b = st.columns(2)

            with col_a:
                iv_date = st.date_input(
                    "Intervention date",
                    value=after_start,
                    min_value=min_date,
                    max_value=max_date,
                    key="savings_iv_date",
                )
                iv_service = st.selectbox(
                    "Service affected",
                    options=services,
                    key="savings_iv_service",
                )

            with col_b:
                iv_expected = st.number_input(
                    "Expected monthly saving (USD)",
                    min_value=0.0,
                    value=100.0,
                    step=10.0,
                    key="savings_iv_expected",
                )
                iv_approved_by = st.text_input(
                    "Approved by (name or email)",
                    value="",
                    key="savings_iv_approved_by",
                )

            iv_description = st.text_input(
                "Description",
                value="",
                placeholder="e.g. Rightsized m5.large to m5.medium",
                key="savings_iv_description",
            )

            if iv_approved_by.strip() and iv_description.strip():
                intervention = ApprovedIntervention(
                    id="ui-iv-1",
                    date=iv_date,
                    service=iv_service,
                    description=iv_description.strip(),
                    approved_by=iv_approved_by.strip(),
                    expected_monthly_saving_usd=float(iv_expected),
                )
            else:
                st.info(
                    "Fill in *Approved by* and *Description* to include "
                    "this intervention. Until then, the measurement runs "
                    "without it and verified savings will be $0."
                )

    # ---- Compute measurement ----------------------------------------------
    potential_monthly = sum(
        r.estimated_monthly_savings_usd for r in recommendations
    )

    try:
        measurement = measure_savings(
            baseline=baseline_df,
            after=after_df,
            interventions=[intervention] if intervention else None,
            potential_saving_monthly=potential_monthly,
        )
    except ValueError as exc:
        st.error(f"Could not compute savings: {exc}")
        return

    # ---- Display result ---------------------------------------------------
    st.markdown("### Measurement result")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Potential",
        f"${measurement.potential_monthly_saving:,.2f}/mo",
        help="Sum of estimated savings from recommendations. "
             "Not proven, not billable.",
    )
    c2.metric(
        "Observed",
        f"${measurement.observed_monthly_saving:,.2f}/mo",
        help="Cost reduction between the two windows. Real, but not "
             "necessarily caused by us.",
    )
    c3.metric(
        "Verified",
        f"${measurement.verified_monthly_saving:,.2f}/mo",
        help="Attributable to an approved intervention, capped at the "
             "declared expectation.",
    )
    c4.metric(
        "Eligible",
        f"${measurement.eligible_for_billing_monthly:,.2f}/mo",
        help="Subset of verified that matches the contract rules. "
             "Basis for the fee simulation.",
    )

    with st.expander("Calculation details", expanded=True):
        st.markdown(
            f"- **Baseline window**: {measurement.baseline_days} days "
            f"→ **${measurement.baseline_monthly_cost:,.2f}**/month\n"
            f"- **After window**: {measurement.after_days} days "
            f"→ **${measurement.after_monthly_cost:,.2f}**/month\n"
            f"- **Observed** = baseline − after = "
            f"**${measurement.observed_monthly_saving:,.2f}**/month\n"
            f"- **Verified** = attributable, capped at declared expectation = "
            f"**${measurement.verified_monthly_saving:,.2f}**/month\n"
            f"- **Eligible for billing** = "
            f"**${measurement.eligible_for_billing_monthly:,.2f}**/month"
        )

        if measurement.notes:
            st.markdown("**Notes:**")
            for n in measurement.notes:
                st.markdown(f"- {n}")

    # ---- Fee simulator ---------------------------------------------------
    st.divider()
    st.subheader("RaaS fee simulator (illustrative)")
    st.info(
        "This is a **simulation only**. No invoice is generated and no "
        "payment is initiated. The output is meant to support a "
        "conversation, not to replace a contract.",
        icon="ℹ️",
    )

    col_fee, col_period = st.columns([3, 1])
    with col_fee:
        fee_pct = st.slider(
            "Negotiated fee percentage",
            min_value=float(MIN_FEE_PCT),
            max_value=float(MAX_FEE_PCT),
            value=float(DEFAULT_FEE_PCT),
            step=0.5,
            key="savings_fee_pct",
        )
    with col_period:
        period_months = st.number_input(
            "Billing period (months)",
            min_value=1,
            max_value=60,
            value=1,
            step=1,
            key="savings_period_months",
        )

    try:
        sim = simulate_fee(
            eligible_monthly_savings_usd=measurement.eligible_for_billing_monthly,
            fee_pct=float(fee_pct),
            period_months=int(period_months),
        )
    except ValueError as exc:
        st.error(f"Could not simulate fee: {exc}")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Eligible / month",
        f"${sim.eligible_monthly_savings_usd:,.2f}",
    )
    c2.metric(
        f"Eligible / {sim.period_months} month(s)",
        f"${sim.eligible_over_period_usd:,.2f}",
    )
    c3.metric(
        "Estimated fee",
        f"${sim.fee_amount_usd:,.2f}",
    )

    with st.expander("Fee calculation details"):
        for step in sim.calculation_steps:
            st.markdown(f"- {step}")

    if sim.warnings:
        for w in sim.warnings:
            st.warning(w)

    st.caption(
        "The estimated fee is an illustration. Actual billing terms, "
        "taxes, and invoicing are outside the scope of this MVP and must "
        "be agreed separately."
    )


# ---- Footer ---------------------------------------------------------------

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

    recommendations = list(_cached_recommendations(df))

    tab_dashboard, tab_savings = st.tabs(["📊 Dashboard", "💵 Savings simulator"])

    with tab_dashboard:
        _render_kpi_row(stats)

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

    with tab_savings:
        _render_savings_tab(df, recommendations)

    _render_footer()


if __name__ == "__main__":
    main()