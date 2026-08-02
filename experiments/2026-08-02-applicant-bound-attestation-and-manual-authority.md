# Applicant-bound attestation and exact manual authority

## Independence boundary

This experiment is an original implementation based only on the public field
manual, rendered public training pixels, public labels for measurement, and the
repository's existing visible-OCR pipeline. It does not use participant code,
layouts, thresholds, tests, predictions, case rules, or artifacts.

## Hypothesis

Two narrow visible structures can recover evidence without relaxing approval
safety:

1. an exact sponsor-attributed sentence can bind its sponsor, applicant,
   public-vocabulary purpose, and visa class to one resolved applicant; and
2. an exact visibly labeled `Finding:` is authoritative even when damage makes
   the page-family classifier uncertain.

Approximate applicant-label reads are retained only as conflict evidence. They
never populate an output and veto an unrelated sponsor applicant in a
multi-applicant packet.

## Full official Docker result

- Candidate image: `akeboss-mib-128-final`
- Records: `1000 / 1000` valid
- Total: `125.593785 / 150`
- Extraction: `40.943333 / 50`
- Classification: `68.270000 / 80`
- Calibration: `16.380452 / 20`
- Catastrophic false approvals: `0`
- Runtime: `1284 s` total, `1.284 s/PDF`
- Prediction artifact: `/tmp/mib-128-final-full/predictions.jsonl`
- Evaluation artifact: `/tmp/mib-128-final-full/evaluation.json`

The accepted baseline was `124.514335`, so the measured gain is `+1.079450`.

## Changed-case audit

Every changed field or decision became correct; no formerly correct value was
displaced.

| Output | Changes | Development correct/wrong | Holdout correct/wrong |
|---|---:|---:|---:|
| applicant name | 45 | 21 / 0 | 24 / 0 |
| declared purpose | 26 | 17 / 0 | 9 / 0 |
| sponsor ID | 15 | 11 / 0 | 4 / 0 |
| visa class | 13 | 7 / 0 | 6 / 0 |
| adjudication | 6 | 3 / 0 | 3 / 0 |

All six adjudication changes were true approvals. There were no approval
removals and no false approval additions.

## Rejected follow-ups

- A learned-denial veto based on approval probability was rejected because its
  out-of-fold behavior routed denials rather than safely recovering approvals.
- A `2.2x` contrast top-left fee retry was rejected: development produced one
  correct change, while holdout produced one correct and two wrong changes.
  Both wrong reads were `unpaid`; the pipeline's separate-corroboration rule
  would block them, but the two safe `paid` recoveries did not alter decisions
  and did not justify the added runtime.

## Verdict

Keep and commit the applicant-bound attestation, exact manual authority, and
multi-applicant conflict safeguard. Continue from `125.593785` with
failure-only experiments; preserve approval recovery as a separate safety gate.
