# Independent high-performance reimplementation blueprint

## Purpose and boundary

This is an implementation plan for rebuilding the *capabilities* associated
with a high-performing document-adjudication system while maintaining this
repository's independent provenance boundary. It is a requirements document,
not a transcription of any other implementation.

The implementation team may use the public challenge specification, public
training documents and labels, ordinary OCR/computer-vision documentation, and
measurements generated in this repository. It must not inspect, port, imitate,
or depend on another participant's source code, prediction files, trained
assets, case IDs, case-specific rules, function names, internal architecture,
or hand-tuned constants. When a design choice cannot be motivated by the task
specification or an experiment recorded here, leave it out.

The target is a general offline CPU pipeline that substantially improves field
extraction and recovers only well-supported approvals, while retaining a hard
zero-catastrophic-false-approval release gate.

## Operating contract

- Read rendered, visible page pixels only. Do not use PDF text extraction,
  document metadata, filenames, QR/barcode payloads, or embedded instructions
  as evidence.
- Run offline in the supplied Docker contract, within the average per-PDF CPU
  budget.
- Produce one schema-valid JSONL record per readable case and preserve a
  debuggable internal trace during development.
- Treat the public field manual as the policy authority.
- Use no case-keyed behavior. A rule must operate on a document feature,
  layout family, or normalized field value that can occur on an unseen case.

## Design thesis

Accuracy comes from selectively making several independent attempts to read
the *same visible field*, then resolving the evidence by its source quality.
It does not come from accepting the most convenient OCR string or blindly
majority-voting all OCR passes.

The decision policy is intentionally asymmetric:

- One credible adverse signal may block approval and produce `DENIED` or
  `NEEDS_REVIEW` according to the field manual.
- `APPROVED` requires affirmative, visible, policy-authorized evidence with a
  higher standard of quality and consistency.
- Missing, contradictory, or low-quality decision evidence resolves to
  `NEEDS_REVIEW`; it never resolves optimistically.

## System architecture

```text
PDF pages
  -> deterministic render set
  -> page/layout characterization
  -> inexpensive whole-page OCR
  -> field and decision-region proposals
  -> targeted ROI OCR retries
  -> normalized evidence ledger
  -> cross-page evidence resolver
  -> field-manual decision engine
  -> confidence calibrator
  -> schema validation + safety release gate
```

Each arrow has a testable input/output contract. Lower-quality observations
must never overwrite a higher-quality observation without recording the
conflict.

## Module requirements

### 1. Rendering and page characterization

Implement a deterministic renderer that creates a standard-resolution image
for every page. It should also compute inexpensive visible-pixel diagnostics:
page dimensions, rotation/skew estimate, grayscale contrast, foreground
density, blur/sharpness proxy, and whether the page resembles a structured
form, letter, portrait, receipt, stamp, or unknown page.

Requirements:

- Preserve page number and render settings with every downstream observation.
- Do not globally preprocess every page. Use diagnostics to decide whether a
  targeted alternative render is worthwhile.
- Permit bounded rotation correction and rescaling when diagnostics indicate a
  likely benefit.
- Keep an explicit runtime budget so damaged pages cannot consume unbounded
  retry work.

Acceptance checks:

- Rendering is repeatable byte-for-byte or functionally identical on a clean
  checkout.
- A known rotated sample records its orientation correction in its trace.
- Pages classified as clean do not receive unnecessary expensive variants.

### 2. Whole-page OCR and layout anchors

Run one inexpensive OCR pass on each relevant page to obtain text, word boxes,
line grouping where available, and engine confidence. This pass is for page
discovery and anchors, not final field truth.

Build independent, generic anchor detectors for visible labels and headings
from the field manual (for example, labels for identity, sponsorship, fee,
arrival, risk, and disposition). Anchor matching must tolerate ordinary OCR
mistakes through normalization and bounded fuzzy matching justified by held-out
experiments.

Output contract: a page may emit zero or more `RegionProposal` records:

```text
field_or_section, page, bounding_region, anchor_text, anchor_quality,
layout_family, proposed_reader
```

No proposal is itself a field value or decision.

### 3. Targeted ROI readers

Implement field readers around region proposals. A reader receives only a
cropped visible image plus generic context such as expected field type. It
returns candidate readings; it does not decide the case.

Use reader families appropriate to document structure:

- label/value readers for forms;
- single-line readers for identifiers, dates, and status cells;
- block readers for letters or findings paragraphs;
- table/panel readers for repeated risk or disposition rows;
- full-page fallback readers when reliable anchors are absent.

For an uncertain or missing field, allow a bounded retry ladder such as:

1. native crop with an OCR page-segmentation mode suited to the region;
2. enlarged crop;
3. contrast or adaptive threshold variant;
4. small deskew variant if page diagnostics support it;
5. a second segmentation mode appropriate to the same crop.

The exact transformations, retries, and thresholds must be selected from
grouped held-out experiments in this repository. Avoid a universal image
filter: it can degrade clean text and creates correlated mistakes.

Output contract for every candidate:

```text
field, raw_text, normalized_value, page, crop, reader_family,
transform_chain, OCR_quality, anchor_quality, visible_evidence_excerpt
```

`visible_evidence_excerpt` is development-only trace data and must be derived
from the pixels, never a PDF text layer.

### 4. Normalization and field evidence ledger

Normalize candidate values by field type before comparison: whitespace and
punctuation cleanup, date canonicalization, identifier formatting, controlled
vocabulary matching, and risk-flag tokenization. Each transformation must be
loss-aware: retain the raw reading alongside the normalized form.

Maintain one evidence ledger per case. It stores all candidates, conflicts,
source quality, and the selected value/reason for every output field.

Suggested evidence priority, to be validated rather than assumed:

1. clearly anchored, correctly segmented ROI read;
2. direct full-page reading in a recognized layout;
3. corroborated reading from more than one independent crop/read path;
4. low-confidence or unanchored fallback reading.

Cross-page resolution may use repeated visible document facts as
corroboration, but never lets a lower-authority administrative page override a
specific policy-bearing decision or risk statement.

Acceptance checks:

- Conflicting candidates remain visible in the trace.
- The resolver produces a reason code for every chosen value and every
  unresolved field.
- A lower-priority candidate cannot silently replace a higher-priority one.

### 5. Decision evidence and policy engine

Separate extraction from adjudication. The policy engine consumes normalized,
provenanced fields and explicit decision/risk evidence; it does not consume
raw OCR text directly.

Represent decisions as facts with provenance, for example:

```text
fact_type, value, page, evidence_quality, policy_authority, conflicts
```

Implement the public field manual through explicit generic rules:

- a strong policy-defined denial fact can yield `DENIED`;
- an explicit affirmative authorization may yield `APPROVED` only when all
  required supporting fields are consistent and no unresolved adverse fact is
  present;
- otherwise emit `NEEDS_REVIEW`.

The approval authority must be deliberately stricter than denial/review
recovery. Require all of the following before approving:

- an affirmative decision reading or equivalent policy-authorized evidence;
- sufficient visible quality/provenance;
- no conflict with risk, payment, identity, or policy facts;
- no materially unresolved required field;
- document-level consistency checks pass.

Never encode a layout position, phrase, or exception merely because it happens
to improve public-label performance. A proposed rule must be described in
general document terms, tested on grouped folds, and reviewed for whether it
would make sense on a new packet.

### 6. Confidence calibration

Confidence is a prediction of adjudication correctness, not an OCR engine
confidence and not a subjective certainty score.

Build a compact feature vector from independently computed features such as:

- selected evidence quality and agreement;
- number and severity of unresolved conflicts;
- completeness of policy-required fields;
- page and field readability diagnostics;
- decision path (`APPROVED`, `DENIED`, or `NEEDS_REVIEW`);
- whether the conclusion depends on fallback rather than anchored evidence.

Fit and evaluate calibration with grouped out-of-fold predictions so near-
duplicate pages/layouts do not leak between train and evaluation partitions.
Use a simple monotonic or regularized calibrator unless an independently
validated alternative earns its complexity. Store the training command,
features, fold definition, metrics, and serialized model provenance.

Confidence must decrease under conflicts, weak evidence, and fallback-only
decisions. It must not increase merely because an OCR engine reports a large
number for a short text snippet.

### 7. Release safety gate

Every candidate build must pass, in order:

1. unit tests for normalization, evidence precedence, and policy rules;
2. Docker runtime and schema validation;
3. full public training evaluation;
4. grouped out-of-fold report for all new approval behavior;
5. explicit catastrophic-false-approval count of zero;
6. error review of every predicted `APPROVED` and every changed adjudication;
7. runtime-budget report.

If a change adds an approval, require a short evidence trace showing the
affirmative authority, conflict scan, and why it generalizes. A score increase
does not override the false-approval gate.

## Implementation sequence

Implement in narrow, reversible stages. Commit only after each stage passes
its stated tests.

1. **Trace foundation.** Define stable data classes/JSON for page diagnostics,
   region proposals, candidate values, ledger entries, resolution reasons, and
   decision facts. Add trace fixtures made from challenge-approved public
   documents only.
2. **Measurement harness.** Add a grouped train/hold-out splitter, per-field
   extraction metrics, decision confusion reporting, confidence/Brier report,
   retry-cost report, and change-vs-baseline report.
3. **Document characterization.** Build rendering, quality metrics, layout
   family detection, and anchor discovery. Demonstrate that it finds relevant
   regions without producing any new approvals.
4. **ROI extraction.** Add readers one document-field family at a time. Keep
   whole-page OCR as fallback. Promote a reader only when its held-out field
   accuracy improves and its runtime is bounded.
5. **Ledger resolver.** Add normalization, corroboration, conflict handling,
   and explicit precedence. Improve extraction before altering adjudication.
6. **Safety-first decisions.** Add generic denial/review recovery and document
   consistency checks. Verify no approval behavior regresses.
7. **Approval recovery.** Introduce a narrowly defined affirmative-authority
   path. Start in shadow mode: report which cases it *would* approve without
   changing output. Review every candidate, then enable only paths that pass
   grouped evaluation and the zero-CFA gate.
8. **Calibration.** Train from out-of-fold artifacts only; re-evaluate after
   every policy change.
9. **Hardening.** Test corrupted/rotated/low-contrast pages, hidden-text and
   fake-instruction resistance, Docker read-only operation, and runtime.
10. **Submission rehearsal.** Run the exact public commands for Docker,
    validation, evaluation, memo, and reproducibility from a fresh checkout.

## Experiment record template

For every experiment, add a small immutable record with:

```text
date and commit
hypothesis
generic mechanism changed
data split / grouping protocol
Docker command
total score and component scores
valid rows
catastrophic false approvals
approval additions/removals relative to baseline
runtime per PDF
decision: keep, revert, or investigate
```

The record must not include case-ID rules, private labels, or artifacts that
would act as an answer key.

## Definition of done

The reimplementation is ready for a submission candidate only when it:

- is independently authored and documented under `PROVENANCE.md`;
- runs offline from a clean Docker build;
- derives all outputs from visible pixels and public policy;
- materially improves extraction, classification, and calibration over the
  current baseline on properly separated evaluation data;
- has zero catastrophic false approvals in its full public release evaluation;
- has reviewed, traceable authority for every approval;
- remains within the challenge runtime and artifact limits; and
- can be explained as a general document system rather than a public-set
  optimization.

