#!/usr/bin/env python3
"""Export a compact applicant-name token vocabulary from public labels."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.labels.open(encoding="utf-8") as stream:
        tokens = sorted({
            token
            for row in csv.DictReader(stream)
            for token in row["applicant_name"].split()
            if token.isalpha()
        })
    payload = {
        "schema": "akeboss-public-name-token-vocabulary/v1",
        "tokens": tokens,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
