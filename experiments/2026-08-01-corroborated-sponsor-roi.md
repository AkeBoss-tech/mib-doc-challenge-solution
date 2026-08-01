# Corroborated sponsor ROI fallback

- **Date and intended commit:** 2026-08-01, sponsor ROI promotion stage.
- **Hypothesis:** when whole-page extraction has no sponsor, two distinct native
  crops from exact visible sponsor anchors can recover `SPN-####` safely if they
  agree, have no conflict, and both clear a grouped-validated OCR quality gate.
- **Generic mechanism changed:** pages whose ordinary visible OCR contains the
  word `sponsor` receive a TSV geometry pass. Only sponsor proposals are read in
  production. A fallback is accepted only for the default `SPN-0000`, from two
  distinct native crops, exact anchors (`1.0`), identical normalized values,
  no ledger conflict, and OCR quality at least 85 for both. Rescaled-only,
  single-source, conflicting, or weak evidence remains shadow-only.
- **Data split / grouping protocol:** a deterministic 100-packet development
  split and disjoint 100-packet holdout each contain 25 packets from every
  visible page-count group (three through six pages). The acceptance rule and
  quality threshold were fixed after development, tested unchanged on holdout,
  then evaluated on all 1,000 public training packets.
- **Shadow measurements:** development trace runtime 129.85 seconds and holdout
  trace runtime 132.24 seconds. The ledger produced 18 eligible sponsor
  fallbacks across the two groups; all 18 matched public truth.
- **Holdout production result:** seven sponsor changes, all correct; total score
  104.690747 versus 104.287778 (+0.402969); extraction +0.388889,
  calibration +0.014080, classification unchanged; 0 CFA and 0 decision or
  approval changes. Runtime 88.36 seconds, or 0.88 seconds/PDF.
- **Full total score:** 101.728369 / 150 versus 101.330381, +0.397988.
- **Full component scores:** extraction 37.113333 / 50 (+0.394444);
  classification 51.41 / 80 (unchanged); calibration 13.205036 / 20
  (+0.003544); missing penalty 0.
- **Valid rows:** 1,000/1,000; no missing, extra, duplicate, or invalid rows.
- **Catastrophic false approvals:** 0 before and 0 after.
- **Approval additions/removals:** 0/0; all adjudications were unchanged.
- **Sponsor changes:** 71, all 71 correct and none incorrect.
- **Runtime:** approximately 829 seconds total, or 0.83 seconds/PDF, under the
  official 6-second average budget; image size remained 0.09 GiB.
- **Verification:** 21 unit tests, compilation, `git diff --check`, development
  and disjoint-holdout trace audits, holdout production Docker evaluation, full
  official Docker runner/validator/evaluator, field diff, decision diff, and
  complete changed-sponsor truth review.
- **Decision:** keep. Other ROI fields remain shadow-only because their evidence
  did not yet meet equivalent grouped precision and recovery-volume gates.
