"""Tests for src.validation.

Every rule documented in docs/csv_format.md is covered here at least
once. The tests are intentionally small and focused: one behaviour per
test, so that a failure immediately points at the broken rule.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from src.validation import (
    MAX_FILE_SIZE_BYTES,
    MAX_STRING_LENGTH,
    REQUIRED_COLUMNS,
    ValidationIssue,
    ValidationResult,
    detect_duplicates,
    validate_currency,
    validate_dataframe,
    validate_dates,
    validate_file_size,
    validate_non_negative_number,
    validate_required_columns,
    validate_string_field,
)


# ---- Fixtures and helpers -------------------------------------------------

def _valid_row() -> dict[str, str]:
    """A single row that satisfies every rule of the spec."""
    return {
        "usage_date": "2026-05-01",
        "service": "Amazon Elastic Compute Cloud - Compute",
        "region": "eu-west-1",
        "usage_type": "BoxUsage",
        "usage_amount": "437.5",
        "unit": "Hrs",
        "unblended_cost": "42.0",
        "currency": "USD",
    }


def _valid_df(**overrides: str) -> pd.DataFrame:
    """One-row DataFrame with optional column overrides."""
    row = _valid_row()
    row.update(overrides)
    return pd.DataFrame([row])


# ---- validate_required_columns --------------------------------------------

def test_required_columns_all_present_passes() -> None:
    result = validate_required_columns(_valid_df().columns)
    assert result.is_valid
    assert result.errors == []


def test_missing_required_column_is_reported() -> None:
    df = _valid_df().drop(columns=["currency"])
    result = validate_required_columns(df.columns)
    assert not result.is_valid
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.row == 1
    assert err.column == "currency"
    assert "missing" in err.message.lower()


def test_all_missing_required_columns_are_reported() -> None:
    df = pd.DataFrame({"unrelated": ["x"]})
    result = validate_required_columns(df.columns)
    reported = {e.column for e in result.errors}
    assert reported == set(REQUIRED_COLUMNS)


# ---- validate_dates -------------------------------------------------------

def test_valid_iso_date_passes() -> None:
    result = validate_dates(_valid_df())
    assert result.is_valid


@pytest.mark.parametrize("bad_date", [
    "2026/05/01",
    "05-01-2026",
    "2026-5-1",
    "2026-05-1",
    "20260501",
    "not-a-date",
])
def test_non_iso_dates_are_rejected(bad_date: str) -> None:
    df = _valid_df(usage_date=bad_date)
    result = validate_dates(df)
    assert not result.is_valid
    assert result.errors[0].row == 2
    assert result.errors[0].column == "usage_date"


def test_calendar_invalid_date_is_rejected() -> None:
    df = _valid_df(usage_date="2026-02-30")
    result = validate_dates(df)
    assert not result.is_valid
    assert "calendar" in result.errors[0].message.lower()


def test_empty_date_is_rejected() -> None:
    df = _valid_df(usage_date="")
    result = validate_dates(df)
    assert not result.is_valid
    assert "empty" in result.errors[0].message.lower()


# ---- validate_non_negative_number -----------------------------------------

@pytest.mark.parametrize("value", ["42", "42.5", "42.50", "0", "0.0", "0.0001"])
def test_valid_numbers_pass(value: str) -> None:
    df = _valid_df(unblended_cost=value)
    result = validate_non_negative_number(df, "unblended_cost")
    assert result.is_valid


def test_negative_number_is_rejected() -> None:
    df = _valid_df(unblended_cost="-0.01")
    result = validate_non_negative_number(df, "unblended_cost")
    assert not result.is_valid
    assert "non-negative" in result.errors[0].message.lower()


def test_zero_is_accepted() -> None:
    df = _valid_df(unblended_cost="0")
    result = validate_non_negative_number(df, "unblended_cost")
    assert result.is_valid


def test_non_numeric_is_rejected() -> None:
    df = _valid_df(unblended_cost="abc")
    result = validate_non_negative_number(df, "unblended_cost")
    assert not result.is_valid


def test_comma_decimal_is_rejected() -> None:
    df = _valid_df(unblended_cost="42,5")
    result = validate_non_negative_number(df, "unblended_cost")
    assert not result.is_valid


def test_empty_number_is_rejected() -> None:
    df = _valid_df(unblended_cost="")
    result = validate_non_negative_number(df, "unblended_cost")
    assert not result.is_valid


# ---- validate_string_field ------------------------------------------------

def test_valid_string_passes() -> None:
    df = _valid_df(service="Amazon S3")
    result = validate_string_field(df, "service")
    assert result.is_valid


def test_empty_string_is_rejected() -> None:
    df = _valid_df(service="")
    result = validate_string_field(df, "service")
    assert not result.is_valid


def test_whitespace_only_is_rejected() -> None:
    df = _valid_df(service="   ")
    result = validate_string_field(df, "service")
    assert not result.is_valid


def test_overlong_string_is_rejected() -> None:
    df = _valid_df(service="x" * (MAX_STRING_LENGTH + 1))
    result = validate_string_field(df, "service")
    assert not result.is_valid
    assert "length" in result.errors[0].message.lower()


def test_exactly_max_length_is_accepted() -> None:
    df = _valid_df(service="x" * MAX_STRING_LENGTH)
    result = validate_string_field(df, "service")
    assert result.is_valid


# ---- validate_currency ----------------------------------------------------

@pytest.mark.parametrize("code", ["USD", "EUR", "GBP", "TND", "JPY"])
def test_valid_currencies_pass(code: str) -> None:
    df = _valid_df(currency=code)
    result = validate_currency(df)
    assert result.is_valid


@pytest.mark.parametrize("bad_code", ["usd", "US", "USDD", "$", "123", "U$D", "uSd"])
def test_invalid_currencies_are_rejected(bad_code: str) -> None:
    df = _valid_df(currency=bad_code)
    result = validate_currency(df)
    assert not result.is_valid


def test_empty_currency_is_rejected() -> None:
    df = _valid_df(currency="")
    result = validate_currency(df)
    assert not result.is_valid


# ---- detect_duplicates ----------------------------------------------------

def test_no_duplicates_passes() -> None:
    row = _valid_row()
    df = pd.DataFrame([row, {**row, "usage_date": "2026-05-02"}])
    result = detect_duplicates(df)
    assert result.is_valid


def test_duplicate_key_is_reported() -> None:
    row = _valid_row()
    df = pd.DataFrame([row, row])
    result = detect_duplicates(df)
    assert not result.is_valid
    assert result.errors[0].row == 3
    assert "row 2" in result.errors[0].message


def test_three_occurrences_report_two_errors() -> None:
    row = _valid_row()
    df = pd.DataFrame([row, row, row])
    result = detect_duplicates(df)
    assert len(result.errors) == 2
    rows_reported = {e.row for e in result.errors}
    assert rows_reported == {3, 4}


def test_different_region_is_not_duplicate() -> None:
    row = _valid_row()
    df = pd.DataFrame([row, {**row, "region": "us-east-1"}])
    result = detect_duplicates(df)
    assert result.is_valid


def test_different_usage_type_is_not_duplicate() -> None:
    row = _valid_row()
    df = pd.DataFrame([row, {**row, "usage_type": "DataTransfer-Out"}])
    result = detect_duplicates(df)
    assert result.is_valid


# ---- validate_dataframe (integration) -------------------------------------

def test_sample_file_from_disk_is_valid() -> None:
    df = pd.read_csv("data/sample_costs.csv", dtype=str)
    result = validate_dataframe(df)
    assert result.is_valid, [str(e) for e in result.errors]


def test_empty_dataframe_is_rejected() -> None:
    df = pd.DataFrame(columns=REQUIRED_COLUMNS)
    result = validate_dataframe(df)
    assert not result.is_valid
    assert "no data rows" in result.errors[0].message.lower()


def test_none_dataframe_is_rejected() -> None:
    result = validate_dataframe(None)  # type: ignore[arg-type]
    assert not result.is_valid


def test_multiple_errors_are_all_reported_and_sorted() -> None:
    row = _valid_row()
    df = pd.DataFrame([
        {**row, "usage_date": "2026/05/01", "region": "eu-west-1"},   # row 2 — bad date
        {**row, "currency": "usd",          "region": "eu-west-2"},   # row 3 — bad currency
        {**row,                            "region": "eu-west-3"},   # row 4 — valid
    ])
    result = validate_dataframe(df)
    assert not result.is_valid
    assert len(result.errors) == 2
    assert result.errors[0].row == 2
    assert result.errors[1].row == 3


def test_extra_columns_are_ignored() -> None:
    row = _valid_row()
    row["extra_column"] = "ignored"
    df = pd.DataFrame([row])
    result = validate_dataframe(df)
    assert result.is_valid


# ---- validate_file_size ---------------------------------------------------

def test_small_file_size_passes() -> None:
    result = validate_file_size(1024)
    assert result.is_valid


def test_exactly_max_file_size_passes() -> None:
    result = validate_file_size(MAX_FILE_SIZE_BYTES)
    assert result.is_valid


def test_oversized_file_is_rejected() -> None:
    result = validate_file_size(MAX_FILE_SIZE_BYTES + 1)
    assert not result.is_valid
    assert "maximum size" in result.errors[0].message.lower()


# ---- Data structure sanity ------------------------------------------------

def test_validation_result_summary_no_issues() -> None:
    empty = ValidationResult()
    assert empty.summary() == "No issues."


def test_validation_result_summary_with_errors() -> None:
    result = ValidationResult()
    result.add_error(ValidationIssue(row=2, column="x", value="y", message="problem"))
    assert "1 error" in result.summary()


def test_validation_issue_is_immutable() -> None:
    issue = ValidationIssue(row=2, column="c", value="v", message="m")
    with pytest.raises(FrozenInstanceError):
        issue.row = 5  # type: ignore[misc]