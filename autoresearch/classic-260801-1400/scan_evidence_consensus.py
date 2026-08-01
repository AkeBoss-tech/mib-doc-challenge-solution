#!/usr/bin/env python3
"""Measure cross-source consensus over existing visible parsed evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import solution


def bucket(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def consensus(options: list[solution.Evidence]) -> str:
    by_value: dict[str, list[solution.Evidence]] = defaultdict(list)
    for item in options:
        by_value[item.value].append(item)
    ranked = sorted(
        (
            (len({item.source for item in items}), len(items), max(item.confidence for item in items), value)
            for value, items in by_value.items()
        ),
        reverse=True,
    )
    if not ranked:
        return ""
    sources, reads, _, value = ranked[0]
    if sources < 2 and reads < 3:
        return ""
    if len(ranked) > 1 and ranked[1][:2] >= ranked[0][:2]:
        return ""
    return value


def scan_one(item: tuple[str, dict[str, str], dict[str, object]]) -> dict[str, object]:
    pdf_path_text, truth, baseline = item
    evidence: dict[str, list[solution.Evidence]] = defaultdict(list)
    for image in solution.render_pages(Path(pdf_path_text)):
        for text in solution.visible_texts(image):
            solution.parse_page(solution.page_kind(text), text, evidence)
    fields = solution.FIELDS
    selected = {field: consensus(evidence[field]) for field in fields}
    return {
        "case_id": truth["case_id"], "actual": truth["adjudication"],
        "bucket": bucket(truth["case_id"]), "selected": selected,
        "truth": {field: truth[field] for field in fields},
        "baseline": {field: baseline[field] for field in fields},
        "candidate_counts": {field: len(set(item.value for item in evidence[field])) for field in fields},
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
    for field in solution.FIELDS:
        print(field)
        for split in (0, 1):
            group = [r for r in rows if r["bucket"] == split and r["selected"][field] and r["selected"][field] != r["baseline"][field]]
            print(split, "changes", len(group), "correct", sum(r["selected"][field] == r["truth"][field] for r in group), "wrong", sum(r["selected"][field] != r["truth"][field] for r in group), "baseline_correct", sum(r["baseline"][field] == r["truth"][field] for r in group))


if __name__ == "__main__":
    main()
