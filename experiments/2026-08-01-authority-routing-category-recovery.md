# Authority routing and structured category recovery

## Hypothesis

A step change is possible by combining four independently measured mechanisms:

1. exact visible manual-note authority with unresolved-note vetoes;
2. trusted staleness and strict sponsor denial facts;
3. affirmative clean biometric/paid-fee approval evidence;
4. unique exact or uniquely near-exact public category values found in OCR text
   already produced by the pipeline.

## Grouped development results

The approval gates were measured on deterministic SHA-256 development and
holdout cohorts. Exact manual approvals and the clean-evidence gate produced no
false approvals in either cohort. Trusted staleness and strict sponsor rules
were also zero-error in their grouped cohorts. Category recovery was evaluated
across review and non-review slices: species, home-world, and default-purpose
promotions produced zero wrong changes.

Rejected alternatives included generic label ROI promotion, global rotation,
high-resolution whole-page OCR, an official `tessdata_best` pass, unrestricted
unique sponsor/date patterns, and uncorroborated unpaid-fee evidence.

## Full Docker gate

Command shape:

```text
docker run --rm --network none --cpus 4 --memory 8g --read-only \
  --tmpfs /tmp:rw,size=4g --mount input:ro --mount output:rw \
  akeboss-mib-goal120 /input /output/predictions.jsonl
```

Results on the complete 1,000-packet public training set:

- total: `111.791176` (`+8.875000` from `102.916176`)
- extraction: `39.245556 / 50`
- classification: `57.050000 / 80`
- calibration: `15.495620 / 20`
- mean Brier error: `0.1126095`
- catastrophic false approvals: `0`
- valid rows: `1000 / 1000`
- runtime: `1214.98 s`, or `1.21498 s/PDF`

Relative to the incumbent, the candidate made 51 correct review-to-approval
changes and 32 correct review-to-denial changes, with no incorrect decision
changes. Every one of its 57 approvals was a true approval.

Field changes were also regression-free: 164 species, 98 home worlds, 11
purposes, and 8 fee statuses became correct, with zero correct fields lost.

## Decision

Keep and commit as the new validated incumbent. Continue autoresearch toward
the pinned `>=120` objective; do not push without explicit user approval.
