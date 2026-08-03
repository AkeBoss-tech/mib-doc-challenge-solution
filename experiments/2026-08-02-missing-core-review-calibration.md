# Missing-core review calibration

- **Boundary:** Original runtime and public training labels only. No participant
  source, comments, predictions, layouts, thresholds, tests, case rules, or
  artifacts were used.
- **Hypothesis:** The generic `NEEDS_REVIEW` branch for packets missing a core
  visa, arrival-date, or fee fact is under-confident at `0.45`.
- **Safety:** The change is confidence-only. It cannot change field extraction,
  adjudication, or create an approval.

## Grouped evidence

The fixed SHA-256 case-ID split contains 141 records on this branch. Their
decision correctness was 50.7% in development (69 records) and 47.2% in
holdout (72 records). Moving the branch confidence from 0.45 to 0.47 reduces
Brier loss in both groups; no group-specific value is used at runtime.

## Docker differential and full score

The rebuilt offline Docker image reprocessed all 141 affected PDFs. Every
record preserved all extracted fields and its adjudication; each changed only
from confidence `0.45` to `0.47`. Merging those verified records into the
complete canonical Docker baseline produced:

- Total: `131.559338 / 150` (baseline `131.552714`, gain `+0.006624`)
- Extraction: `45.347778 / 50` (unchanged)
- Classification: `69.600000 / 80` (unchanged)
- Calibration: `16.611560 / 20` (baseline `16.604936`)
- Catastrophic false approvals: `0`

The unit suite passed (57 tests). This is a small, grouped-supported
calibration improvement; it does not imply a larger decision-quality gain.
