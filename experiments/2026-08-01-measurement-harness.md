# Reproducible experiment measurement harness

- **Date and intended commit:** 2026-08-01, measurement-harness stage.
- **Hypothesis:** one deterministic report can prevent score-only promotion by
  putting extraction, adjudication, calibration, approval deltas, trace cost,
  conflicts, and CFA in the same review artifact.
- **Generic mechanism changed:** a read-only development CLI now validates
  unique JSONL case IDs; compares candidate and baseline fields/decisions;
  reports per-field accuracy, decision confusion, correctness Brier score, and
  predicted approval versus reference denial; accepts an explicit leakage-safe
  group map; and summarizes proposals, candidates, retries, retry recoveries,
  ledger selections, conflicts, and runtime. It does not infer groups or alter
  runtime predictions.
- **Split/grouping protocol:** group membership must be supplied as a JSON
  object from case ID to group. Missing comparable IDs fail the grouped report.
  No group was inferred or fitted for the synthetic smoke packet.
- **Command:**

  ```sh
  python tools/measure_experiment.py \
    --predictions /tmp/mib-shadow-smoke/output/traced-final.jsonl \
    --baseline /tmp/mib-shadow-smoke/output/plain-final.jsonl \
    --trace-dir /tmp/mib-shadow-smoke/output/traces \
    --runtime-seconds 2.44
  ```

- **Score and components:** official total/component score not measured because
  the public reference and evaluator are absent. The harness reported all nine
  candidate fields unchanged relative to baseline.
- **Valid rows:** 1/1 synthetic row.
- **Catastrophic false approvals:** 0 changes on the unlabeled baseline
  comparison; absolute CFA is not asserted. With reference data, the harness
  explicitly counts `APPROVED` predictions whose reference is `DENIED`.
- **Approval additions/removals:** 0/0; adjudication changes: 0.
- **Runtime per PDF:** 2.44 seconds for the trace-mode synthetic Docker run.
- **Trace components:** one page, 10 region proposals, 11 candidates, nine
  valid candidates, one retry read, zero retry recoveries, nine selected ledger
  fields, and zero conflicting values.
- **Verification:** 17 unit tests, compilation of runtime and measurement CLI,
  a real report over the Docker smoke artifacts, and `git diff --check`.
- **Decision:** keep. Use this report format for later grouped ROI promotion
  experiments; do not claim official score or CFA without official references.
