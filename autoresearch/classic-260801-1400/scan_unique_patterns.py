#!/usr/bin/env python3
"""Measure unique visible schema patterns in existing whole-page OCR."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import solution


def stable_bucket(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def scan_one(item: tuple[str, dict[str, str], dict[str, object]]) -> dict[str, object]:
    pdf_path_text, truth, baseline = item
    sponsors: set[str] = set()
    dates: set[str] = set()
    for image in solution.render_pages(Path(pdf_path_text)):
        for text in solution.visible_texts(image):
            sponsors.update(
                f"SPN-{digits}"
                for digits in re.findall(r"\bSPN[- ]?(\d{4})\b", text, re.I)
            )
            for candidate in re.findall(r"\b20\d{2}[-/]\d{2}[-/]\d{2}\b", text):
                cleaned = solution.clean_date(candidate)
                if cleaned:
                    dates.add(cleaned)
    selected = {
        "sponsor_id": next(iter(sponsors)) if len(sponsors) == 1 else "",
        "arrival_date": next(iter(dates)) if len(dates) == 1 else "",
    }
    return {
        "case_id": truth["case_id"], "actual": truth["adjudication"],
        "bucket": stable_bucket(truth["case_id"]), "selected": selected,
        "truth": {field: truth[field] for field in selected},
        "baseline": {field: baseline[field] for field in selected},
        "sponsor_conflicts": sorted(sponsors) if len(sponsors) > 1 else [],
        "date_conflicts": sorted(dates) if len(dates) > 1 else [],
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
    items = [
        (str(args.pdf_dir / f"{case_id}.pdf"), truth[case_id], row)
        for case_id, row in sorted(predictions.items())
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    for adjudication in ("NEEDS_REVIEW", "DENIED", "APPROVED"):
        print(adjudication)
        for bucket in (0, 1):
            group = [row for row in rows if row["actual"] == adjudication and row["bucket"] == bucket]
            for field, default in (("sponsor_id", "SPN-0000"), ("arrival_date", "1900-01-01")):
                changed = [row for row in group if row["baseline"][field] == default and row["selected"][field]]
                print(
                    bucket, field, "changes", len(changed),
                    "correct", sum(row["selected"][field] == row["truth"][field] for row in changed),
                    "wrong", sum(row["selected"][field] != row["truth"][field] for row in changed),
                )


if __name__ == "__main__":
    main()
