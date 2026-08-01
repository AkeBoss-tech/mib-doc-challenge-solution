# Explicit approval-authority shadow study

The development and holdout cohorts each contain 150 packets currently emitted
as `NEEDS_REVIEW`, balanced equally across actual approved, denied, and review
outcomes. The candidate reads only rendered pixels and requires two existing
whole-page OCR variants to agree on an exact labeled manual finding. Conflicts
fail closed. Approval output remains disabled during this study.

A later 80%-width, 30%-height top-left note band found one additional correct
development denial and two additional correct shadow approvals across both
cohorts. After the ordinary core approval gate, however, it produced no holdout
decision improvement while adding OCR runtime, so the production reader was
discarded and only its measurement script is retained.
