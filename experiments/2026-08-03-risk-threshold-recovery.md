# Conservative risk threshold recovery

The visible-pixel risk model was evaluated on the remaining `risk_flags=none`
outputs with a SHA-256 case-ID development/holdout split. Threshold `0.60`
was positive in both groups for the selected flags. This recovery can only add
risk evidence; it cannot create an approval, and a disqualifying recovered
flag may only move `NEEDS_REVIEW` to `DENIED`.

The full offline Docker evaluation used the public training PDFs, four CPUs,
no network, read-only input, and the official evaluator. It scored
`131.781560 / 150`, up `0.222222` from the calibrated `131.559338` baseline.
Extraction rose from `45.347778` to `45.570000`; classification and
calibration were unchanged. There were zero catastrophic false approvals.

All 57 unit tests passed before Docker evaluation. The change was retained
because it has grouped evidence, an independently reproduced full Docker
result, and preserves the approval-safety invariant.
