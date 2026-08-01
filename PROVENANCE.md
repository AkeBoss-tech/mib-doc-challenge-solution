# Originality boundary

This project is an original implementation authored in this repository. The
challenge package itself is the authoritative specification and supplies the
field manual, schema, evaluator, and PDFs.

Permitted research inputs are high-level observations such as: use visible
pixels rather than hidden PDF text, preserve evidence provenance, make denial
recovery asymmetric, validate on a held-out split, and treat approval errors as
costly. Those observations must be re-expressed as independently designed
requirements and covered by new tests.

Prohibited inputs are another participant's source code, file layouts,
function names, tests, trained model files, prediction files, case IDs,
case-specific rules, source-derived constants, or a rewrite that closely
tracks their implementation. If a mechanism cannot be justified from the
public specification and our own experiment, it does not ship.

Every future experiment records its command, code commit, public-data score,
and catastrophic-false-approval count before it is considered for release.
