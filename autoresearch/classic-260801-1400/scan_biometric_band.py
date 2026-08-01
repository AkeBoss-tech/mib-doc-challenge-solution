#!/usr/bin/env python3
"""Measure a generic upper-left biometric evidence reader on review outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import ImageEnhance, ImageOps

import solution


def stable_bucket(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def normalize_reading(text: str) -> str:
    folded = solution.normalized_anchor(text)
    if not ("biometric" in folded or re.search(r"\bb\s*13\b", folded)):
        return ""
    labeled = solution.extract_label(text, "Observed Flags") or solution.extract_label(text, "Risk Flags")
    value = solution.clean_flags(labeled)
    if value:
        return value
    exact = [
        flag for flag in sorted(solution.DISQUALIFYING | solution.REVIEW_FLAGS)
        if flag in text.casefold().replace("-", "_").replace(" ", "_")
    ]
    return "|".join(exact)


def read_band(image) -> list[str]:
    crop = image.crop((0, 0, int(image.width * 0.72), int(image.height * 0.32)))
    crop = ImageEnhance.Contrast(ImageOps.grayscale(crop)).enhance(1.7)
    enlarged = crop.resize((crop.width * 2, crop.height * 2))
    return [
        value for value in (
            normalize_reading(solution.ocr(crop, 11)),
            normalize_reading(solution.ocr(enlarged, 6)),
        )
        if value
    ]


def scan_one(item: tuple[str, str, str]) -> dict[str, object]:
    pdf_path_text, actual, truth_flags = item
    candidates = []
    for image in solution.render_pages(Path(pdf_path_text)):
        candidates.extend(read_band(image))
    values = sorted(set(candidates))
    selected = values[0] if len(values) == 1 else ""
    return {
        "case_id": Path(pdf_path_text).stem,
        "actual": actual,
        "truth_flags": truth_flags,
        "bucket": stable_bucket(Path(pdf_path_text).stem),
        "candidates": values,
        "selected": selected,
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
    ids = sorted(case_id for case_id, row in predictions.items() if row["adjudication"] == "NEEDS_REVIEW")
    items = [
        (str(args.pdf_dir / f"{case_id}.pdf"), truth[case_id]["adjudication"], truth[case_id]["risk_flags"])
        for case_id in ids
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    for bucket, name in ((0, "development"), (1, "holdout")):
        group = [row for row in rows if row["bucket"] == bucket and row["selected"]]
        correct = sum(row["selected"] == row["truth_flags"] for row in group)
        wrong = len(group) - correct
        print(name, "selected", len(group), "correct", correct, "wrong", wrong)


if __name__ == "__main__":
    main()
