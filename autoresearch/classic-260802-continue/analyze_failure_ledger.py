#!/usr/bin/env python3
"""Summarize public-label misses without generating runtime rules."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)
DEFAULTS = {
    "applicant_name": "unknown",
    "species_code": "unknown",
    "home_world": "unknown",
    "visa_class": "unknown",
    "sponsor_id": "SPN-0000",
    "arrival_date": "1900-01-01",
    "declared_purpose": "unknown",
    "risk_flags": "none",
    "fee_status": "unknown",
}


def normalized(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.truth.open(newline="", encoding="utf-8") as stream:
        truth = {row["case_id"]: row for row in csv.DictReader(stream)}
    with args.predictions.open(encoding="utf-8") as stream:
        predictions = {row["case_id"]: row for row in map(json.loads, stream) if row}

    fields: dict[str, dict[str, object]] = {}
    examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    for field in FIELDS:
        exact = sentinel = nondefault_wrong = 0
        for case_id, expected in truth.items():
            actual = predictions.get(case_id, {})
            got = normalized(actual.get(field, ""))
            wanted = normalized(expected[field])
            if got == wanted:
                exact += 1
                continue
            if got == normalized(DEFAULTS[field]):
                sentinel += 1
            else:
                nondefault_wrong += 1
            if len(examples[field]) < 12:
                examples[field].append({
                    "case_id": case_id,
                    "predicted": str(actual.get(field, "")),
                    "expected": expected[field],
                })
        fields[field] = {
            "exact": exact,
            "misses": len(truth) - exact,
            "sentinel_misses": sentinel,
            "nondefault_wrong_misses": nondefault_wrong,
        }

    decision_confusion = Counter(
        f"{expected['adjudication']}->{predictions.get(case_id, {}).get('adjudication', 'MISSING')}"
        for case_id, expected in truth.items()
    )
    payload = {
        "rows": len(truth),
        "prediction_rows": len(predictions),
        "fields": fields,
        "decision_confusion": dict(sorted(decision_confusion.items())),
        "examples": dict(examples),
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
