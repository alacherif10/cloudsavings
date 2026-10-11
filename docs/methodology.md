# CloudSavings — Methodology

## Why this document exists

CloudSavings makes claims about money. Claims about money are worth
nothing unless the underlying method is explicit, reproducible, and
honest about its limits.

This document explains **what each figure means, how it is computed,
and what it does not prove**. It is written for a non-technical reader
(CTO, DAF, consultant) who wants to audit our numbers.

## The four levels of "savings"

The most common source of misunderstanding in cloud cost optimization
is conflating four very different things. We keep them separate.

### 1. Potential savings (an estimate)

Produced by the recommendation engine from **cost data alone**.

- It is an **upper bound** on what could plausibly be saved if a
  recommendation is investigated and acted upon.
- It is **not** verified. Nothing in the bill has changed yet.
- It is **not** billable under any contract.

**Example:** the engine sees EC2 spend rise 45 % in June relative to
the previous months and estimates a potential saving of $742/month.
That figure means "the delta between June and the trend is $742". It
does **not** mean "$742 is recoverable".

### 2. Observed savings (a measured reduction)

The difference in cost between a **baseline window** and an **after
window**, normalised to a 30-day month.

- It is **real**: the bill actually went down (or up).
- It is **not necessarily caused by us**. Workloads change on their
  own. Seasons exist. Migrations happen.
- It is **not** billable.

**Formula:** `observed = baseline_monthly_cost − after_monthly_cost`,
clamped at zero if the result is negative.

### 3. Verified savings (attributable to an approved intervention)

The portion of the observed saving that we can **attribute** to a
specific intervention, given three conditions:

1. The customer **declared** the intervention, with a date, a service,
   a description, and an "approved by" name.
2. The intervention is recorded on the **same service** as the cost
   drop. A drop on S3 is never attributed to an intervention on EC2.
3. The attributed amount is **capped** at the customer's declared
   `expected_monthly_saving_usd`. If the drop exceeds the declared
   expectation, only the declared amount is verified; the excess
   remains observed.

Additionally, the **sum** of verified savings across interventions
cannot exceed the total observed saving. This prevents two
interventions from double-counting the same cost drop.

### 4. Eligible for billing (a contractual subset)

The subset of verified savings that matches the **contract rules**.

In the current MVP, eligible equals verified. A future version will
add per-service caps, excluded services, minimum thresholds, and
billing-period constraints.

## Monthly normalisation

All monetary figures are expressed **per 30-day month**. A window of
20 days at $200 becomes $300/month. This lets us compare windows of
different lengths without distortion.

**Why 30 and not the exact number of days in the month?** Because the
alternative — using the calendar month — would make short windows
produce misleading figures, and long windows would be dominated by
month-length noise. 30 is the standard approximation and is documented
here so no reader is surprised.

**Where it appears:**

- `src/savings.py` (function `_monthly_cost`)
- `src/reports.py` (documented in the report's "Assumptions" section)

## Minimum window length

Savings measurement requires **at least 14 days per window**. Below
that, cost variance from a single event would dominate the figure and
make the result meaningless.

If a caller passes a shorter window, `measure_savings` raises a
`ValueError` rather than producing a misleading number.

## Recommendation rules — thresholds and philosophy

Five rules currently run. Each has an explicit threshold, documented in
`src/recommendations.py`. We summarise them here.

### Rule `unusual_increase`

**Trigger:** a service's cost in a month is at least **1.30×** the mean
of the preceding months, with at least two preceding months available.

**Estimated saving:** the delta between that month and the preceding
mean.

**Confidence:** low.

**Cap:** the top 3 results by absolute delta are kept. This avoids
flooding the user when the dataset has monotonic growth (which would
otherwise flag almost every month).

### Rule `step_change`

**Trigger:** a service's daily cost jumps by at least **1.35×** between
two consecutive 14-day windows and stays there.

**Estimated saving:** the daily delta × 30.

**Confidence:** medium.

### Rule `concentration`

**Trigger:** a single service accounts for at least **40 %** of total
spend.

**Estimated saving:** **0**. Concentration is a signal, not an
opportunity. Attaching a dollar figure would be misleading.

**Confidence:** low.

### Rule `daily_outlier`

**Trigger:** a single-day cost exceeds the mean by at least **3
standard deviations**, in a series of at least 30 days.

**Estimated saving:** the excess over the mean (not over the
threshold).

**Confidence:** low.

### Rule `storage_review`

**Trigger:** the service is a storage family (S3, EBS, Glacier, EFS)
and its average monthly cost is at least **$10**.

**Estimated saving:** **15 %** of the average monthly cost, presented
as a conservative upper bound. Real savings require inspecting access
patterns and lifecycle configuration, which we do not have.

**Confidence:** low.

## What we do **not** claim

- We do **not** claim a resource is unused based on cost data alone.
  That claim would require resource inventory and utilisation metrics,
  which the MVP does not have.
- We do **not** claim that a cost drop was caused by our work unless
  the customer declared an intervention on the affected service.
- We do **not** sum potential and verified savings. They are different
  quantities.
- We do **not** propose to modify any resource automatically. Every
  change requires human approval.

## Sources of uncertainty (acknowledged)

- **Attribution is inherently fuzzy.** Even with a declared
  intervention, part of a cost drop may come from unrelated factors
  (workload change, seasonality, currency). Our cap mechanism is a
  conservative answer to this: we only claim what the customer
  pre-declared.
- **Cost data alone is not proof of waste.** A high EC2 bill may be
  entirely justified by the business. Our recommendations are
  signals, and each one lists the additional evidence required.
- **Storage-class savings are estimates.** 15 % is a defensive
  heuristic, not a measurement.
- **Currency is not converted.** All figures stay in the currency of
  the uploaded file. Multi-currency datasets are rejected.

## Worked example

A customer has:

- Baseline window: 2026-01-01 to 2026-01-31 (31 days, $930 total).
- After window: 2026-03-01 to 2026-03-31 (31 days, $620 total).
- One approved intervention: on 2026-02-15, on EC2, expected monthly
  saving $250.

**Step 1 — Normalise.**

- Baseline monthly cost: `930 / 31 × 30 = $900.00`.
- After monthly cost: `620 / 31 × 30 = $600.00`.

**Step 2 — Observed.**

`900 − 600 = $300/month observed`.

**Step 3 — Verified (per intervention).**

- EC2-specific drop: suppose the EC2-only baseline was $600 and the
  after is $400, a drop of $200 → capped at the declared $250 → $200.
- Sum across interventions: $200.
- Global cap: $200 ≤ $300 observed → kept at $200.

**Step 4 — Eligible for billing.**

`Eligible = $200/month` (MVP: equals verified).

**Step 5 — Fee simulation (illustrative).**

At 20 % on a 1-month period: `$200 × 20 % = $40`.

The reader can reproduce every step by hand. That is the goal.

## Reporting

The Markdown report (`src/reports.py`) embeds this methodology in its
sections 6 (Assumptions) and 7 (Limitations). If you change the
methodology, update both this file **and** the report sections in the
same commit.

## Legal, tax, and contractual note

This document describes a **calculation methodology**, not a
contract. Pricing, invoicing, VAT, and the legal enforceability of any
RaaS arrangement are outside the scope of this tool and must be
settled separately, with professional advice, before any customer is
billed.