"""Tests for src.csv_import.

Covers the three shapes of ImportResult (success, row-level failure,
fatal failure) and every behaviour the rest of the app relies on:
typed columns, dropped extras, encodings, preview helper.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.csv_import import (
    MAX_PREVIEW_ROWS,
    ImportResult,
    import_csv_from_bytes,
    preview_rows,
)
from src.validation import MAX_FILE_SIZE_BYTES

SAMPLE_PATH = Path("data/sample_costs.csv")


# ---- Helpers --------------------------------------------------------------

def _valid_csv_bytes() -> bytes:
    """A minimal, well-formed CSV matching docs/csv_format.md."""
    return (
        b"usage_date,service,region,usage_type,usage_amount,unit,unblended_cost,currency\n"
        b"2026-05-01,Amazon S3,eu-west-1,TimedStorage,100.0,GB-Mo,1.50,USD\n"
        b"2026-05-02,Amazon S3,eu-west-1,TimedStorage,110.0,GB-Mo,1.65,USD\n"
    )


# ---- Success path ---------------------------------------------------------

def test_valid_bytes_return_success() -> None:
    result = import_csv_from_bytes(_valid_csv_bytes(), "ok.csv")
    assert result.is_success
    assert result.fatal_error is None
    assert result.validation.is_valid
    assert result.df is not None
    assert len(result.df) == 2


def test_sample_file_imports_successfully() -> None:
    result = import_csv_from_bytes(SAMPLE_PATH.read_bytes(), "sample_costs.csv")
    assert result.is_success, [str(e) for e in result.validation.errors]
    assert result.df is not None
    assert len(result.df) == 10


def test_success_result_has_typed_columns() -> None:
    result = import_csv_from_bytes(_valid_csv_bytes(), "ok.csv")
    assert result.df is not None
    df = result.df

    assert pd.api.types.is_datetime64_any_dtype(df["usage_date"])
    assert pd.api.types.is_float_dtype(df["usage_amount"])
    assert pd.api.types.is_float_dtype(df["unblended_cost"])
    assert df["service"].dtype == object


def test_dates_are_parsed_not_strings() -> None:
    result = import_csv_from_bytes(_valid_csv_bytes(), "ok.csv")
    assert result.df is not None
    assert result.df["usage_date"].iloc[0] == pd.Timestamp("2026-05-01")


def test_extra_columns_are_dropped() -> None:
    csv_bytes = (
        b"usage_date,service,region,usage_type,usage_amount,unit,unblended_cost,currency,notes\n"
        b"2026-05-01,Amazon S3,eu-west-1,TimedStorage,100.0,GB-Mo,1.50,USD,hello\n"
    )
    result = import_csv_from_bytes(csv_bytes, "ok.csv")
    assert result.is_success
    assert result.df is not None
    assert "notes" not in result.df.columns


def test_utf8_bom_is_accepted() -> None:
    csv_bytes = b"\xef\xbb\xbf" + _valid_csv_bytes()
    result = import_csv_from_bytes(csv_bytes, "ok.csv")
    assert result.is_success


def test_crlf_line_endings_are_accepted() -> None:
    csv_bytes = _valid_csv_bytes().replace(b"\n", b"\r\n")
    result = import_csv_from_bytes(csv_bytes, "ok.csv")
    assert result.is_success
    assert result.df is not None
    assert len(result.df) == 2


def test_strings_are_trimmed() -> None:
    csv_bytes = (
        b"usage_date,service,region,usage_type,usage_amount,unit,unblended_cost,currency\n"
        b"2026-05-01,  Amazon S3  ,eu-west-1,TimedStorage,100.0,GB-Mo,1.50,USD\n"
    )
    result = import_csv_from_bytes(csv_bytes, "ok.csv")
    assert result.is_success
    assert result.df is not None
    assert result.df["service"].iloc[0] == "Amazon S3"


# ---- Row-level failures ---------------------------------------------------

def test_bad_date_returns_row_level_error() -> None:
    csv_bytes = _valid_csv_bytes().replace(b"2026-05-01", b"2026/05/01")
    result = import_csv_from_bytes(csv_bytes, "bad.csv")
    assert not result.is_success
    assert result.fatal_error is None
    assert len(result.validation.errors) == 1
    assert result.validation.errors[0].column == "usage_date"


def test_missing_column_returns_row_level_error() -> None:
    csv_bytes = (
        b"usage_date,service,region,usage_type,usage_amount,unit,unblended_cost\n"
        b"2026-05-01,Amazon S3,eu-west-1,TimedStorage,100.0,GB-Mo,1.50\n"
    )
    result = import_csv_from_bytes(csv_bytes, "bad.csv")
    assert not result.is_success
    assert result.fatal_error is None
    reported = {e.column for e in result.validation.errors}
    assert "currency" in reported


def test_duplicate_rows_return_row_level_error() -> None:
    # Original: header + row1 + row2. Append row1 + row2 again.
    body = _valid_csv_bytes().split(b"\n", 1)[1]
    csv_bytes = _valid_csv_bytes() + body
    result = import_csv_from_bytes(csv_bytes, "bad.csv")
    assert not result.is_success
    assert result.fatal_error is None
    assert any("Duplicate" in e.message for e in result.validation.errors)


# ---- Fatal failures -------------------------------------------------------

def test_empty_bytes_return_fatal_error() -> None:
    result = import_csv_from_bytes(b"", "empty.csv")
    assert not result.is_success
    assert result.fatal_error is not None
    assert "empty" in result.fatal_error.lower()
    assert result.validation.errors == []


def test_whitespace_only_bytes_return_fatal_error() -> None:
    result = import_csv_from_bytes(b"   \n   \n", "blank.csv")
    assert not result.is_success
    assert result.fatal_error is not None


def test_non_utf8_bytes_return_fatal_error() -> None:
    # UTF-16 with BOM is not valid UTF-8.
    utf16_payload = "usage_date,service\n".encode("utf-16")
    result = import_csv_from_bytes(utf16_payload, "utf16.csv")
    assert not result.is_success
    assert result.fatal_error is not None
    assert "utf-8" in result.fatal_error.lower()


def test_oversized_payload_returns_fatal_error() -> None:
    huge = b"x" * (MAX_FILE_SIZE_BYTES + 1)
    result = import_csv_from_bytes(huge, "huge.csv")
    assert not result.is_success
    assert result.fatal_error is not None
    assert "maximum" in result.fatal_error.lower()


def test_header_only_returns_fatal_error() -> None:
    csv_bytes = (
        b"usage_date,service,region,usage_type,usage_amount,unit,unblended_cost,currency\n"
    )
    result = import_csv_from_bytes(csv_bytes, "header_only.csv")
    assert not result.is_success
    assert result.fatal_error is not None
    assert "no data rows" in result.fatal_error.lower()


def test_malformed_csv_returns_fatal_error() -> None:
    # Second data row has MORE fields than the header — pandas raises
    # ParserError, which we translate into a fatal_error.
    csv_bytes = (
        b"usage_date,service,region,usage_type,usage_amount,unit,unblended_cost,currency\n"
        b"2026-05-01,Amazon S3,eu-west-1,TimedStorage,100.0,GB-Mo,1.50,USD\n"
        b"2026-05-02,Amazon S3,eu-west-1,TimedStorage,110.0,GB-Mo,1.65,USD,EXTRA,FIELDS,HERE\n"
    )
    result = import_csv_from_bytes(csv_bytes, "broken.csv")
    assert not result.is_success
    assert result.fatal_error is not None

# ---- Preview helper -------------------------------------------------------

def test_preview_rows_truncates_to_max() -> None:
    df = pd.DataFrame({"a": range(100)})
    preview = preview_rows(df, max_rows=10)
    assert len(preview) == 10


def test_preview_rows_returns_copy() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    preview = preview_rows(df)
    preview.loc[0, "a"] = 999
    assert df.loc[0, "a"] == 1


def test_default_preview_size() -> None:
    df = pd.DataFrame({"a": range(MAX_PREVIEW_ROWS + 50)})
    preview = preview_rows(df)
    assert len(preview) == MAX_PREVIEW_ROWS


# ---- ImportResult contract ------------------------------------------------

def test_import_result_default_state_is_empty() -> None:
    result = ImportResult()
    assert result.df is None
    assert result.fatal_error is None
    assert result.validation.is_valid  # empty ValidationResult is valid
    assert not result.is_success


def test_fatal_error_short_circuits_validation() -> None:
    result = import_csv_from_bytes(b"", "empty.csv")
    assert result.validation.errors == []
    assert result.validation.warnings == []