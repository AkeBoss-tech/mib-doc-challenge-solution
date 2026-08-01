#!/usr/bin/env python3
"""Measure high-resolution and upper-document OCR on the pinned failure cohort."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageEnhance, ImageOps

import solution


FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)


def normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def alternatives(field: str, value: str) -> tuple[str, ...]:
    values = [value]
    if field == "risk_flags" and value != "none":
        values.extend(value.split("|"))
    if field in {"species_code", "risk_flags"}:
        values.extend(item.replace("_", " ") for item in tuple(values))
    return tuple(dict.fromkeys(normalized(item) for item in values if item))


def matches(text: str, field: str, value: str) -> int:
    haystack = normalized(text)
    values = alternatives(field, value)
    if field == "risk_flags" and value != "none":
        return sum(item in haystack for item in values[: len(value.split("|"))])
    return int(any(item and item in haystack for item in values))


def scan_one(item: tuple[str, dict[str, str], dict[str, object], int]) -> dict[str, object]:
    pdf_path_text, truth, baseline, bucket = item
    document = pdfium.PdfDocument(pdf_path_text)
    readings: list[str] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                image = page.render(scale=4.0).to_pil().convert("RGB")
            finally:
                page.close()
            gray = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.8)
            readings.append(solution.ocr(gray, 11))
            upper = gray.crop((0, 0, gray.width, int(gray.height * 0.48)))
            readings.append(solution.ocr(upper, 6))
    finally:
        document.close()
    text = "\n".join(readings)
    found = {field: matches(text, field, truth[field]) for field in FIELDS}
    parsed: dict[str, list[solution.Evidence]] = defaultdict(list)
    findings: list[str] = []
    for reading in readings:
        finding = solution.parse_page(solution.page_kind(reading), reading, parsed)
        if finding:
            findings.append(finding)
    selected = {field: solution.choose(field, parsed[field]) for field in FIELDS}
    return {
        "case_id": truth["case_id"], "actual": truth["adjudication"], "bucket": bucket,
        "found": found,
        "selected": selected,
        "findings": findings,
        "missing_before": {
            field: str(baseline[field]) in {"unknown", "SPN-0000", "1900-01-01"}
            for field in FIELDS
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cohort = list(map(json.loads, args.cohort.open()))
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    baseline = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    items = [
        (str(args.pdf_dir / f"{row['case_id']}.pdf"), truth[row["case_id"]], baseline[row["case_id"]], row["bucket"])
        for row in cohort
    ]
    with ProcessPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    for bucket, name in ((0, "development"), (1, "holdout")):
        selected = [row for row in rows if row["bucket"] == bucket]
        print(name, "cases", len(selected))
        for field in FIELDS:
            recovered = sum(row["missing_before"][field] and bool(row["found"][field]) for row in selected)
            promoted_correct = sum(
                row["missing_before"][field]
                and row["selected"][field] == truth[row["case_id"]][field]
                for row in selected
            )
            promoted_wrong = sum(
                row["missing_before"][field]
                and row["selected"][field] not in {
                    "unknown", "SPN-0000", "1900-01-01", truth[row["case_id"]][field]
                }
                for row in selected
            )
            if recovered or promoted_correct or promoted_wrong:
                print(field, "visible", recovered, "correct", promoted_correct, "wrong", promoted_wrong)


if __name__ == "__main__":
    main()
