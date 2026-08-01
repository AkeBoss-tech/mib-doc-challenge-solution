#!/usr/bin/env python3
"""Measure bounded fuzzy public-category recovery in existing OCR strings."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import solution


def stable_bucket(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def best_window(text: str, target: str) -> float:
    words = re.findall(r"[a-z0-9]+", solution.normalized_anchor(text))
    expected = solution.normalized_anchor(target)
    size = len(expected.split())
    best = 0.0
    for width in range(max(1, size - 1), size + 2):
        for start in range(max(0, len(words) - width + 1)):
            candidate = " ".join(words[start:start + width])
            best = max(best, SequenceMatcher(None, candidate, expected).ratio())
    return best


def scan_one(item: tuple[str, dict[str, str], dict[str, object]]) -> dict[str, object]:
    pdf_path_text, truth, baseline = item
    scores = {field: {value: 0.0 for value in values} for field, values in solution.CATEGORY_VOCABULARY.items()}
    for image in solution.render_pages(Path(pdf_path_text)):
        for text in solution.visible_texts(image):
            kind = solution.page_kind(text)
            for field, values in solution.CATEGORY_VOCABULARY.items():
                if field == "declared_purpose" and kind not in {"intake", "sponsor"}:
                    continue
                for value in values:
                    scores[field][value] = max(scores[field][value], best_window(text, value))
    ranked = {field: sorted(((score, value) for value, score in values.items()), reverse=True) for field, values in scores.items()}
    return {
        "case_id": truth["case_id"], "actual": truth["adjudication"],
        "bucket": stable_bucket(truth["case_id"]),
        "best": {field: values[0] for field, values in ranked.items()},
        "margin": {field: values[0][0] - values[1][0] for field, values in ranked.items()},
        "truth": {field: truth[field] for field in ranked},
        "baseline": {field: baseline[field] for field in ranked},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    items = [(str(args.pdf_dir / f"{case_id}.pdf"), truth[case_id], row) for case_id, row in sorted(predictions.items())]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    print("cases", len(rows))


if __name__ == "__main__":
    main()
