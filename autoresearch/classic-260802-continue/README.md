# Post-submission improvement cycle

- Baseline full public-training score: 131.55271377777774 / 150; CFA: 0.
- Objective: identify one independent visible-pixel improvement with positive grouped public-training evidence and zero CFA.
- Boundary: no participant code, comments, layouts, thresholds, tests, predictions, case rules, or artifacts.
- Acceptance: positive holdout gain, no new CFA, full regression and unit tests before commit.

## Rejected follow-up

An analysis-only visible-OCR approval-router sweep evaluated character, word,
and hybrid n-gram variants with out-of-fold scoring. Its strongest candidate
recovered nine approvals but also misrouted six true review cases. It had no
catastrophic false approval in that aggregate measurement, but did not provide
the required positive grouped evidence. No runtime rule or model was exported.
