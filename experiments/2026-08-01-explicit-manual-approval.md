# Explicit manual approval authority

- **Date and intended commit:** 2026-08-01, first approval recovery stage.
- **Hypothesis:** a precisely labeled approval on a visible manual adjudicator
  note can safely authorize approval when the packet also passes independent
  core, fee, sponsor, risk, and completeness gates.
- **Independent mechanism:** `exact_manual_finding` accepts only a complete
  `Finding` label/value on a page classified from visible OCR as a manual note.
  `SAMPLE DENIAL` and unlabeled verdict words are excluded. Any conflicting
  labeled finding blocks approval. Existing denial and review evidence is
  evaluated before the approval gate.
- **Approval requirements:** exact manual approval; no disqualifying or review
  risk flag; known acceptable fee; known visa and arrival date; a nondefault,
  nonrevoked sponsor unless `DIP-1`; and affirmative clean biometric evidence
  for `MED-3`. Missing or contradictory evidence remains review.
- **Data protocol:** development and disjoint holdout each contain 150 packets
  currently emitted as review, balanced equally across actual approved,
  denied, and review outcomes. Thus each cohort contains 100 direct
  false-approval controls. The frozen production gate then ran over all 1,000
  public training packets in the official Docker contract.
- **Shadow result:** one exact labeled approval appeared in 8/50 development
  approvals and 9/50 holdout approvals, with zero occurrences among 200
  denied/review controls. Requiring both OCR variants to agree was safe but
  lower recall and remained shadow-only.
- **Production development result:** three approval additions, all correct;
  +1.535422 total, zero CFA.
- **Production holdout result:** three approval additions, all correct;
  +1.443914 total, zero CFA.
- **Full total score:** 102.916176 / 150 versus 102.516328, +0.399848.
- **Full component scores:** extraction 37.535556 / 50 (unchanged);
  classification 52.07 / 80 (+0.36); calibration 13.310620 / 20
  (+0.039848); missing penalty 0.
- **Full decisions:** six `NEEDS_REVIEW` to `APPROVED` changes, all six correct;
  zero incorrect changes, zero approval removals, and zero catastrophic false
  approvals.
- **Runtime:** approximately 966 seconds including the negligible cached image
  build, or 0.97 seconds/PDF; image size 0.09 GiB. The evidence gate adds no OCR.
- **Verification:** 28 unit tests, compilation, `git diff --check`, balanced
  three-outcome shadow analysis, frozen development and holdout production
  evaluation, official full Docker runner/validator/evaluator, and complete
  approval-by-truth audit.
- **Decision:** keep as the only approval path. Broader approval recovery stays
  disabled until additional independent note/evidence readers clear the same
  grouped and zero-CFA gates.
