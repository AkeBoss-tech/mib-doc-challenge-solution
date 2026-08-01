#!/usr/bin/env python3
"""Cache incumbent visible OCR strings for candidate-trained text modeling."""

from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import solution


def scan_one(item: tuple[str, str]) -> dict[str, object]:
    pdf_path_text, actual = item
    texts: list[str] = []
    for image in solution.render_pages(Path(pdf_path_text)):
        texts.extend(solution.visible_texts(image))
    return {
        "case_id": Path(pdf_path_text).stem,
        "actual": actual,
        "text": "\n".join(texts),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    items = [(str(args.pdf_dir / f"{case_id}.pdf"), row["adjudication"]) for case_id, row in sorted(truth.items())]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(scan_one, items))
    with args.output.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    print("cases", len(rows))


if __name__ == "__main__":
    main()
