#!/usr/bin/env python3
"""Measure bounded high-contrast fee-band retries on unresolved outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import ImageEnhance, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import solution


def split(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def scan_one(item: tuple[str, str]) -> dict[str, object]:
    case_id, pdf_path = item
    candidates: dict[str, set[str]] = defaultdict(set)
    for image in solution.render_pages(Path(pdf_path)):
        crop = image.crop((0, 0, int(image.width * 0.65), int(image.height * 0.28)))
        gray = ImageOps.grayscale(crop)
        for label, variant in (
            ("contrast_2.2", ImageEnhance.Contrast(gray).enhance(2.2)),
        ):
            value = solution.clean_anchored_fee_status(solution.ocr(variant, 11))
            if value:
                candidates[label].add(value)
    return {"case_id": case_id, "candidates": {key: sorted(value) for key, value in candidates.items()}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    ids = sorted(case_id for case_id, row in predictions.items() if row["fee_status"] == "unknown")
    items = [(case_id, str(args.pdf_dir / f"{case_id}.pdf")) for case_id in ids]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    for variant in ("contrast_2.2",):
        for part in (0, 1):
            selected = [
                (row["case_id"], row["candidates"].get(variant, []))
                for row in rows if split(row["case_id"]) == part
            ]
            changed = [(case_id, values[0]) for case_id, values in selected if len(values) == 1]
            correct = sum(value == truth[case_id]["fee_status"] for case_id, value in changed)
            print(json.dumps({"variant": variant, "split": part, "changes": len(changed),
                              "correct": correct, "wrong": len(changed) - correct}, separators=(",", ":")))


if __name__ == "__main__":
    main()
