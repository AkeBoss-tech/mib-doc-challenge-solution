#!/usr/bin/env python3
"""Search generic approval gates with split safety checks over visible OCR."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import solution


DEFAULTS = {"unknown", "SPN-0000", "1900-01-01"}
FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)


def split(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    rows = []
    for case_id in sorted(truth):
        prediction = predictions[case_id]
        text = cache[case_id]["text"]
        folded = re.sub(r"[^a-z0-9]+", " ", text.casefold())
        complete = sum(str(prediction[field]) not in DEFAULTS for field in FIELDS)
        rows.append({
            "case_id": case_id,
            "split": split(case_id),
            "actual": truth[case_id]["adjudication"],
            "baseline": prediction["adjudication"],
            "clean": prediction["risk_flags"] == "none",
            "complete": complete,
            "fee_ok": prediction["fee_status"] == "paid" or (
                prediction["fee_status"] == "waived" and prediction["visa_class"] == "DIP-1"
            ),
            "valid_visa": prediction["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"},
            "valid_sponsor": prediction["visa_class"] == "DIP-1" or (
                re.fullmatch(r"SPN-\d{4}", str(prediction["sponsor_id"])) is not None
                and prediction["sponsor_id"] not in solution.REVOKED_SPONSORS
            ),
            "bio_none": "observed flags none" in folded or "risk flags none" in folded,
            "denial": solution.visible_ocr_denial_probability((text,)),
            "approval": solution.visible_ocr_approval_probability((text,)),
        })

    candidates = []
    for minimum_complete in range(4, 10):
        for denial_max in (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.49):
            for approval_min in (0.0, 0.25, 0.50, 0.65, 0.75, 0.85, 0.90):
                for require_bio_none in (False, True):
                    routed = [row for row in rows if (
                        row["baseline"] == "NEEDS_REVIEW"
                        and row["clean"] and row["complete"] >= minimum_complete
                        and row["fee_ok"] and row["valid_visa"] and row["valid_sponsor"]
                        and row["denial"] <= denial_max and row["approval"] >= approval_min
                        and (not require_bio_none or row["bio_none"])
                    )]
                    metrics = []
                    safe = True
                    for part in (0, 1):
                        group = [row for row in routed if row["split"] == part]
                        approved = sum(row["actual"] == "APPROVED" for row in group)
                        reviews = sum(row["actual"] == "NEEDS_REVIEW" for row in group)
                        denied = sum(row["actual"] == "DENIED" for row in group)
                        safe &= denied == 0
                        metrics.append({"approved": approved, "reviews": reviews, "denied": denied,
                                        "classification_raw_gain": 6 * approved - 7 * reviews})
                    if safe:
                        candidates.append((
                            min(item["classification_raw_gain"] for item in metrics),
                            sum(item["classification_raw_gain"] for item in metrics),
                            sum(item["approved"] for item in metrics),
                            -sum(item["reviews"] for item in metrics),
                            {"minimum_complete": minimum_complete, "denial_max": denial_max,
                             "approval_min": approval_min, "require_bio_none": require_bio_none,
                             "splits": metrics, "routed": len(routed)},
                        ))
    for candidate in sorted(candidates, key=lambda item: item[:4], reverse=True)[:20]:
        print(json.dumps(candidate[-1], separators=(",", ":")))


if __name__ == "__main__":
    main()
