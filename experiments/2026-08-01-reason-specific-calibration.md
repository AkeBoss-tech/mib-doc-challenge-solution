# Reason-specific confidence calibration

## Hypothesis

The adjudication branches have materially different correctness rates, so one
narrow confidence range for nearly every `NEEDS_REVIEW` path is not calibrated.
Confidence can be improved without changing extraction or adjudication by
estimating correctness separately for each generic decision reason.

## Method

The committed 1,000-packet predictions were divided into five deterministic
SHA-256 case-ID folds for measurement only. Each existing confidence value maps
one-to-one to a generic decision branch. Fold accuracies were inspected for
stability and the full public-train mean was rounded to two decimals; no case
IDs, field values, layouts, or participant artifacts enter runtime behavior.

The largest cohorts were stable across folds: fallback review was 0.158-0.275,
missing-core review was 0.196-0.357, explicit review evidence was 0.893-1.000,
and denial evidence was 0.936-1.000. Small cohorts were rounded conservatively.

## Candidate

| Decision path | Old | Candidate |
| --- | ---: | ---: |
| denial evidence | 0.91 | 0.97 |
| explicit review evidence | 0.58 | 0.94 |
| missing core evidence | 0.48 | 0.28 |
| invalid/missing sponsor | 0.46 | 0.02 |
| unsupported waiver | 0.45 | 0.27 |
| clean but unauthorized approval path | 0.40 | 0.04 |
| generic fallback review | 0.42 | 0.21 |
| explicit gated approval | 0.93 | 0.96 |

## Preliminary result

Re-scoring the unchanged official prediction decisions with these confidences
reduces mean Brier error from `0.1672345` to `0.1163556`. Calibration rises
from `13.310620` to `15.345776`, a `+2.035156` point gain. Classification,
extraction, approvals, and catastrophic false approvals are unchanged.

The candidate still requires a fresh Docker prediction run and official full
evaluation before it is eligible to commit.
