# AkeBoss-tech original MIB solution

An original, offline, CPU-only document adjudication pipeline for the 8090 MIB
Doc Challenge. This repository deliberately contains no code, model artifact,
prediction artifact, or commit history from another participant.

The runtime reads only visible PDF pixels, performs local Tesseract OCR, keeps
field evidence with page provenance, and fails closed to `NEEDS_REVIEW` unless
the public field manual supports a decision.

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
