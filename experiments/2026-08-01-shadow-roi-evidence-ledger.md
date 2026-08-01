# Shadow ROI reader and evidence ledger

- **Date and intended commit:** 2026-08-01, shadow ROI/ledger stage.
- **Hypothesis:** label-span crops can recover visible same-line values, and a
  provenance-preserving ledger can expose conflicts, without changing the
  production extraction or adjudication path.
- **Generic mechanism changed:** anchors now end at the matched label tokens,
  not at the end of the whole OCR line. Trace mode reads the resulting crop
  once with Tesseract TSV and retries once at 2x scale only when field-type
  normalization rejects the native reading. At most two proposals per field
  per page are retained. All raw readings, transforms, OCR/anchor quality,
  selections, reason codes, and conflicts remain in a shadow evidence ledger.
- **Split/grouping protocol:** no label-derived constant or policy behavior was
  fitted. Pure-data tests use synthetic word geometry, and the container smoke
  test uses one generated visible-pixel form. Official grouped public data was
  not present in the checkout and was not discoverable from the official
  repository, so this stage is not eligible to influence predictions.
- **Docker commands:**

  ```sh
  docker build -t akeboss-mib-shadow-roi .
  docker run --rm --network none \
    --mount type=bind,src=<synthetic-input>,dst=/input,readonly \
    --mount type=bind,src=<output>,dst=/output \
    akeboss-mib-shadow-roi /input /output/plain.jsonl
  docker run --rm --network none \
    --mount type=bind,src=<synthetic-input>,dst=/input,readonly \
    --mount type=bind,src=<output>,dst=/output \
    -e MIB_TRACE_DIR=/output/traces \
    akeboss-mib-shadow-roi /input /output/traced.jsonl
  cmp <output>/plain.jsonl <output>/traced.jsonl
  ```

- **Score and components:** not measured; no official public labels or
  evaluator were available locally. This stage is shadow-only.
- **Valid rows:** 1/1 synthetic Docker smoke row; the two prediction files were
  byte-for-byte identical.
- **Catastrophic false approvals:** change of 0 by construction because the
  prediction file is unchanged. Absolute public CFA was not measurable without
  the public labels/evaluator.
- **Approval additions/removals:** 0/0 in the container smoke comparison;
  approval recovery remains disabled.
- **Runtime per PDF:** 0.96 seconds without trace and 2.44 seconds with trace on
  the one-page synthetic packet on Docker Desktop/Apple Silicon. Trace emitted
  10 proposals, 11 candidates, and nine ledger fields before the final proposal
  cap test was added; the cap permits at most two proposals per field per page
  and two OCR reads per proposal.
- **Verification:** 15 unit tests, Python compilation, `git diff --check`, clean
  Docker build, offline container execution, trace JSON inspection, and
  byte-for-byte prediction comparison.
- **Decision:** keep as development-only measurement infrastructure. Do not
  connect ledger selections to production fields until grouped public
  extraction accuracy, runtime, adjudication changes, and CFA are measured.
