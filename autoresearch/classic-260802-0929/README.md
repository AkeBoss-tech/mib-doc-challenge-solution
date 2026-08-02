# Robustness-first extension

- Incumbent: `f0193ba`, score `131.300239`, zero CFA.
- Metric: official deterministic score, with extraction/classification/
  calibration components and catastrophic false approvals recorded separately.
- Acceptance: positive grouped gain, no new CFA, no overwrite regressions for
  structurally valid fields, and packet-family holdout evidence for learned
  overwrite branches.
- Boundary: rendered visible pixels and public labels only; no participant
  code, comments, layouts, thresholds, predictions, tests, or artifacts.

## Outcome

- Rejected a date-component classifier after it recovered only 1 of 112
  missing dates out of fold.
- Kept packet-wide sponsor recovery: 15 new exact values, split 4/11 across
  the two case halves, with no overwritten exact values.
- Kept strict name-pair recovery: 6 new exact values, split 4/2 across the two
  halves, with no overwritten exact values.
- Rejected the relaxed name threshold because it recovered only one exact name
  and none in the second half.
- Final targeted-merge score: `131.416906`; CFA: `0`.
