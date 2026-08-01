# AkeBoss-tech original MIB solution

An original, offline, CPU-only document adjudication pipeline for the 8090 MIB
Doc Challenge. This repository deliberately contains no code, model artifact,
prediction artifact, or commit history from another participant.

The runtime reads only visible PDF pixels, performs local Tesseract OCR, keeps
field evidence with page provenance, and fails closed to `NEEDS_REVIEW` unless
the public field manual supports a decision.

## Development traces

Set `MIB_TRACE_DIR` during local development to write one JSON trace per PDF.
Traces contain visible-pixel page diagnostics and generic label-anchored region
proposals, bounded ROI OCR candidates, and a conflict-preserving evidence
ledger. Native crop reads retry once at 2x scale only when field-type
normalization fails. These records are not prediction inputs and are not part
of a submission; approval recovery remains disabled.

## Run

```sh
docker build -t akeboss-mib .
docker run --rm --network none \
  --mount type=bind,src=/path/to/pdfs,dst=/input,readonly \
  --mount type=bind,src=/tmp,dst=/output \
  akeboss-mib /input /output/predictions.jsonl
```

## Provenance

The public challenge manual, schemas, and supplied data are used as the task
specification. Other public solutions may inform high-level research questions,
but their source code, output artifacts, model files, and case-specific rules
are excluded from this repository.
