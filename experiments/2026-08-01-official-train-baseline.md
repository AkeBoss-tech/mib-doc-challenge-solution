# Official 1,000-packet training baseline

- **Date and commit:** 2026-08-01 at `efc3547`.
- **Hypothesis:** the exact official offline runner and evaluator can establish
  a trustworthy score, safety, and runtime incumbent before any shadow ROI
  selection is promoted into production extraction.
- **Generic mechanism changed:** none; this is the baseline measurement for the
  two preceding development-only commits.
- **Data split / grouping protocol:** all 1,000 public training packets were
  scored. For subsequent grouped comparisons, visible packet length supplies
  four coarse groups: 378 three-page, 220 four-page, 267 five-page, and 135
  six-page packets (4,159 rendered pages total). No case ID enters the runtime.
- **Docker command:** the official `scripts/run_docker_submission.py` with four
  CPUs, 8 GiB RAM, read-only root, network disabled, and a 6,000-second timeout,
  followed by the official `scripts/evaluate.py` and validator.
- **Total score:** 96.873714 / 150.
- **Component scores:** extraction 32.262222 / 50; classification 51.41 / 80;
  calibration 13.201492 / 20; missing penalty 0.
- **Field matches:** applicant name 526/1,000; species 213/1,000; home world
  789/1,000; visa 821/1,000; sponsor 705/1,000; arrival 775/1,000; purpose
  791/1,000; risk flags 730/1,000; fee 560/1,000.
- **Valid rows:** 1,000/1,000; no missing, extra, duplicate, invalid decision,
  invalid confidence, or invalid fee records.
- **Catastrophic false approvals:** 0.
- **Approval additions/removals:** not applicable for the first official
  incumbent. The baseline emitted no approvals: 254 denials and 746 reviews.
- **Classification:** 525 exact, 469 conservative reviews, three wrong
  decisions, and three missed reviews. Mean correctness Brier: 0.169963.
- **Runtime:** approximately 1,050 seconds wall-clock total, or 1.05 seconds per
  PDF, under the official 6-second average budget. Image size: 0.09 GiB.
- **Decision:** keep `efc3547` as the measured incumbent. Prioritize generic
  species/applicant/fee extraction and denial/review evidence; keep approval
  recovery disabled.
