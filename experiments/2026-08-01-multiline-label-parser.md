# Strict multiline label/value parser

- **Date and intended commit:** 2026-08-01, multiline label parser stage.
- **Hypothesis:** parsing an exact standalone label before attempting an inline
  match prevents optional label suffixes from becoming field values, while
  rejecting schema labels as names prevents nearby form headings from being
  selected as applicants.
- **Generic mechanism changed:** `extract_label` now checks exact label rows and
  their following visible row before its same-line regex. Applicant extraction
  tries an after-label value before a before-label fallback, and `clean_name`
  rejects public schema labels plus three generic form headings. No field value,
  case ID, layout coordinate, policy rule, or approval path was added.
- **Data split / grouping protocol:** a deterministic 100-packet development
  sample selected 25 packets from each visible page-count group (three, four,
  five, and six pages). The accepted candidate was then evaluated on all 1,000
  public training packets with the official scorer. A first sample run using
  symlinks was invalid because targets were outside the Docker mount and was
  excluded before code judgment; the same sample was rerun with copied PDFs.
- **Docker command:** official-equivalent offline Docker limits for the grouped
  sample, then the official `run_docker_submission.py`, validator, and
  `evaluate.py` over all training PDFs.
- **Grouped development result:** 110.300760 versus 105.756316 on 100 packets,
  a +4.544444 extraction-only gain. Applicant matches improved 56→67 and
  species 21→80; all other fields and decisions were unchanged. Runtime was
  71.09 seconds total, or 0.71 seconds/PDF.
- **Full total score:** 101.330381 / 150 versus 96.873714, +4.456667.
- **Full component scores:** extraction 36.718889 / 50 (+4.456667);
  classification 51.41 / 80 (unchanged); calibration 13.201492 / 20
  (unchanged); missing penalty 0.
- **Field changes:** applicant matches 526→673 (+147); species 213→759 (+546);
  every other field's match count was unchanged.
- **Valid rows:** 1,000/1,000; no missing, extra, duplicate, or invalid rows.
- **Catastrophic false approvals:** 0 before and 0 after.
- **Approval additions/removals:** 0/0; all 1,000 adjudications were unchanged.
- **Runtime:** approximately 662 seconds total, or 0.66 seconds/PDF, in the
  official full Docker workflow; image size remained 0.09 GiB.
- **Verification:** 20 unit tests, compilation, `git diff --check`, grouped
  Docker evaluation, full official Docker evaluation, format validation,
  component comparison, field comparison, and decision/CFA diff.
- **Decision:** keep. This improves generic extraction without spending the
  approval safety budget. Next prioritize fee and adverse risk evidence.
