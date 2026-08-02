#!/usr/bin/env python3
"""Cross-fit the paired visible-OCR approval/denial safety gate."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold


DEFAULTS = {"unknown", "SPN-0000", "1900-01-01"}
REVOKED = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-7331"}
FIELDS = ("applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
          "arrival_date", "declared_purpose", "risk_flags", "fee_status")


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.casefold())).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    pred = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    ids = sorted(truth)
    texts = np.asarray([sanitize(cache[x]["text"]) for x in ids], dtype=object)
    actual = np.asarray([truth[x]["adjudication"] for x in ids])
    approval = np.zeros(len(ids))
    denial = np.zeros(len(ids))
    strata = np.asarray([{"APPROVED": 0, "DENIED": 1, "NEEDS_REVIEW": 2}[value] for value in actual])
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)
    fold_pairs = list(folds.split(texts, strata))
    fold_ids = np.zeros(len(ids), dtype=int)
    for fold_number, (train, test) in enumerate(fold_pairs):
        fold_ids[test] = fold_number
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                                     max_features=12000, sublinear_tf=True)
        train_matrix = vectorizer.fit_transform(texts[train])
        test_matrix = vectorizer.transform(texts[test])
        for target, destination, c_value in (("APPROVED", approval, 8.0), ("DENIED", denial, 2.0)):
            labels = actual[train] == target
            classifier = LogisticRegression(C=c_value, class_weight="balanced", max_iter=1200,
                                            solver="liblinear", random_state=1)
            classifier.fit(train_matrix, labels)
            destination[test] = classifier.predict_proba(test_matrix)[:, 1]
    policy_facts = []
    for case_id in ids:
        row = pred[case_id]
        complete = sum(str(row[field]) not in DEFAULTS for field in FIELDS)
        fee_ok = row["fee_status"] == "paid" or (row["fee_status"] == "waived" and row["visa_class"] == "DIP-1")
        sponsor_ok = row["visa_class"] == "DIP-1" or (
            re.fullmatch(r"SPN-\d{4}", str(row["sponsor_id"])) is not None and row["sponsor_id"] not in REVOKED
        )
        folded = re.sub(r"[^a-z0-9]+", " ", cache[case_id]["text"].casefold())
        policy_facts.append((
            row["adjudication"] == "NEEDS_REVIEW" and row["risk_flags"] == "none"
            and fee_ok and sponsor_ok and row["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"},
            complete,
            "observed flags none" in folded or "risk flags none" in folded,
        ))
    candidates = []
    for minimum_complete in (7, 8, 9):
        for require_bio_none in (False, True):
            policy_ok = np.asarray([
                base and complete >= minimum_complete and (not require_bio_none or bio_none)
                for base, complete, bio_none in policy_facts
            ])
            for approval_min in (0.50, 0.65, 0.75, 0.85, 0.90):
                for denial_max in (0.20, 0.30, 0.40, 0.49):
                    routed = policy_ok & (approval >= approval_min) & (denial <= denial_max)
                    counts = {value: int(np.sum(routed & (actual == value))) for value in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
                    candidates.append({"minimum_complete": minimum_complete, "require_bio_none": require_bio_none,
                                       "approval_min": approval_min, "denial_max": denial_max,
                                       "routed": int(np.sum(routed)), "counts": counts,
                                       "classification_raw_gain": 6 * counts["APPROVED"] - 7 * counts["NEEDS_REVIEW"]})
    candidates.sort(key=lambda row: (row["counts"]["DENIED"] == 0, row["classification_raw_gain"], row["counts"]["APPROVED"]), reverse=True)
    for candidate in candidates[:25]:
        print(json.dumps(candidate, separators=(",", ":")))
    branch = np.asarray([float(pred[x]["confidence"]) for x in ids])
    review_mask = np.isin(branch, (0.21, 0.27, 0.28))
    correctness = actual == "NEEDS_REVIEW"
    dynamic = np.zeros(len(ids))
    static_values = {0.21: 0.39, 0.27: 0.42, 0.28: 0.45}
    for fold_number in range(5):
        training = review_mask & (fold_ids != fold_number)
        testing = review_mask & (fold_ids == fold_number)
        for index in np.flatnonzero(testing):
            approval_bin = int(approval[index] * 5)
            denial_bin = int(denial[index] * 5)
            peers = training & (branch == branch[index]) & (approval * 5 >= approval_bin) & (approval * 5 < approval_bin + 1) & (denial * 5 >= denial_bin) & (denial * 5 < denial_bin + 1)
            if np.sum(peers) < 8:
                peers = training & (branch == branch[index])
            dynamic[index] = float(np.mean(correctness[peers]))
    static = np.asarray([static_values.get(value, 0.5) for value in branch])
    print(json.dumps({
        "review_confidence_crossfit": {
            "rows": int(np.sum(review_mask)),
            "static_brier": round(float(np.mean((static[review_mask] - correctness[review_mask]) ** 2)), 6),
            "dynamic_brier": round(float(np.mean((dynamic[review_mask] - correctness[review_mask]) ** 2)), 6),
        }
    }, separators=(",", ":")))
    learned_denials = np.asarray([
        pred[x]["adjudication"] == "DENIED" and float(pred[x]["confidence"]) == 0.87
        for x in ids
    ])
    for threshold in (0.50, 0.60, 0.65, 0.70, 0.75):
        routed = learned_denials & (approval >= threshold)
        counts = {value: int(np.sum(routed & (actual == value))) for value in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
        print(json.dumps({"learned_denial_veto_approval_min": threshold,
                          "routed": int(np.sum(routed)), "counts": counts,
                          "classification_raw_gain": 2 * counts["APPROVED"] + 7 * counts["NEEDS_REVIEW"] - 6 * counts["DENIED"]},
                         separators=(",", ":")))


if __name__ == "__main__":
    main()
