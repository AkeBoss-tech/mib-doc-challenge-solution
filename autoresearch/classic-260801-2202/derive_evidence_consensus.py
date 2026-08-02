#!/usr/bin/env python3
"""Apply measured name/sponsor consensus selections to frozen predictions."""

from __future__ import annotations

import argparse
import json
from difflib import SequenceMatcher
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import solution


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--consensus", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = {row["case_id"]: row["selected"] for row in map(json.loads, args.consensus.open())}
    cache = {row["case_id"]: row["text"] for row in map(json.loads, args.ocr_cache.open())}
    changes = {"applicant_name": 0, "species_code": 0, "sponsor_id": 0}
    with args.output.open("w", encoding="utf-8") as stream:
        for row in map(json.loads, args.predictions.open()):
            for field in changes:
                candidate = selected[row["case_id"]][field]
                valid = field != "species_code" or candidate in solution.CATEGORY_VOCABULARY["species_code"]
                if candidate and candidate != row[field] and valid:
                    row[field] = candidate
                    changes[field] += 1
            cleaned = solution.clean_name(row["applicant_name"])
            if cleaned and cleaned != row["applicant_name"]:
                row["applicant_name"] = cleaned
                changes["applicant_name"] += 1
            attested = solution.sponsor_attested_applicant(cache[row["case_id"]])
            if attested:
                agreement = SequenceMatcher(None, row["applicant_name"].casefold(), attested.casefold()).ratio()
                if row["applicant_name"] == "unknown" or agreement >= 0.60:
                    if row["applicant_name"] != attested:
                        row["applicant_name"] = attested
                        changes["applicant_name"] += 1
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(json.dumps(changes, separators=(",", ":")))


if __name__ == "__main__":
    main()
