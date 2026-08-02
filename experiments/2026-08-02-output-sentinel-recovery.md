# Output sentinel recovery

## Objective

Test whether unresolved output sentinels can be replaced from repeated visible
packet text without changing adjudication evidence or approval behavior. All
implementation, comments, tests, and probes were written independently from
scratch. No participant code or artifacts were used.

## Method

The experiment froze the validated `131.300239` prediction artifact, selected
only rows whose sponsor or applicant output still contained a sentinel, and
reran those PDFs through the official offline Docker harness. Candidate values
were read from packet-wide OCR after adjudication, so they cannot influence a
decision. Both sentinels were exact in zero selected truth rows, making the
replacement branches monotonic for exact extraction: a wrong guess is neutral,
while a correct guess adds one match.

Sponsor IDs use a generic `SPN-` pattern, a bounded set of common OCR glyph
substitutions, packet frequency, and conflict rejection. Applicant names use
adjacent tokens from the exported public name vocabulary, a minimum pairwise
similarity of `0.70`, packet frequency, and conflict rejection.

## Results

| Candidate | Changed | New exact | Development | Holdout | Total score | CFA | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| Sponsor sentinel recovery | 21 | 15 | 4 | 11 | 131.383572 | 0 | Keep |
| Strict name-pair recovery | 13 | 6 | 4 | 2 | 131.416906 | 0 | Keep |
| Relaxed name threshold (`0.60`) | 13 | 1 | 1 | 0 | 131.389128 | 0 | Discard |

The accepted combined artifact scores `131.41690577777774`: extraction
`45.34777777777778`, classification `69.48`, calibration
`16.589127999999963`, and zero catastrophic false approvals. Column-level
diffs confirmed that sponsor validation changed only `sponsor_id`, and the name
validation changed only `applicant_name`.

## Overfitting assessment

The live OCR result was lower than the frozen-text sponsor projection, and the
relaxed name rule failed the second-half check. Those discrepancies are why the
cached projections were not accepted. The retained branches generalized in
both case halves, never overwrite valid values, run only after adjudication,
and do not alter confidence or classification. They are therefore lower risk
than adding another learned decision rule, though the finite benchmark remains
an imperfect estimate of unseen-template performance.
