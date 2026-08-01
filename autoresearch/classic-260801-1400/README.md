# Goal-120 architecture cycle

Metric: official deterministic score, with zero catastrophic false approvals,
complete schema output, and average runtime below six seconds/PDF as mandatory
gates.

This cycle used only rendered public pixels, public labels for development
measurement, the public field manual, and official Tesseract documentation and
artifacts. Public participant implementations, predictions, layouts,
thresholds, tests, and trained artifacts were excluded.

Development JSONL caches are ignored by git. The checked-in scripts reproduce
the bounded failure studies and contain no runtime case rules.

The validated incumbent from this cycle scores `111.791176`, has zero CFA, and
runs at `1.21498 s/PDF`. See the experiment record in
`experiments/2026-08-01-authority-routing-category-recovery.md`.
