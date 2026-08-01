# Strict visible-anchor fee band

- **Date and intended commit:** 2026-08-01, fee ROI promotion stage.
- **Hypothesis:** a single normalized top-left crop can recover tiny receipt
  text missed by whole-page OCR if promotion requires an exact visible fee
  anchor, a closed status reading, and conflict refusal.
- **Independent mechanism:** every rendered page receives one crop spanning the
  left 60% and top 25%, derived from direct inspection of official rendered
  failures. Tesseract PSM 11 reads the crop. Production fills only an existing
  `unknown` fee value and never overwrites stronger extracted evidence.
- **Acceptance rules:** the same crop must contain exact `Fee Receipt` or
  `Fee Status` text. Exact `paid`, `waived`, or `unpaid` values are accepted;
  a uniquely separated short OCR variant may normalize to the closed public
  fee vocabulary. A visible `$809` receipt can recover `paid`. `$0` is not
  interpreted as a waiver. `unpaid` additionally requires the separate
  whole-page OCR family to read the exact same status. Conflicting valid crop
  readings fail closed.
- **Data protocol:** development and disjoint holdout each contain 60 packets
  whose committed fee output was `unknown` despite a scorable visible status.
  Both cohorts were stratified across paid, waived, unpaid, and visible page
  counts from three through six. The frozen candidate then ran through the
  official 1,000-PDF Docker contract.
- **Rejected branches:** the existing generic TSV proposal found a fee anchor
  in only 3/60 development failures and was discarded. A broad top band found
  4/60. Overlapping tiles and unconditional rotations added little recall. A
  first production candidate inferred `waived` from `$0`; the full gate found
  19 unsupported changes, so the inference was removed. One contradictory
  printed `unpaid` value was rejected by requiring whole-page corroboration.
- **Development result:** nine fee changes, all nine correct; +0.730667 total,
  no decision changes, zero CFA.
- **Disjoint holdout result:** seven fee changes, all seven correct; one correct
  `NEEDS_REVIEW` to `DENIED` recovery; +1.685319 total, zero regressions and
  zero CFA.
- **Full total score:** 102.516328 / 150 versus 102.223520, +0.292808.
- **Full component scores:** extraction 37.535556 / 50 (+0.2);
  classification 51.71 / 80 (+0.06); calibration 13.270772 / 20
  (+0.032808); missing penalty 0.
- **Full changes:** 45 fee recoveries (35 paid, nine waived, one unpaid), all
  45 correct with zero field regressions. The sole decision change recovered a
  true denial; zero incorrect decision changes and zero approval additions.
- **Catastrophic false approvals:** 0 before and 0 after.
- **Runtime:** approximately 970 seconds including the negligible cached image
  build, or 0.97 seconds/PDF; image size 0.09 GiB.
- **Verification:** 26 unit tests, compilation, `git diff --check`, shadow
  ledger audit, rendered-pixel failure inspection, frozen development and
  holdout evaluation, two full Docker gates (one rejected, one accepted),
  official validation/evaluation, and complete changed-field/decision audits.
- **Decision:** keep the strict fee band. Approval recovery remains disabled
  and moves next to a balanced three-outcome shadow study.
