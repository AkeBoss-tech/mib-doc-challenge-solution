#!/usr/bin/env python3
"""Measure one independent visible manual-note band without changing output."""

from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import ImageEnhance, ImageOps

import solution


def scan(path: Path) -> tuple[str, list[dict[str, object]]]:
    pages: list[dict[str, object]] = []
    for page_number, image in enumerate(solution.render_pages(path), start=1):
        crop = image.crop((0, 0, int(image.width * 0.80), int(image.height * 0.30)))
        crop = ImageEnhance.Contrast(ImageOps.grayscale(crop)).enhance(1.7)
        text, quality = solution.ocr_with_quality(crop, 11)
        pages.append({
            "page": page_number,
            "finding": solution.exact_manual_finding(text),
            "quality": quality,
            "text": " ".join(text.split())[:240],
        })
    return path.stem, pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.truth.open() as stream:
        truth = {row["case_id"]: row["adjudication"] for row in csv.DictReader(stream)}
    paths = sorted(args.input_dir.glob("*.pdf"))
    with ProcessPoolExecutor(max_workers=4) as executor:
        rows = dict(executor.map(scan, paths))
    with args.output.open("w", encoding="utf-8") as stream:
        for case_id in sorted(rows):
            stream.write(json.dumps({
                "case_id": case_id,
                "truth": truth[case_id],
                "pages": rows[case_id],
            }, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
