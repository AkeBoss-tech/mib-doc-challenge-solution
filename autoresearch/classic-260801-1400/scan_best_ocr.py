#!/usr/bin/env python3
"""Measure official tessdata_best as a conditional independent OCR pass."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import ImageEnhance, ImageOps

import solution


def best_ocr(image, tessdata_dir: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as temporary:
        image.save(temporary.name)
        completed = subprocess.run(
            ["tesseract", temporary.name, "stdout", "--tessdata-dir", tessdata_dir, "--oem", "1", "--psm", "11", "-l", "eng"],
            capture_output=True, text=True, timeout=40, check=False,
        )
    return completed.stdout


def scan_one(item: tuple[str, dict[str, str], dict[str, object], int, str]) -> dict[str, object]:
    pdf_path_text, truth, baseline, bucket, tessdata_dir = item
    evidence: dict[str, list[solution.Evidence]] = defaultdict(list)
    findings: list[str] = []
    for image in solution.render_pages(Path(pdf_path_text)):
        boosted = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.5)
        text = best_ocr(boosted, tessdata_dir)
        kind = solution.page_kind(text)
        finding = solution.parse_page(kind, text, evidence)
        if finding:
            findings.append(finding)
    selected = {field: solution.choose(field, evidence[field]) for field in solution.FIELDS}
    return {
        "case_id": truth["case_id"], "actual": truth["adjudication"], "bucket": bucket,
        "selected": selected, "findings": sorted(set(findings)),
        "truth": {field: truth[field] for field in solution.FIELDS},
        "baseline": {field: baseline[field] for field in solution.FIELDS},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--tessdata-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cohort = {row["case_id"]: row for row in map(json.loads, args.cohort.open())}
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    baseline = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    items = [
        (str(args.pdf_dir / f"{case_id}.pdf"), truth[case_id], baseline[case_id], row["bucket"], str(args.tessdata_dir))
        for case_id, row in sorted(cohort.items())
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    defaults = solution.DEFAULTS
    for split, name in ((0, "development"), (1, "holdout")):
        print(name)
        for field in solution.FIELDS:
            changed = [r for r in rows if r["bucket"] == split and r["baseline"][field] == defaults[field] and r["selected"][field] != defaults[field]]
            if changed:
                print(field, "changes", len(changed), "correct", sum(r["selected"][field] == r["truth"][field] for r in changed), "wrong", sum(r["selected"][field] != r["truth"][field] for r in changed))


if __name__ == "__main__":
    main()
