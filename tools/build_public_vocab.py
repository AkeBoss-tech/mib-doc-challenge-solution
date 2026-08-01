#!/usr/bin/env python3
"""Build a small category vocabulary from the public training manifest.

This intentionally excludes case identifiers, applicant names, sponsors, and
dates. It is a reproducible spelling-normalization artifact, not an answer
lookup table.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


FIELDS = ("species_code", "home_world", "declared_purpose")


def main(source: Path, destination: Path) -> None:
    with source.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    payload = {
        "schema": "akeboss-public-category-vocabulary/v1",
        "fields": {
            field: sorted({row[field] for row in rows if row.get(field)})
            for field in FIELDS
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_public_vocab.py <train_labels.csv> <output.json>")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
