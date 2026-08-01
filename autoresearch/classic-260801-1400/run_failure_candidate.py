#!/usr/bin/env python3
"""Run the production predictor on only incumbent review outputs."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import solution


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    paths = [
        args.pdf_dir / f"{case_id}.pdf"
        for case_id, row in baseline.items()
        if row["adjudication"] == "NEEDS_REVIEW"
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor, args.output.open("w") as stream:
        for row in executor.map(solution.predict, paths):
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    print("cases", len(paths))


if __name__ == "__main__":
    main()
