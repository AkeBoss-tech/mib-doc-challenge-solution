# Bounded fuzzy risk-flag recovery

- **Date and intended commit:** 2026-08-01, adverse-risk recovery stage.
- **Hypothesis:** a short OCR-corrupted value beside a public risk-field label
  can be recovered safely when one public flag is both a strong and uniquely
  better string match, while arbitrary prose and watermark text remain outside
  the recovery path.
- **Generic mechanism changed:** exact risk matches remain authoritative. When
  exact parsing fails, at most three field-like tokens in an 80-character value
  are compared with the public risk vocabulary. Recovery requires similarity
  at least 0.75 and a 0.20 margin over the runner-up. Duplicate readings are
  normalized without adding case identifiers, coordinates, or layout rules.
- **Data split / grouping protocol:** development and disjoint holdout sets each
  contain 100 packets, balanced with 25 packets from each visible page-count
  group (three through six pages). The unchanged candidate was then evaluated
  on all 1,000 public training packets with the official offline Docker runner,
  validator, and evaluator.
- **Failure inspection:** rendered false-denial and recovered-denial packets
  showed that `Registry Status: EMBARGO REVIEW` appears identically in true
  denials and legitimate review cases. A trial that treated that phrase as
  denial authority scored higher but introduced two false denials, so it was
  rejected. The phrase remains separate review evidence and cannot deny.
- **Grouped result:** the combined 200-packet sample gained 0.512409 points.
  One apparent holdout decision difference reproduced with the untouched
  committed baseline under the same host OCR environment and was therefore not
  attributable to this change. The official Docker full-set comparison is the
  authoritative deterministic gate.
- **Full total score:** 102.223520 / 150 versus 101.728369, +0.495150.
- **Full component scores:** extraction 37.335556 / 50 (+0.222222);
  classification 51.65 / 80 (+0.24); calibration 13.237964 / 20
  (+0.032928); missing penalty 0.
- **Valid rows:** 1,000/1,000; no missing, extra, duplicate, or invalid rows.
- **Risk changes:** 29; 25 recoveries, 0 regressions, and four partial
  multi-flag readings that did not replace a previously correct value.
- **Decision changes:** four `NEEDS_REVIEW` to `DENIED`, all four correct; zero
  incorrect changes and zero approval additions or removals.
- **Catastrophic false approvals:** 0 before and 0 after.
- **Runtime:** approximately 1,091 seconds including the negligible cached
  image build, or 1.09 seconds/PDF; image size 0.09 GiB. This remains well below
  the official six-second average budget.
- **Verification:** 23 unit tests, compilation, `git diff --check`, grouped
  development/holdout evaluation, direct rendered-pixel failure inspection,
  full official Docker runner, validator, evaluator, complete changed-risk
  truth audit, and complete decision-change truth audit.
- **Decision:** keep only the bounded fuzzy recovery. Reject registry-status
  denial authority. Approval recovery remains disabled.
