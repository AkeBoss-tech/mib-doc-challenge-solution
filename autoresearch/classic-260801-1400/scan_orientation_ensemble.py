#!/usr/bin/env python3
"""Measure bounded rotation OCR on decision failures without changing runtime code."""

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


def normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def contains(text: str, value: str) -> bool:
    return bool(value) and normalized(value) in normalized(text)


def scan_one(item: tuple[str, str, dict[str, str]]) -> dict[str, object]:
    pdf_path_text, predicted, truth = item
    current_texts: list[str] = []
    rotated_texts: list[str] = []
    for image in solution.render_pages(Path(pdf_path_text)):
        current_texts.extend(solution.visible_texts(image))
        gray = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.8)
        for angle in (90, 270):
            rotated = gray.rotate(angle, expand=True, fillcolor=255)
            rotated_texts.append(solution.ocr(rotated, 11))
    current = "\n".join(current_texts)
    ensemble = current + "\n" + "\n".join(rotated_texts)
    fields = (
        "applicant_name", "species_code", "home_world", "visa_class",
        "sponsor_id", "arrival_date", "declared_purpose", "fee_status",
    )
    truth_flags = [] if truth["risk_flags"] == "none" else truth["risk_flags"].split("|")
    return {
        "case_id": truth["case_id"],
        "actual": truth["adjudication"],
        "predicted": predicted,
        "bucket": stable_bucket(truth["case_id"]),
        "current_fields": sum(contains(current, truth[field]) for field in fields),
        "ensemble_fields": sum(contains(ensemble, truth[field]) for field in fields),
        "current_flags": sum(contains(current, flag) for flag in truth_flags),
        "ensemble_flags": sum(contains(ensemble, flag) for flag in truth_flags),
        "current_finding": any(contains(current, value) for value in ("finding denied", "finding approved", "finding needs review")),
        "ensemble_finding": any(contains(ensemble, value) for value in ("finding denied", "finding approved", "finding needs review")),
        "rotation_added_tokens": max(0, len(set(normalized("\n".join(rotated_texts)).split())) - len(set(normalized(current).split()))),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--per-class-per-bucket", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    selected: list[tuple[str, str, dict[str, str]]] = []
    for actual in ("APPROVED", "DENIED"):
        for bucket in (0, 1):
            ids = [
                case_id for case_id in sorted(truth)
                if truth[case_id]["adjudication"] == actual
                and predictions[case_id]["adjudication"] == "NEEDS_REVIEW"
                and stable_bucket(case_id) == bucket
            ][: args.per_class_per_bucket]
            selected.extend((str(args.pdf_dir / f"{case_id}.pdf"), "NEEDS_REVIEW", truth[case_id]) for case_id in ids)

    with ProcessPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(scan_one, selected))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")

    for bucket, label in ((0, "development"), (1, "holdout")):
        cohort = [row for row in rows if row["bucket"] == bucket]
        print(label, "cases", len(cohort))
        for actual in ("APPROVED", "DENIED"):
            group = [row for row in cohort if row["actual"] == actual]
            print(
                actual,
                "field_recoveries", sum(int(row["ensemble_fields"]) - int(row["current_fields"]) for row in group),
                "flag_recoveries", sum(int(row["ensemble_flags"]) - int(row["current_flags"]) for row in group),
                "finding_recoveries", sum(bool(row["ensemble_finding"]) and not bool(row["current_finding"]) for row in group),
            )


if __name__ == "__main__":
    main()
