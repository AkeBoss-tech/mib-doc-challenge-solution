# Independent fee ROI autoresearch

Metric: official total score, with zero new catastrophic false approvals and no
incorrect decision changes as mandatory safety gates.

Development and holdout each contain 60 independently selected public training
packets where the committed pipeline outputs `fee_status=unknown` despite a
scorable visible fee status. Selection is deterministic, disjoint, stratified
across truth statuses and visible page counts, and contains no case-specific
runtime logic. Candidate geometry and acceptance rules are derived only from
visible anchors and measurements recorded in this directory.

Verification sequence: unit tests, development score, unchanged disjoint
holdout score, changed-case truth audit, then the official 1,000-PDF Docker
runner/validator/evaluator. Participant source code, layouts, thresholds, tests,
predictions, and artifacts are excluded.
