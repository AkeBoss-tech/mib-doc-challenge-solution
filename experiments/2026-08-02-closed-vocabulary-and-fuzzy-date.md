# Closed-vocabulary and anchored date recovery

- **Boundary:** Original implementation using public labels and OCR from
  rendered visible pixels only. No participant code, comments, layouts,
  thresholds, tests, predictions, or artifacts were used.
- **Incumbent:** commit `0db8cc1`, validated score `130.288017`, zero CFA.
- **Safety:** Every accepted change runs after adjudication. These branches
  cannot modify adjudication, confidence, denial routing, or approval routing.

## Closed-vocabulary recovery

Species, home world, visa class, and declared purpose have closed public
vocabularies. An output outside its vocabulary is structurally invalid and
cannot be an exact match. Five-fold visible-text classifiers were therefore
allowed to fill only invalid outputs, using probability `0.08` and no margin
requirement. Applicant names use the public compositional token vocabulary;
the new reader may discard one or two extra OCR artifact tokens when an ordered
pair has token similarity at least `0.70`.

Out-of-fold exact gains were positive in both SHA-256 groups: home world +23,
species +15, purpose +12, visa +4, and compositional names +17. The live Docker
differential reran all 202 affected PDFs in `277.91` seconds and produced:

- applicant name: 34 changes, 17 gains, 0 losses;
- species: 59 changes, 47 gains, 0 losses;
- home world: 90 changes, 34 gains, 0 losses;
- visa: 42 changes, 27 gains, 0 losses;
- purpose: 83 changes, 49 gains, 0 losses.

There were no unexpected field, adjudication, or confidence changes. The
complete merged submission scored `131.198017`, with zero CFA.

## Anchored date repair

Failure inspection showed a generic glyph substitution in damaged arrival-date
rows: the printed six is repeatedly read as eight, yielding year `2028` and,
when applicable, month `08`. The recovery requires a short arrival-date-like
label on the same visible OCR line, converts the year to `2026`, converts month
`08` to `06`, validates the resulting ISO date, and accepts only one
conflict-free candidate. It fills only the `1900-01-01` sentinel after policy
is complete.

The frozen-text probe recovered 22 exact dates across both groups. The rebuilt
offline Docker image reran all 138 unresolved-date PDFs in `190.67` seconds.
Live results were 26 changes, 23 exact gains, and zero losses: development
11 gains and holdout 12 gains.

## Final result and rejected branches

- Deterministic score: `131.300239 / 150`.
- Extraction: `45.231111 / 50`.
- Classification: `69.48 / 80`.
- Calibration: `16.589128 / 20`.
- Catastrophic false approvals: `0`.
- Complete valid rows: `1,000 / 1,000`.
- Image size: `0.10 GiB`.

Rejected branches included post-extraction approval recovery (positive only in
development and negative in holdout), learned overwrites of already-valid
categories (no stable grouped gain), additive learned risk flags (no
development gain), and broad high-resolution retries (no useful date recovery
in the pinned cohort). Frozen-cache projections were not accepted as live
scores; all kept changes were confirmed with rebuilt offline Docker images.
