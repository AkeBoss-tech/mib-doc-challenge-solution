#!/usr/bin/env python3
"""Cache generic visible decision features for current review failures."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import ImageEnhance, ImageOps

import solution


def band_finding(image) -> tuple[str, bool]:
    crop = image.crop((0, 0, int(image.width * 0.80), int(image.height * 0.30)))
    crop = ImageEnhance.Contrast(ImageOps.grayscale(crop)).enhance(1.7)
    text = solution.ocr(crop, 11)
    marker = solution.page_kind(text) == "note"
    return solution.exact_manual_finding(text), marker


def scan_one(item: tuple[str, str, str]) -> dict[str, object]:
    pdf_path_text, actual, predicted = item
    evidence: dict[str, list[solution.Evidence]] = defaultdict(list)
    manual_findings: list[str] = []
    saw_note = False
    page_kinds: list[list[str]] = []
    for image in solution.render_pages(Path(pdf_path_text)):
        kinds: list[str] = []
        for text in solution.visible_texts(image):
            kind = solution.page_kind(text)
            kinds.append(kind)
            saw_note |= kind == "note"
            solution.parse_page(kind, text, evidence)
            finding = solution.exact_manual_finding(text)
            if finding:
                manual_findings.append(finding)
        finding, note_marker = band_finding(image)
        saw_note |= note_marker
        if finding:
            manual_findings.append(finding)
        page_kinds.append(kinds)
    row = {field: solution.choose(field, evidence[field]) for field in solution.FIELDS}
    return {
        "case_id": Path(pdf_path_text).stem,
        "actual": actual,
        "predicted": predicted,
        "row": row,
        "clean_biometric_reads": sum(
            item.source == "biometric" and item.value == "none"
            for item in evidence["risk_flags"]
        ),
        "paid_fee_reads": sum(
            item.source == "fee" and item.value == "paid"
            for item in evidence["fee_status"]
        ),
        "manual_findings": sorted(set(manual_findings)),
        "saw_note": saw_note,
        "unresolved_note": saw_note and not manual_findings,
        "visa_sources": sorted({item.source for item in evidence["visa_class"]}),
        "date_sources": sorted({item.source for item in evidence["arrival_date"]}),
        "page_kinds": page_kinds,
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
        (str(args.pdf_dir / f"{case_id}.pdf"), truth[case_id]["adjudication"], predictions[case_id]["adjudication"])
        for case_id in ids
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    print("cases", len(rows))


if __name__ == "__main__":
    main()
