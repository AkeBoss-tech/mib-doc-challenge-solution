#!/usr/bin/env python3
"""Measure one generic visible top-band fee reader without changing predictions."""

from __future__ import annotations

import argparse
import csv
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import ImageEnhance, ImageOps

import solution


def scan(task: tuple[Path, float, float, float, float, float, int, int]) -> tuple[str, list[dict[str, object]]]:
    path, left_ratio, top_ratio, width_ratio, height_ratio, resize_scale, psm, rotation = task
    pages: list[dict[str, object]] = []
    for page_number, image in enumerate(solution.render_pages(path), start=1):
        if rotation:
            image = image.rotate(rotation, expand=True, fillcolor="white")
        band = image.crop((
            int(image.width * left_ratio),
            int(image.height * top_ratio),
            int(image.width * min(1.0, left_ratio + width_ratio)),
            int(image.height * min(1.0, top_ratio + height_ratio)),
        ))
        band = ImageEnhance.Contrast(ImageOps.grayscale(band)).enhance(1.7)
        if resize_scale != 1.0:
            band = band.resize(
                (int(band.width * resize_scale), int(band.height * resize_scale)),
                solution.Image.Resampling.LANCZOS,
            )
        text, quality = solution.ocr_with_quality(band, psm)
        values = re.findall(r"\b(paid|unpaid|waived)\b", text, re.I)
        pages.append({
            "page": page_number,
            "values": [value.lower() for value in values],
            "quality": quality,
            "text": " ".join(text.split())[:300],
        })
    return path.stem, pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--left-ratio", type=float, default=0.0)
    parser.add_argument("--top-ratio", type=float, default=0.0)
    parser.add_argument("--width-ratio", type=float, default=1.0)
    parser.add_argument("--height-ratio", type=float, default=0.34)
    parser.add_argument("--resize-scale", type=float, default=1.0)
    parser.add_argument("--psm", type=int, default=6)
    parser.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), default=0)
    args = parser.parse_args()
    with args.truth.open() as stream:
        truth = {row["case_id"]: row["fee_status"] for row in csv.DictReader(stream)}
    paths = sorted(args.input_dir.glob("*.pdf"))
    tasks = [
        (
            path, args.left_ratio, args.top_ratio, args.width_ratio,
            args.height_ratio, args.resize_scale, args.psm, args.rotation,
        )
        for path in paths
    ]
    with ProcessPoolExecutor(max_workers=4) as executor:
        rows = dict(executor.map(scan, tasks))
    with args.output.open("w", encoding="utf-8") as stream:
        for case_id in sorted(rows):
            stream.write(json.dumps({
                "case_id": case_id,
                "truth": truth[case_id],
                "pages": rows[case_id],
            }, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
