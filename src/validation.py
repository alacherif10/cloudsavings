"""Pure validation logic for uploaded AWS cost CSVs.

This module implements the rules described in docs/csv_format.md.
It performs no file I/O and imports nothing from Streamlit, so it can be
tested in isolation and reused from any future entry point (CLI, API).

Public entry point:

    validate_dataframe(df) -> ValidationResult

The caller typically rejects the whole file if `result.is_valid` is False.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date as _date

import pandas as pd

# ---- Constants (mirror docs/csv_format.md) --------------------------------

REQUIRED_COLUMNS: tuple[str, ...] = (
    "usage_date",
    "service",
    "region",
    "usage_type",
    "usage_amount",
    "unit",
    "unblended_cost",
    "currency",
)

DUPLICATE_KEY_COLUMNS: tuple[str, ...] = (
    "usage_date",
    "service",
    "region",
    "usage_type",
)

MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024   # 10 MB
MAX_STRING_LENGTH: int = 200
SAMPLE_VALUE_MAX_LEN: int = 40

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


# ---- Data structures ------------------------------------------------------

@dataclass(frozen=True)
class ValidationIssue:
    """A single problem found in the uploaded data.

    `row` is 1-based and matches what the user sees in a spreadsheet,
    including the header row: `row=1` refers to the header, and the
    first data row is `row=2`.
    """
    row: int
    column: str
    value: str
    message: str

    def __str__(self) -> str:
        return (
            f"row {self.row}, column '{self.column}': {self.message} "
            f"(value: {self.value!r})"
        )


@dataclass
class ValidationResult:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add_error(self, issue: ValidationIssue) -> None:
        self.errors.append(issue)

    def add_warning(self, issue: ValidationIssue) -> None:
        self.warnings.append(issue)

    def summary(self) -> str:
        if self.is_valid and not self.warnings:
            return "No issues."
        parts: list[str] = []
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        return ", ".join(parts)


# ---- Helpers --------------------------------------------------------------

def _as_text(raw: object) -> str:
    """Return a stripped string representation, treating NA as empty."""
    if raw is None:
        return ""
    try:
        if pd.isna(raw):
            return ""
    except (TypeError, ValueError):
        # pd.isna raises on some non-scalar inputs; fall through.
        pass
    return str(raw).strip()


def _truncate_for_message(value: str, limit: int = SAMPLE_VALUE_MAX_LEN) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _merge_into(target: ValidationResult, source: ValidationResult) -> None:
    target.errors.extend(source.errors)
    target.warnings.extend(source.warnings)


# ---- Per-column validators ------------------------------------------------

def validate_required_columns(columns) -> ValidationResult:
    """Report every required column missing from the header.

    Missing columns are reported on row 1, so the message is immediately
    actionable for the user.
    """
    result = ValidationResult()
    present = set(columns)
    for name in REQUIRED_COLUMNS:
        if name not in present:
            result.add_error(ValidationIssue(
                row=1,
                column=name,
                value="",
                message=f"Required column '{name}' is missing from the header.",
            ))
    return result


def validate_dates(df: pd.DataFrame, column: str = "usage_date") -> ValidationResult:
    result = ValidationResult()
    if column not in df.columns:
        return result

    for position, raw in enumerate(df[column]):
        row_number = position + 2

        # If pandas already parsed the column to real dates, the values
        # are valid by construction; nothing to check.
        if isinstance(raw, _date):
            continue

        text = _as_text(raw)

        if not text:
            result.add_error(ValidationIssue(
                row=row_number, column=column, value="",
                message="Date is empty.",
            ))
            continue

        if not _DATE_RE.match(text):
            result.add_error(ValidationIssue(
                row=row_number, column=column,
                value=_truncate_for_message(text),
                message="Date must use ISO 8601 format YYYY-MM-DD.",
            ))
            continue

        # Regex matched; now check it is a real calendar date.
        try:
            pd.Timestamp(text)
        except (ValueError, TypeError):
            result.add_error(ValidationIssue(
                row=row_number, column=column,
                value=_truncate_for_message(text),
                message="Date is not a valid calendar date.",
            ))
    return result


def validate_non_negative_number(df: pd.DataFrame, column: str) -> ValidationResult:
    result = ValidationResult()
    if column not in df.columns:
        return result

    for position, raw in enumerate(df[column]):
        row_number = position + 2
        text = _as_text(raw)

        if not text:
            result.add_error(ValidationIssue(
                row=row_number, column=column, value="",
                message="Value is empty.",
            ))
            continue

        try:
            number = float(text)
        except (ValueError, OverflowError):
            result.add_error(ValidationIssue(
                row=row_number, column=column,
                value=_truncate_for_message(text),
                message="Value must be a number using '.' as decimal separator.",
            ))
            continue

        if not math.isfinite(number):
            result.add_error(ValidationIssue(
                row=row_number, column=column,
                value=_truncate_for_message(text),
                message="Value must be a finite number (not NaN or infinity).",
            ))
            continue

        if number < 0:
            result.add_error(ValidationIssue(
                row=row_number, column=column,
                value=_truncate_for_message(text),
                message="Value must be non-negative.",
            ))
    return result


def validate_string_field(
    df: pd.DataFrame,
    column: str,
    max_length: int = MAX_STRING_LENGTH,
) -> ValidationResult:
    result = ValidationResult()
    if column not in df.columns:
        return result

    for position, raw in enumerate(df[column]):
        row_number = position + 2
        text = _as_text(raw)

        if not text:
            result.add_error(ValidationIssue(
                row=row_number, column=column, value="",
                message="Value is empty.",
            ))
            continue

        if len(text) > max_length:
            result.add_error(ValidationIssue(
                row=row_number, column=column,
                value=_truncate_for_message(text),
                message=f"Value exceeds the maximum length of {max_length} characters.",
            ))
    return result


def validate_currency(df: pd.DataFrame, column: str = "currency") -> ValidationResult:
    result = ValidationResult()
    if column not in df.columns:
        return result

    for position, raw in enumerate(df[column]):
        row_number = position + 2
        text = _as_text(raw)

        if not text:
            result.add_error(ValidationIssue(
                row=row_number, column=column, value="",
                message="Currency is empty.",
            ))
            continue

        if not _CURRENCY_RE.match(text):
            result.add_error(ValidationIssue(
                row=row_number, column=column,
                value=_truncate_for_message(text),
                message="Currency must be a 3-letter uppercase ISO 4217 code.",
            ))
    return result


def detect_duplicates(df: pd.DataFrame) -> ValidationResult:
    """Flag rows sharing the same (date, service, region, usage_type) key.

    The first occurrence is the reference; every later row with the same
    key is reported as an error pointing back to the first one.
    """
    result = ValidationResult()

    if not all(c in df.columns for c in DUPLICATE_KEY_COLUMNS):
        # Without the full key we cannot check reliably. Skip; the
        # missing-column validator will already have reported the issue.
        return result

    seen: dict[tuple[str, ...], int] = {}

    columns = [df[c] for c in DUPLICATE_KEY_COLUMNS]
    for position, values in enumerate(zip(*columns)):
        key = tuple(_as_text(v) for v in values)
        row_number = position + 2

        first_row = seen.get(key)
        if first_row is None:
            seen[key] = row_number
            continue

        result.add_error(ValidationIssue(
            row=row_number,
            column=", ".join(DUPLICATE_KEY_COLUMNS),
            value=_truncate_for_message(" | ".join(key)),
            message=f"Duplicate of row {first_row}.",
        ))
    return result


# ---- Entry points ---------------------------------------------------------

def validate_dataframe(df: pd.DataFrame) -> ValidationResult:
    """Run every rule from docs/csv_format.md against `df`.

    The DataFrame is expected to already be loaded from CSV. Extra
    columns are ignored.

    Returns a ValidationResult; check `.is_valid`.
    """
    result = ValidationResult()

    if df is None or len(df) == 0:
        result.add_error(ValidationIssue(
            row=0,
            column="",
            value="",
            message="The file contains no data rows.",
        ))
        return result

    _merge_into(result, validate_required_columns(df.columns))
    _merge_into(result, validate_dates(df, "usage_date"))
    _merge_into(result, validate_string_field(df, "service"))
    _merge_into(result, validate_string_field(df, "region"))
    _merge_into(result, validate_string_field(df, "usage_type"))
    _merge_into(result, validate_non_negative_number(df, "usage_amount"))
    _merge_into(result, validate_string_field(df, "unit"))
    _merge_into(result, validate_non_negative_number(df, "unblended_cost"))
    _merge_into(result, validate_currency(df, "currency"))
    _merge_into(result, detect_duplicates(df))

    result.errors.sort(key=lambda i: (i.row, i.column))
    result.warnings.sort(key=lambda i: (i.row, i.column))
    return result


def validate_file_size(size_bytes: int) -> ValidationResult:
    """Check that an uploaded file does not exceed the size limit."""
    result = ValidationResult()
    if size_bytes > MAX_FILE_SIZE_BYTES:
        limit_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        result.add_error(ValidationIssue(
            row=0,
            column="",
            value=f"{size_bytes:,} bytes",
            message=f"File exceeds the maximum size of {limit_mb} MB.",
        ))
    return result