#!/usr/bin/env python3
"""Measure strict anchored fee evidence in existing whole-page OCR."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import solution


def bucket(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def scan_one(item: tuple[str, dict[str, str], dict[str, object]]) -> dict[str, object]:
    pdf_path_text, truth, baseline = item
    candidates: set[str] = set()
    for image in solution.render_pages(Path(pdf_path_text)):
        for text in solution.visible_texts(image):
            value = solution.clean_anchored_fee_status(text)
            if value:
                candidates.add(value)
    selected = next(iter(candidates)) if len(candidates) == 1 else ""
    return {
        "case_id": truth["case_id"], "actual": truth["adjudication"],
        "bucket": bucket(truth["case_id"]), "selected": selected,
        "truth": truth["fee_status"], "baseline": baseline["fee_status"],
        "candidates": sorted(candidates),
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
    for adjudication in ("APPROVED", "DENIED", "NEEDS_REVIEW"):
        for split in (0, 1):
            group = [r for r in rows if r["actual"] == adjudication and r["bucket"] == split and r["selected"] and r["selected"] != r["baseline"]]
            print(adjudication, split, "changes", len(group), "correct", sum(r["selected"] == r["truth"] for r in group), "wrong", sum(r["selected"] != r["truth"] for r in group))


if __name__ == "__main__":
    main()
