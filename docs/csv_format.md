# Supported CSV format

This document is the contract between CloudSavings and any CSV file
uploaded by a user. If a file does not conform to this specification,
the importer will reject it with a clear error message and will not
silently insert partial or altered data.

## Encoding and separators

- Encoding: **UTF-8** (with or without BOM).
- Field separator: **comma** (`,`).
- Line terminator: **LF** or **CRLF** (both accepted).
- Quoting: standard RFC 4180. Fields containing commas or newlines must
  be enclosed in double quotes (`"`).

## Required columns

Every uploaded file must contain these columns, spelled exactly:

| Column           | Type    | Accepted values                                                   |
|------------------|---------|-------------------------------------------------------------------|
| `usage_date`     | date    | ISO 8601: `YYYY-MM-DD`. No other format is accepted.              |
| `service`        | string  | Non-empty. Any AWS service name. Max 200 characters.              |
| `region`         | string  | Non-empty. Any region code (e.g. `eu-west-1`, `us-east-1`).       |
| `usage_type`     | string  | Non-empty. Short label (e.g. `BoxUsage`, `TimedStorage`).         |
| `usage_amount`   | decimal | Non-negative number. Decimal point must be `.`.                   |
| `unit`           | string  | Non-empty. Unit of measure (e.g. `Hrs`, `GB-Mo`, `GB`).           |
| `unblended_cost` | decimal | Non-negative number. Decimal point must be `.`.                   |
| `currency`       | string  | ISO 4217: 3 uppercase letters (e.g. `USD`, `EUR`, `TND`).         |

Extra columns are allowed and ignored. Missing required columns cause a
hard rejection with a message listing which ones are absent.

## Accepted value formats

### Dates

- `2026-05-01` — valid.
- `05/01/2026` — **rejected** (ambiguous).
- `2026-5-1` — **rejected** (not zero-padded).
- Empty value — **rejected**.

### Numbers

- `42`, `42.5`, `42.50`, `0`, `0.0` — valid.
- `1 234.56` — **rejected** (space separators not supported).
- `1234,56` — **rejected** (comma decimal not supported — the comma is
  the field separator).
- Negative numbers — **rejected** for `usage_amount` and
  `unblended_cost`. AWS Cost Explorer exports are always non-negative
  in `unblended_cost`; credits are exported as separate rows with a
  positive amount and a different `usage_type` — out of scope here.
- Empty value — **rejected**.

### Currency

- `USD`, `EUR`, `GBP`, `TND` — valid.
- `usd` — **rejected** (must be uppercase).
- `US` — **rejected** (must be 3 letters).
- `$` — **rejected**.
- Empty value — **rejected**.

## Duplicate detection

A row is considered a **duplicate** of another when all of the
following are identical:

- `usage_date`
- `service`
- `region`
- `usage_type`

If two rows share this key, the importer rejects the **whole file** and
reports the first few duplicated keys it found. This is deliberate: a
cost report with duplicate rows is ambiguous, and we refuse to pick one
silently.

## Empty file / header only

- A file with zero data rows (header only) is **rejected**.
- A completely empty file is **rejected**.

## File size

- Maximum accepted size during the initial MVP: **10 MB**.
- Larger files are **rejected** with a message suggesting the user
  split the export by month.

## Errors reported to the user

Errors are reported as a list. Each entry contains:

- the row number (1-based, counting the header as row 1),
- the column name,
- the offending value (truncated to 40 characters),
- a short human-readable explanation.

The importer never displays local file paths, stack traces, or
internal implementation details.

## What is not yet supported

- Non-UTF-8 encodings (e.g. Windows-1252).
- Semicolon-separated files (`.csv` with `;` delimiter common in
  French/European Excel exports).
- Excel `.xlsx` files.
- Compressed archives (`.zip`, `.gz`).
- Multi-currency files where `currency` changes within the file — this
  is rejected because aggregation would be meaningless.

These may be added later, one at a time, each with a dedicated test.

## Sample file

A minimal valid file is available at `data/sample_costs.csv`. Use it as
a starting point when you want to test the importer or demo the app.