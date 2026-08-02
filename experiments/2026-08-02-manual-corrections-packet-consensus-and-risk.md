# Manual corrections, packet consensus, and exact adverse risk

## Independence boundary

This is an original visible-pixel implementation using the public field manual,
rendered public training PDFs, and public labels for measurement. No participant
code, thresholds, layouts, tests, predictions, case rules, or artifacts were
used.

## Accepted levers

- Parse exact visible `Manual correction:` prose into typed applicant, sponsor,
  visa, and fee candidates. Only one conflict-free value per field is accepted.
- Recover a default arrival date from one unique valid 2025–2026 ISO date in
  packet-wide visible OCR. This is applied after adjudication and therefore
  cannot unlock an approval or denial.
- Recover exact public adverse flags packet-wide after excluding visible
  barcode/instruction lines.
- Preserve exact `Finding: NEEDS_REVIEW` as higher authority than lower-priority
  denial facts and learned routers.
- Deny a non-diplomatic `SPN-4040` packet only when fee status is affirmatively
  `paid`; the conjunction changed one development and one holdout case, both
  true denials.

## Full official Docker result

- Candidate image: `akeboss-mib-129`
- Records: `1000 / 1000` valid
- Total: `126.381740 / 150`
- Extraction: `41.450000 / 50`
- Classification: `68.460000 / 80`
- Calibration: `16.471740 / 20`
- Catastrophic false approvals: `0`
- Runtime: `1318 s` total, `1.318 s/PDF`
- Prediction artifact: `/tmp/mib-129-full/predictions.jsonl`
- Evaluation artifact: `/tmp/mib-129-full/evaluation.json`

The accepted baseline was `125.593785`, so the measured gain is `+0.787955`.

## Changed-case audit

| Output | Changes | Development correct/wrong | Holdout correct/wrong | Correct lost |
|---|---:|---:|---:|---:|
| applicant name | 1 | 1 / 0 | 0 / 0 | 0 |
| arrival date | 57 | 25 / 0 | 27 / 5 | 0 |
| risk flags | 19 | 7 / 1 | 9 / 2 | 0 |
| sponsor ID | 23 | 14 / 0 | 9 / 0 | 0 |
| adjudication | 3 | 1 / 0 | 2 / 0 | 0 |

The five still-wrong dates and three partial risk values replaced fields that
were already wrong; they displaced no correct output. All three adjudication
changes became correct, with no approval additions or removals.

## Rejected probes

- Multiclass risk text routing: no confidence/margin setting produced positive
  exact-field gain in both out-of-fold groups.
- Low-resolution visible-thumbnail routing: the best out-of-fold approval route
  recovered four approvals and misrouted one review (`+17` raw classification
  points), too small to justify exporting a runtime model.
- Biometric-confidence thresholds: illegibility and clean cases overlapped from
  the 60s through the 90s; no defensible threshold existed.
- Packet-wide sponsor fallback: unique visible sponsors still belonged to the
  wrong applicant in five cases, so the fallback remains disabled.

## Verdict

Keep and commit the deterministic evidence changes. Continue from
`126.381740`, preserving zero catastrophic false approvals and separating all
future extraction fallbacks from approval evidence.
