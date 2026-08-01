#!/usr/bin/env python3
"""Measure unique exact public-category reads in existing visible OCR text."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import solution


def stable_bucket(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def scan_one(item: tuple[str, dict[str, str], dict[str, object]]) -> dict[str, object]:
    pdf_path_text, truth, baseline = item
    candidates = {field: set() for field in solution.CATEGORY_VOCABULARY}
    for image in solution.render_pages(Path(pdf_path_text)):
        for text in solution.visible_texts(image):
            folded = f" {solution.normalized_anchor(text)} "
            kind = solution.page_kind(text)
            for field, values in solution.CATEGORY_VOCABULARY.items():
                if field == "declared_purpose" and kind not in {"intake", "sponsor"}:
                    continue
                for value in values:
                    needle = f" {solution.normalized_anchor(value)} "
                    if needle in folded:
                        candidates[field].add(value)
    selected = {
        field: next(iter(values)) if len(values) == 1 else ""
        for field, values in candidates.items()
    }
    return {
        "case_id": truth["case_id"],
        "actual": truth["adjudication"],
        "bucket": stable_bucket(truth["case_id"]),
        "selected": selected,
        "truth": {field: truth[field] for field in selected},
        "baseline": {field: baseline[field] for field in selected},
        "conflicts": {field: sorted(values) for field, values in candidates.items() if len(values) > 1},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--selection", choices=("review", "nonreview", "all"), default="review")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    ids = sorted(
        case_id for case_id, row in predictions.items()
        if args.selection == "all"
        or (args.selection == "review" and row["adjudication"] == "NEEDS_REVIEW")
        or (args.selection == "nonreview" and row["adjudication"] != "NEEDS_REVIEW")
    )
    items = [
        (str(args.pdf_dir / f"{case_id}.pdf"), truth[case_id], predictions[case_id])
        for case_id in ids
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    for bucket, name in ((0, "development"), (1, "holdout")):
        group = [row for row in rows if row["bucket"] == bucket]
        print(name)
        for field in solution.CATEGORY_VOCABULARY:
            changed = [
                row for row in group
                if row["baseline"][field] in {"unknown", ""} and row["selected"][field]
            ]
            print(
                field, "changes", len(changed),
                "correct", sum(row["selected"][field] == row["truth"][field] for row in changed),
                "wrong", sum(row["selected"][field] != row["truth"][field] for row in changed),
            )


if __name__ == "__main__":
    main()
