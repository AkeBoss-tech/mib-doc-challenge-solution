# Visible-OCR routing, selective orientation, and evidence consensus

## Independence boundary

This work is an original implementation using only rendered public pixels,
the public field manual and labels, the repository's existing code, and
official Tesseract/scikit-learn interfaces during training. Public participant
memos supplied only broad research themes. No participant code, thresholds,
layouts, test cases, predictions, case rules, or artifacts were inspected or
used.

## Accepted mechanisms

- Two compact character n-gram logistic models are trained from native visible
  OCR with digits masked. Runtime inference is dependency-free. Denial routing
  remains first and approval never overrides a denial.
- Approval recovery uses either the already-validated high threshold or a
  paired gate requiring complete clean policy fields, approval probability at
  least `0.75`, and an independent denial probability no greater than `0.30`.
  A second path lowers the approval threshold to `0.50` only with explicit
  native OCR evidence that biometric flags are none and a denial probability
  no greater than `0.40`.
- Sparse pages retry 90 and 270 degree orientations. A retry is accepted only
  when distinct public schema labels improve by at least two, reach at least
  three, and uniquely beat the other orientation. Rotated OCR is additive and
  may fill defaults, supply an exact public category, or add adverse risk; it
  cannot overwrite an already-valid ordinary field.
- Names and sponsors can use unique multi-source or three-read consensus.
  Visibly attributed sponsor prose can repair an unknown or closely matching
  OCR name, but cannot replace a dissimilar higher-authority name.
- Two additional revoked sponsors (`SPN-2718`, `SPN-9090`) were inferred from
  consistent labeled examples. Non-diplomatic uses deny after any explicit
  signed approval authority has had the opportunity to override.
- Confidence values were recalibrated by generic decision reason after the
  routing changes.

## Grouped safety measurements

- Selective orientation had no development field regressions, recovered one
  adverse development risk flag, improved eight holdout fields, and recovered
  one correct holdout approval with zero catastrophic false approvals.
- The paired approval gate was cross-fitted across five folds. The accepted
  complete-field gate recovered 15 approvals with five ordinary review errors
  and zero denied approvals out of fold. The explicit-biometric sub-gate
  recovered six approvals with zero review or denial errors out of fold.
- Name consensus improved both hash groups (25 correct changes in each, with
  two and one previously correct values displaced). Sponsor consensus improved
  both groups with nine total correct changes and no losses. The additional
  sponsor-sentence similarity gate recovered 48 correct names across the two
  groups with no previously correct name displaced.
- Diverse text-model ensembles, learned correctness calibration, aggressive
  approval thresholds, and a degraded-page enhancement retry were rejected.
  The aggressive approval option scored higher in-sample but produced denied
  approvals out of fold. The degraded retry had one small development gain,
  one bad applicant reading, and no holdout changes.

## Full official Docker gate

- Total: `124.514335 / 150`
- Extraction: `40.451111 / 50`
- Classification: `67.850000 / 80`
- Calibration: `16.213224 / 20`
- Mean confidence Brier: `0.0946694`
- Catastrophic false approvals: `0`
- Valid rows: `1000 / 1000`
- Confusion: 128 approved approvals, 409 denied denials, 267 correct reviews;
  two review-to-approval errors and no denied-to-approved errors.
- Runtime: approximately `1,341 s`, or `1.34 s/PDF`, within the official
  six-second average limit. Image size was `0.09 GiB`.

## Decision

Keep and commit this validated stack. It advances the previous independently
validated `119.462216` checkpoint by `5.052119` points while retaining zero
catastrophic false approvals. Do not push without explicit user approval.
