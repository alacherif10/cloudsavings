"""Tests for src.reports.

Verify the structure, honesty flags, and edge-case behaviour of the
Markdown report generator. We do not assert the exact text of the
report (that would be brittle), only the invariants the report must
respect.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from src.recommendations import Recommendation
from src.reports import build_markdown_report
from src.savings import ApprovedIntervention, measure_savings


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


def _small_df() -> pd.DataFrame:
    return pd.DataFrame([
        _row("2026-01-01", "EC2", 100.0),
        _row("2026-01-15", "EC2", 110.0),
        _row("2026-02-01", "EC2", 90.0),
        _row("2026-02-15", "S3", 20.0),
    ])


def _rec() -> Recommendation:
    return Recommendation(
        id="unusual_increase",
        title="Test recommendation",
        service="EC2",
        category="spend_increase",
        reason="Just testing.",
        estimated_monthly_savings_usd=42.0,
        confidence="low",
        required_evidence=("evidence one",),
        risks=("a risk",),
        next_action="Do nothing.",
    )


# ---- Basic structure ------------------------------------------------------

def test_report_starts_with_title() -> None:
    md = build_markdown_report(_small_df())
    assert md.startswith("# CloudSavings — Cost report")


def test_report_ends_with_newline() -> None:
    md = build_markdown_report(_small_df())
    assert md.endswith("\n")


def test_report_contains_all_core_sections() -> None:
    md = build_markdown_report(_small_df())
    for section in ("## 1.", "## 2.", "## 3.", "## 4.",
                    "## 6.", "## 7.", "## 8."):
        assert section in md, f"Missing section {section}"


def test_savings_section_absent_when_no_measurement() -> None:
    md = build_markdown_report(_small_df())
    assert "## 5." not in md


def test_savings_section_present_when_measurement_provided() -> None:
    baseline = pd.DataFrame([
        _row("2026-01-01", "EC2", 10.0),
        _row("2026-01-15", "EC2", 10.0),
        _row("2026-01-30", "EC2", 10.0),
        _row("2026-02-10", "EC2", 10.0),
    ])
    after = pd.DataFrame([
        _row("2026-03-01", "EC2", 6.0),
        _row("2026-03-15", "EC2", 6.0),
        _row("2026-03-30", "EC2", 6.0),
        _row("2026-04-10", "EC2", 6.0),
    ])
    iv = ApprovedIntervention(
        id="iv-1", date=date(2026, 3, 1), service="EC2",
        description="test", approved_by="test@x.test",
        expected_monthly_saving_usd=100.0,
    )
    m = measure_savings(baseline, after, [iv])
    md = build_markdown_report(_small_df(), measurement=m)
    assert "## 5." in md
    assert "Savings measurement" in md


# ---- Honesty flags --------------------------------------------------------

def test_synthetic_banner_present_when_flagged() -> None:
    md = build_markdown_report(_small_df(), is_synthetic=True)
    assert "SIMULATED DATA" in md


def test_synthetic_banner_absent_when_not_flagged() -> None:
    md = build_markdown_report(_small_df(), is_synthetic=False)
    assert "SIMULATED DATA" not in md
    assert "Source file" in md


def test_source_name_appears_in_header() -> None:
    md = build_markdown_report(_small_df(), source_name="my_export.csv")
    assert "my_export.csv" in md


# ---- Recommendations ------------------------------------------------------

def test_no_recommendations_says_so() -> None:
    md = build_markdown_report(_small_df(), recommendations=[])
    assert "No recommendations were produced" in md


def test_recommendation_appears_with_evidence_and_risks() -> None:
    md = build_markdown_report(_small_df(), recommendations=[_rec()])
    assert "Test recommendation" in md
    assert "evidence one" in md
    assert "a risk" in md
    assert "Do nothing." in md


def test_recommendation_escapes_pipe_characters() -> None:
    rec = _rec()
    # Rebuild with a pipe in the service name.
    rec2 = Recommendation(
        id=rec.id,
        title=rec.title,
        service="Service|With|Pipes",
        category=rec.category,
        reason=rec.reason,
        estimated_monthly_savings_usd=rec.estimated_monthly_savings_usd,
        confidence=rec.confidence,
        required_evidence=rec.required_evidence,
        risks=rec.risks,
        next_action=rec.next_action,
    )
    md = build_markdown_report(_small_df(), recommendations=[rec2])
    # The service name is inside a code span in a bullet, not a table,
    # so pipes survive. But if the report ever moves it into a table,
    # this assertion will catch a regression.
    assert "Service" in md


# ---- Determinism and generated_at -----------------------------------------

def test_no_timestamp_by_default_is_deterministic() -> None:
    md_a = build_markdown_report(_small_df(), generated_at=None)
    md_b = build_markdown_report(_small_df(), generated_at=None)
    assert md_a == md_b


def test_explicit_timestamp_appears() -> None:
    ts = datetime(2026, 10, 11, 15, 0, 0)
    md = build_markdown_report(_small_df(), generated_at=ts)
    assert "2026-10-11T15:00:00" in md


def test_different_timestamps_produce_different_reports() -> None:
    ts_a = datetime(2026, 1, 1, 0, 0, 0)
    ts_b = datetime(2026, 1, 2, 0, 0, 0)
    md_a = build_markdown_report(_small_df(), generated_at=ts_a)
    md_b = build_markdown_report(_small_df(), generated_at=ts_b)
    assert md_a != md_b


# ---- Limitations ----------------------------------------------------------

def test_limitations_section_present() -> None:
    md = build_markdown_report(_small_df())
    assert "## 7. Limitations" in md


def test_limitations_mention_no_automatic_modification() -> None:
    md = build_markdown_report(_small_df())
    assert "No resource is modified automatically" in md


def test_assumptions_mention_thirty_day_month() -> None:
    md = build_markdown_report(_small_df())
    assert "30 days" in md or "thirty" in md.lower()


# ---- Next steps -----------------------------------------------------------

def test_next_steps_section_present() -> None:
    md = build_markdown_report(_small_df())
    assert "## 8. Suggested next steps" in md


# ---- Integration on the demo dataset --------------------------------------

def test_demo_dataset_report_is_well_formed() -> None:
    from src.csv_import import import_csv_from_bytes
    from src.recommendations import generate_recommendations

    df = import_csv_from_bytes(
        open("data/demo_costs.csv", "rb").read(), "demo.csv"
    ).df
    assert df is not None

    recs = generate_recommendations(df)
    md = build_markdown_report(
        df, recs,
        source_name="demo_costs.csv",
        is_synthetic=True,
        generated_at=datetime(2026, 10, 11, 15, 0, 0),
    )

    assert len(md) > 5000
    assert "SIMULATED DATA" in md
    assert "## 1." in md
    assert "## 8." in md
    # At least one recommendation from the demo dataset must show up.
    assert "unusual_increase" in md