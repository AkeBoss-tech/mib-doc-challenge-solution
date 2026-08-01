#!/usr/bin/env python3
"""Measure exact visible manual findings without changing adjudication."""

from __future__ import annotations

import argparse
import csv
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import ImageEnhance, ImageOps

import solution


def exact_finding(text: str) -> str:
    if solution.page_kind(text) != "note":
        return ""
    value = solution.extract_label(text, "Finding")
    match = re.fullmatch(r"(APPROVED|DENIED|NEEDS[ _-]?REVIEW)", value, re.I)
    return match.group(1).upper().replace(" ", "_").replace("-", "_") if match else ""


def scan(path: Path) -> tuple[str, list[dict[str, object]]]:
    pages: list[dict[str, object]] = []
    for page_number, image in enumerate(solution.render_pages(path), start=1):
        boosted = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.5)
        readings = (solution.ocr(boosted, 6), solution.ocr(boosted, 11))
        findings = [exact_finding(text) for text in readings]
        pages.append({"page": page_number, "findings": findings})
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
            visible = [finding for page in rows[case_id] for finding in page["findings"] if finding]
            agreed_approval = any(
                page["findings"] == ["APPROVED", "APPROVED"]
                for page in rows[case_id]
            ) and set(visible) == {"APPROVED"}
            stream.write(json.dumps({
                "case_id": case_id,
                "truth": truth[case_id],
                "agreed_approval": agreed_approval,
                "pages": rows[case_id],
            }, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
