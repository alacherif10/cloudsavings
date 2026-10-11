"""User-facing CSV import pipeline for CloudSavings.

Public entry point:

    import_csv_from_bytes(data: bytes, filename: str) -> ImportResult

The caller (a Streamlit uploader, a CLI, a test) hands us raw bytes and
a filename. We return an ImportResult that either contains:

    - a clean, typed DataFrame ready for analysis (`result.df`), or
    - a structured list of issues (`result.validation.errors`), or
    - a short fatal message for problems that cannot be recovered
      from at the row level (`result.fatal_error`).

Design rules (non-negotiable):

    - Never raise on malformed user input; always return an ImportResult.
    - Never write to disk.
    - Never import Streamlit here; this module is UI-agnostic.
    - Never trust the filename extension; only the bytes matter.
    - Never leak local file paths or stack traces to the user.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import pandas as pd

from src.validation import (
    REQUIRED_COLUMNS,
    ValidationResult,
    validate_dataframe,
    validate_file_size,
)

# How many rows to show in a preview table in the UI.
MAX_PREVIEW_ROWS: int = 50

# Columns we cast to specific dtypes after validation succeeds.
_STRING_COLUMNS: tuple[str, ...] = ("service", "region", "usage_type", "unit", "currency")
_FLOAT_COLUMNS: tuple[str, ...] = ("usage_amount", "unblended_cost")


# ---- Result type ----------------------------------------------------------

@dataclass
class ImportResult:
    """Outcome of attempting to import a CSV file.

    Exactly one of the following is true when the object is returned:

    - Success: `df` is a clean, typed DataFrame; `validation.is_valid`
      is True; `fatal_error` is None.
    - Row-level failure: `df` is None; `validation.errors` lists the
      problems; `fatal_error` is None.
    - Fatal failure: `df` is None; `fatal_error` is a short message;
      `validation.errors` is empty.

    Callers should test `is_success` before touching `df`.
    """
    df: pd.DataFrame | None = None
    validation: ValidationResult = field(default_factory=ValidationResult)
    fatal_error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.df is not None


# ---- Public API -----------------------------------------------------------

def import_csv_from_bytes(data: bytes, filename: str = "upload.csv") -> ImportResult:
    """Import a CSV file provided as raw bytes.

    Parameters
    ----------
    data:
        The bytes exactly as received from the upload mechanism.
    filename:
        Original filename, used only for user-facing messages.

    Returns
    -------
    ImportResult — see the class docstring for the possible shapes.
    """
    result = ImportResult()

    # 1. Size check — cheap, do it first.
    size_result = validate_file_size(len(data))
    if not size_result.is_valid:
        result.fatal_error = size_result.errors[0].message
        return result

    # 2. Empty bytes.
    if not data or not data.strip():
        result.fatal_error = "The uploaded file is empty."
        return result

    # 3. Decode — UTF-8 with optional BOM. Anything else is rejected.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.fatal_error = (
            "The file is not valid UTF-8. "
            "Please re-export it as UTF-8 and try again."
        )
        return result

    # 4. Parse — read everything as strings first so our validators
    #    decide what is well-formed, not pandas.
    try:
        df = pd.read_csv(
            io.StringIO(text),
            dtype=str,
            keep_default_na=False,
            skip_blank_lines=True,
        )
    except pd.errors.EmptyDataError:
        result.fatal_error = "The file contains no data."
        return result
    except pd.errors.ParserError as exc:
        # Do not leak the raw pandas message; it can contain snippets of
        # the user's data and internal line references.
        result.fatal_error = (
            "The file is not a valid CSV. "
            "Check that columns are separated by commas and that fields "
            "containing commas are enclosed in double quotes."
        )
        # `exc` is captured here only to keep the reason around for
        # debugging via logging in a future iteration.
        _ = exc
        return result

    # 5. Header only — no data rows.
    if len(df) == 0:
        result.fatal_error = "The file contains a header but no data rows."
        return result

    # 6. Row-level validation — the actual contract.
    validation = validate_dataframe(df)
    result.validation = validation
    if not validation.is_valid:
        return result

    # 7. Coerce to a typed DataFrame for downstream analysis.
    try:
        result.df = _coerce_types(df)
    except (ValueError, TypeError) as exc:
        # Should be unreachable given step 6 succeeded, but we treat it
        # as a fatal error rather than crashing the caller.
        result.fatal_error = (
            "The file passed validation but could not be converted to "
            "typed data. This is a bug — please report it."
        )
        _ = exc
        return result

    return result


def preview_rows(df: pd.DataFrame, max_rows: int = MAX_PREVIEW_ROWS) -> pd.DataFrame:
    """Return the first `max_rows` rows, for display in the UI."""
    return df.head(max_rows).copy()


# ---- Internals ------------------------------------------------------------

def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of `df` with required columns cast to proper dtypes.

    Extra columns are dropped: validation allows them for forward
    compatibility, but the rest of the app only cares about the contract
    columns listed in docs/csv_format.md.
    """
    out = df.loc[:, list(REQUIRED_COLUMNS)].copy()

    # Strings — trim surrounding whitespace.
    for col in _STRING_COLUMNS:
        out[col] = out[col].astype(str).str.strip()

    # Dates — ISO format is guaranteed by validation.
    out["usage_date"] = pd.to_datetime(
        out["usage_date"], format="%Y-%m-%d", errors="raise"
    )

    # Numbers — non-negative, dot decimal, guaranteed by validation.
    for col in _FLOAT_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="raise").astype(float)

    return out