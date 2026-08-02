#!/usr/bin/env python3
"""Classify public field misses using only Docker-rendered visible OCR text."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)
DEFAULTS = {
    "applicant_name": "unknown", "species_code": "unknown", "home_world": "unknown",
    "visa_class": "unknown", "sponsor_id": "SPN-0000", "arrival_date": "1900-01-01",
    "declared_purpose": "unknown", "risk_flags": "none", "fee_status": "unknown",
}


def normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def best_window_similarity(target: str, text: str) -> float:
    target_words = target.split()
    words = text.split()
    if not target_words or not words:
        return 0.0
    widths = range(max(1, len(target_words) - 1), len(target_words) + 2)
    best = 0.0
    for width in widths:
        for start in range(max(0, len(words) - width + 1)):
            observed = " ".join(words[start:start + width])
            best = max(best, SequenceMatcher(None, target, observed).ratio())
    return round(best, 4)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    ledger = []
    counts: dict[str, Counter[str]] = defaultdict(Counter)

    for case_id in sorted(truth):
        visible = normalize(cache[case_id]["text"])
        for field in FIELDS:
            expected = normalize(truth[case_id][field])
            observed = normalize(predictions[case_id][field])
            if expected == observed:
                continue
            exact_visible = bool(expected and expected in visible)
            similarity = 1.0 if exact_visible else best_window_similarity(expected, visible)
            if exact_visible:
                failure_class = "binding_or_resolution"
            elif similarity >= 0.82:
                failure_class = "near_ocr_read"
            else:
                failure_class = "not_recovered_by_whole_page_ocr"
            row = {
                "case_id": case_id,
                "truth_adjudication": truth[case_id]["adjudication"],
                "predicted_adjudication": predictions[case_id]["adjudication"],
                "field": field,
                "expected": truth[case_id][field],
                "observed": predictions[case_id][field],
                "output_is_default": observed == normalize(DEFAULTS[field]),
                "exact_truth_in_visible_ocr": exact_visible,
                "best_visible_similarity": similarity,
                "failure_class": failure_class,
            }
            ledger.append(row)
            counts[field][failure_class] += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as stream:
        for row in ledger:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    summary = {
        "cases": len(truth),
        "field_misses": len(ledger),
        "by_field": {field: dict(counts[field]) for field in FIELDS},
    }
    args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
