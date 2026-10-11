# `data/` — synthetic demonstration data

## What's in this folder

- `demo_costs.csv` — synthetic daily AWS cost data used for local
  development, automated tests, and demos.
- This README.

## This data is SYNTHETIC

`demo_costs.csv` is NOT real customer data and must never be
presented as such. It is produced by `scripts/generate_demo_data.py`
with a fixed random seed, so the output is byte-identical on every run.

Any demo, screenshot, slide, or landing page that shows this file must
clearly label the data as **simulated**.

## Regenerating

From the project root, with the virtual environment active:

    python scripts\generate_demo_data.py

## Schema

One row per (day, service, region, usage type):

| Column           | Type   | Example                                  |
|------------------|--------|------------------------------------------|
| usage_date       | date   | 2026-05-01                               |
| service          | string | Amazon Elastic Compute Cloud - Compute   |
| region           | string | eu-west-1                                |
| usage_type       | string | BoxUsage                                 |
| usage_amount     | number | 437.5                                    |
| unit             | string | Hrs                                      |
| unblended_cost   | number | 42.0000                                  |
| currency         | string | USD                                      |

## Narrative baked into the data

To make later features demonstrable, the generator plants two
recognisable synthetic events:

- June 2026 — EC2 cost spike (+45% for the whole month): intended
  candidate for the "unusual increase in spending" recommendation.
- From 2026-07-15 — permanent EBS cost step (+60% and stays): intended
  candidate for a "review storage usage" recommendation.

These are synthetic anomalies. A real system would only hypothesise
based on cost data alone, and would require additional evidence
(resource inventory, utilisation metrics) before proposing any action.

## Real customer data

Real customer data is never committed. It must live outside this
folder — the root .gitignore already blocks data/real/ and
data/customer_*/.
