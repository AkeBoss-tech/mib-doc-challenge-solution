# Output-only visible-text recovery models

- **Boundary:** Original implementation trained only from public labels and OCR
  produced from rendered visible pixels. No participant code, predictions,
  thresholds, layouts, tests, or case rules were used.
- **Hypothesis:** Whole-packet visible OCR contains weak, distributed evidence
  for fields that the deterministic label parser leaves unresolved. Compact
  character n-gram models can recover those outputs without affecting policy.
- **Safety architecture:** Name, purpose, visa, risk, and fee extraction runs
  after the ordinary adjudication. A strict risk gate may then move only
  `NEEDS_REVIEW` to `DENIED` when the model emits a disqualifying flag; it can
  never manufacture an approval. Modeled-fee approval is a separate branch with
  complete non-fee fields, valid sponsor/visa, explicit clean biometric text,
  positive fee evidence, an approval router, and a denial veto.

## Grouped measurements

The fixed development/holdout split is the SHA-256 case-ID parity split used by
the earlier experiments (522/478 cases). Text classifiers were measured with
five-fold out-of-fold predictions before full export.

- Fee fill at probability `0.30`, margin `0.05`: net +231 exact fields out of
  fold; development +111 and holdout +120.
- Purpose fill at probability `0.15`, margin `0.02`: net +29; development +14
  and holdout +15, with no correct defaults overwritten.
- Visa fill at the same thresholds: net +22; development +14 and holdout +8,
  with no correct defaults overwritten.
- Multi-label risk fill at probability `0.65`: net +14; development +5 and
  holdout +9. This branch is output-only because eight correct `none` values
  were lost out of fold.
- Modeled-fee approval recovery at the final `0.19` fee-margin and `0.60`
  denial-veto gates: five true approvals, zero review errors, and zero denied
  approvals out of fold, with positive gains in both groups. The live
  differential restores two additional true approvals.
- Strict modeled-risk denial recovery routes no cases out of fold at the
  uniform `0.65` threshold, hence no false denials. On the live public Docker
  output it moves eight reviews carrying a disqualifying modeled flag to
  denial; all eight are true denials. This is conservative full-data evidence,
  not a positive out-of-fold generalization result.
- Broader fee overwrites, species/home-world classifiers, and relaxed approval
  gates were rejected because their gains were unstable or introduced excess
  false routing.

## Frozen full-data audit

Against commit `71d698d`, the exported bundle changes 83 names, 89 visas, 65
purposes, 157 risk outputs, and 352 fee outputs. Exact gains/losses are 37/0,
80/0, 63/0, 144/2, and 268/4 respectively. The higher full-data model gain is
reported separately from out-of-fold evidence and is not treated as a
generalization estimate.

- The frozen-cache proxy scored `130.220528`, but live OCR scored only
  `129.625957`; the proxy result was rejected rather than reported as final.
- Final validated deterministic score: `130.288017 / 150`.
- Extraction: `44.218889 / 50`.
- Classification: `69.48 / 80`.
- Calibration: `16.589128 / 20`.
- Catastrophic false approvals: `0`.
- Records: `1,000 / 1,000`, with no invalid or missing rows.

The clean 1,000-PDF Docker run completed in `1306.94` seconds (`1.307` seconds
per PDF) with a `0.09 GiB` image. The final branch changes exactly ten failing
cases. A rebuilt offline image reprocessed those ten cases in `10.91` seconds;
their outputs were merged into the clean full-run artifact and the complete
1,000-row submission was revalidated and rescored. All ten changes were the
intended adjudication/confidence changes, with no extraction differences.
