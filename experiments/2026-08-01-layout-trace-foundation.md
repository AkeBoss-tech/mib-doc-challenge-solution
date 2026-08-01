# Layout trace foundation

- **Date and intended commit:** 2026-08-01, stage-1 layout trace commit.
- **Hypothesis:** visible-pixel quality diagnostics and field-manual label
  anchors can be measured in shadow mode before a crop reader is allowed to
  affect extraction or adjudication.
- **Generic mechanism:** optional Tesseract TSV geometry creates broad
  right/below region proposals from public schema labels. The normal
  prediction path is unchanged unless `MIB_TRACE_DIR` is explicitly set.
- **Split/grouping protocol:** no training-label-based policy or threshold was
  fitted in this stage; unit tests use synthetic OCR geometry only.
- **Verification commands:**

  ```sh
  /private/tmp/adhyaay-venv/bin/python -m unittest discover -s tests -v
  /private/tmp/adhyaay-venv/bin/python -m py_compile solution.py
  python solution.py <10-public-PDF-directory> /tmp/predictions.jsonl
  python /path/to/challenge/scripts/validate_submission.py \
    --submission /tmp/predictions.jsonl --pdf-dir <10-public-PDF-directory>
  ```

- **Result:** 12 unit tests passed; a public-packet shadow trace wrote four
  page records and 16 proposals while its prediction remained byte-for-byte
  unchanged; the 10-PDF output validated with no missing IDs.
- **Score/CFA impact:** intentionally none. Shadow mode is not enabled by
  default, so it cannot change the known baseline score or add approvals.
- **Runtime:** trace mode costs an extra TSV OCR pass per page and is
  development-only; default runtime is unchanged.
- **Docker status:** Docker is unavailable in this workspace (`docker:
  command not found`), so the image contract was not run here. The Dockerfile
  now includes the existing public vocabulary artifact required by the runtime.
- **Decision:** keep. Next, evaluate a bounded ROI reader in shadow mode on a
  grouped hold-out before it is permitted to contribute field values.
