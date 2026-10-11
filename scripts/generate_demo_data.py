"""Generate synthetic AWS cost data for CloudSavings demos.

The output file is intentionally synthetic and MUST NOT be presented as
real customer data. It is used for:
  - local development of the CSV importer and dashboard
  - automated tests
  - demos shown to potential customers, clearly labelled as simulated

The dataset is deterministic: running this script multiple times produces
identical output because we fix the random seed.

Schema (one row per day per service per region):

    usage_date      ISO date (YYYY-MM-DD)
    service         AWS service name (e.g. "Amazon Elastic Compute Cloud - Compute")
    region          AWS region code (e.g. "eu-west-1")
    usage_type      short usage-type label (e.g. "BoxUsage", "TimedStorage")
    usage_amount    numeric quantity of usage
    unit            unit of measure (e.g. "Hrs", "GB-Mo")
    unblended_cost  cost in the given currency
    currency        ISO currency code (currently only "USD")
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

# ---- Configuration ---------------------------------------------------------

SEED = 20260501
START_DATE = date(2026, 4, 1)
END_DATE = date(2026, 9, 30)  # inclusive — six complete months
REGION = "eu-west-1"
CURRENCY = "USD"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "demo_costs.csv"

# (service_name, usage_type, unit, base_daily_cost_usd, daily_growth, daily_noise)
SERVICES: list[tuple[str, str, str, float, float, float]] = [
    ("Amazon Elastic Compute Cloud - Compute", "BoxUsage",          "Hrs",        42.00, 0.0020, 0.08),
    ("Amazon Simple Storage Service",          "TimedStorage",      "GB-Mo",       6.50, 0.0040, 0.05),
    ("Amazon Relational Database Service",     "InstanceUsage",     "Hrs",        22.00, 0.0000, 0.05),
    ("AWS Lambda",                             "GB-Seconds",        "GB-Seconds",  1.20, 0.0100, 0.15),
    ("Amazon CloudFront",                      "DataTransfer-Out",  "GB",          4.80, 0.0000, 0.07),
    ("Amazon Elastic Block Store",             "EBS:VolumeUsage",   "GB-Mo",       9.00, 0.0010, 0.05),
    ("AWS Data Transfer",                      "DataTransfer-Out",  "GB",          3.20, 0.0030, 0.10),
    ("Amazon ElastiCache",                     "NodeUsage",         "Hrs",         5.50, 0.0000, 0.05),
]

# (service_name, start_date, end_date_or_None, multiplier)
# These plant recognisable anomalies for the recommendation engine.
# They are SYNTHETIC narrative — not real findings.
EVENTS: list[tuple[str, date, date | None, float]] = [
    # June 2026: EC2 cost +45% for the whole month (unusual-increase candidate)
    ("Amazon Elastic Compute Cloud - Compute", date(2026, 6, 1), date(2026, 6, 30), 1.45),
    # From mid-July 2026 onwards: EBS cost +60% and stays (storage-review candidate)
    ("Amazon Elastic Block Store",             date(2026, 7, 15), None,              1.60),
]


# ---- Helpers ---------------------------------------------------------------

def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _event_multiplier(service: str, day: date) -> float:
    m = 1.0
    for name, start, end, mult in EVENTS:
        if name != service:
            continue
        if day < start:
            continue
        if end is not None and day > end:
            continue
        m *= mult
    return m


def _unit_cost(usage_type: str) -> float:
    """Rough real-world unit price, only used to make usage_amount plausible."""
    return {
        "BoxUsage":         0.096,
        "TimedStorage":     0.000767,   # $0.023 / GB-month, expressed per day
        "InstanceUsage":    0.115,
        "GB-Seconds":       0.0000166667,
        "DataTransfer-Out": 0.085,
        "EBS:VolumeUsage":  0.0002667,  # $0.08 / GB-month, expressed per day
        "NodeUsage":        0.068,
    }.get(usage_type, 0.1)


# ---- Generator -------------------------------------------------------------

def generate() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []

    for day_index, day in enumerate(_daterange(START_DATE, END_DATE)):
        # Business-day pattern: EC2 and Data Transfer dip on weekends.
        weekday_factor = 0.85 if day.weekday() >= 5 else 1.0

        for service, usage_type, unit, base, growth, noise in SERVICES:
            trend = base * ((1.0 + growth) ** day_index)
            jitter = 1.0 + rng.uniform(-noise, noise)
            event = _event_multiplier(service, day)

            weekend_services = {
                "Amazon Elastic Compute Cloud - Compute",
                "AWS Data Transfer",
            }
            weekend = weekday_factor if service in weekend_services else 1.0

            cost = trend * jitter * event * weekend
            cost = round(max(cost, 0.01), 4)

            amount = round(cost / _unit_cost(usage_type), 4)

            rows.append({
                "usage_date":     day.isoformat(),
                "service":        service,
                "region":         REGION,
                "usage_type":     usage_type,
                "usage_amount":   amount,
                "unit":           unit,
                "unblended_cost": cost,
                "currency":       CURRENCY,
            })

    return rows


def main() -> None:
    rows = generate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "usage_date", "service", "region", "usage_type",
        "usage_amount", "unit", "unblended_cost", "currency",
    ]
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(r["unblended_cost"] for r in rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"Total synthetic cost: {total:,.2f} {CURRENCY}")
    print(f"Date range: {START_DATE} .. {END_DATE}")
    print("This file is SYNTHETIC. Do not present it as real customer data.")


if __name__ == "__main__":
    main()