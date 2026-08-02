#!/usr/bin/env python3
"""Apply the proposed paired approval gate to frozen predictions for scoring."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import solution


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    rows = list(map(json.loads, args.predictions.open()))
    changed = 0
    with args.output.open("w", encoding="utf-8") as stream:
        for row in rows:
            text = cache[row["case_id"]]["text"]
            approval = solution.visible_ocr_approval_probability((text,))
            denial = solution.visible_ocr_denial_probability((text,))
            complete = all(
                row[field] != solution.DEFAULTS[field]
                for field in solution.FIELDS
                if field != "risk_flags"
            )
            fee_ok = row["fee_status"] == "paid" or (
                row["fee_status"] == "waived" and row["visa_class"] == "DIP-1"
            )
            sponsor_ok = row["visa_class"] == "DIP-1" or (
                re.fullmatch(r"SPN-\d{4}", row["sponsor_id"]) is not None
                and row["sponsor_id"] not in solution.REVOKED_SPONSORS
            )
            bio_none = (
                "observed flags none" in re.sub(r"[^a-z0-9]+", " ", text.casefold())
                or "risk flags none" in re.sub(r"[^a-z0-9]+", " ", text.casefold())
            )
            paired_model_gate = approval >= 0.75 and denial <= 0.30
            affirmative_biometric_gate = bio_none and approval >= 0.50 and denial <= 0.40
            if (
                row["adjudication"] == "NEEDS_REVIEW"
                and row["risk_flags"] == "none"
                and complete and fee_ok and sponsor_ok
                and row["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"}
                and (paired_model_gate or affirmative_biometric_gate)
            ):
                row["adjudication"] = "APPROVED"
                row["confidence"] = 0.93
                changed += 1
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(json.dumps({"changed": changed}))


if __name__ == "__main__":
    main()
